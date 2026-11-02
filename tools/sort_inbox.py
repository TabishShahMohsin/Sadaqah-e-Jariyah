#!/usr/bin/env python3
"""
Sort material you drop into _Inbox: any folders, files, zips, Drive or web links. Two steps, like a pull request:

    python3 tools/sort_inbox.py plan "_Inbox/Mechanical Engineering, ZHCET" --credit "Farhan Baig"
        unpacks, converts, de-duplicates and asks Claude where each file goes. Nothing in the archive changes.
        Writes .sej-cache/sort/<name>/report.md (read this) and plan.json (edit it if you disagree).

    python3 tools/sort_inbox.py apply <name>
        re-checks every decision against the rules and carries the plan out: files are copied into the archive
        (converted, hidden metadata stripped), credits recorded, one catalog change set written, and the inbox
        originals moved to .sej-cache/inbox-merged-originals/<name>/ (nothing is deleted). No AI calls.

Options for plan:
    --credit NAME          who shared it (shown as "shared by NAME"); several: "A, B"
    --note TEXT            what you know about the material (course, year, source…); Claude reads it
    --permission TEXT      the material is cleared by this rights holder (e.g. "Engineering College Library");
                           books are then placed instead of rejected, and the catalog records the permission
    --link URL             a link to include (repeatable); Drive folders/files are downloaded when DRIVE_API_KEY is set
    --limit N              only the first N files (to try it out)

Needs ANTHROPIC_API_KEY. Model: SEJ_MODEL (default claude-sonnet-5-5).
"""
import argparse
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import rules  # noqa: E402
import scrub  # noqa: E402
import sej  # noqa: E402
from sorter import agent, courses, decide, ingest, pipeline, report, util  # noqa: E402

WORK = os.path.join(REPO, ".sej-cache", "sort")
KEEP = os.path.join(REPO, ".sej-cache", "inbox-merged-originals")


def slug_of(paths):
    s = re.sub(r"[^A-Za-z0-9]+", "-", os.path.basename(os.path.abspath(paths[0]).rstrip("/")) or "upload").strip("-")[:50]
    return s or "upload"


def fetch_title(url):
    import requests
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0 (sej-sorter)"}, stream=True)
        head = r.raw.read(200000, decode_content=True).decode("utf-8", "ignore")
        m = re.search(r"<title[^>]*>(.*?)</title>", head, re.I | re.S)
        return util.clean_text(re.sub(r"\s+", " ", m.group(1)) if m else "", 150)
    except Exception:  # noqa: BLE001 — a title is a nicety
        return ""


def add_links(items, links, workdir, log):
    import triage  # Drive helpers (official API, public links only)
    key = os.environ.get("DRIVE_API_KEY")
    for url in links:
        if triage.drive_id(url) and key:
            problems = []
            for df in triage.drive_list(url, key, problems):
                try:
                    path, name = triage.drive_download(df, key, workdir)
                    ingest.new_item(items, f"drive/{df.get('folder') or ''}/{name}".replace("//", "/"), path)
                except Exception as e:  # noqa: BLE001
                    it = ingest.new_item(items, f"drive/{df.get('name', '?')}", None)
                    it["outcome"] = {"action": "review", "reason": f"couldn't download from Drive ({type(e).__name__})"}
            for p in problems:
                log("  " + p)
        else:
            it = ingest.new_item(items, url, None, url=url)
            it["ext"], it["page_title"] = "url", fetch_title(url)
            if triage.drive_id(url):
                it["page_title"] += " (Google Drive link; set DRIVE_API_KEY to download it instead)"


def load_conventions():
    with open(os.path.join(REPO, "CONVENTIONS.md"), encoding="utf-8") as fh:
        return fh.read()


def cmd_plan(a):
    slug = a.name or slug_of(a.paths or ["links"])
    work = os.path.join(WORK, slug)
    if os.path.exists(os.path.join(work, "applied.json")):
        sys.exit(f"{slug} was already applied; pick another --name")
    os.makedirs(work, exist_ok=True)
    items = []
    for p in a.paths:
        if os.path.isdir(p):
            ingest.from_folder(p, items)
        elif os.path.isfile(p):
            ingest.new_item(items, os.path.basename(p), p)
        else:
            sys.exit(f"not found: {p}")
    if a.limit:
        items = items[:a.limit]
    add_links(items, a.link or [], work, print)
    state = sej.load_state()
    archive = courses.Archive(REPO, state, sej, rules, local_root=REPO)
    note = a.note or ""
    if a.credit:
        note += f"\n(Credit for this upload: {a.credit})"
    if a.permission:
        note += f"\n(The maintainer has permission from {a.permission} to publish this material, books included.)"
    call = agent.ApiClient()
    print(f"Sorting {len(items)} inputs as '{slug}'")
    notes = pipeline.sort(items, archive=archive, rules=rules, workdir=work, upload_text=note.strip(), call=call,
                          conventions=load_conventions(), permission=a.permission, log=print)
    plan = {"slug": slug, "inputs": [os.path.abspath(p) for p in a.paths], "credit": a.credit, "note": a.note,
            "permission": a.permission, "notes": notes, "usage": call.usage, "items": items}
    with open(os.path.join(work, "plan.json"), "w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False, indent=1)
    md = report.markdown(items, f"{slug}: sorting plan", extra=[f"Credit: **{a.credit or '—'}**"] +
                         ([f"Permission: {a.permission}"] if a.permission else []) +
                         [f"Claude: {call.usage['calls']} calls, {call.usage['input'] + call.usage['cache_read']:,} input / "
                          f"{call.usage['output']:,} output tokens"])
    with open(os.path.join(work, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    c = report.counts(items)
    print("  " + ", ".join(f"{v} {k}" for k, v in c.most_common()))
    print(f"Read {os.path.relpath(os.path.join(work, 'report.md'), REPO)}; then: python3 tools/sort_inbox.py apply {slug}")


def recheck(items, state, archive, permission):
    """Plans can be edited by hand: every placement is checked again before anything moves."""
    ctx = decide.Context(archive, rules, items, permission)
    bad = []
    for it in items:
        o = it.get("outcome") or {}
        if o.get("action") == "place":
            folder, _, name = o["path"].rpartition("/")
            err = rules.check_destination(folder, name, o.get("convert_to") or it["ext"], archive.known) or rules.check_format(folder, name)
            if err:
                bad.append(f"{it['src']} → {o['path']}: {err}")
            elif o["path"] in state:
                o["path"] = rules.unique_path(o["path"], ctx.taken)
            ctx.taken.add(o["path"])
        elif o.get("action") in ("credit", "duplicate") and o.get("target") not in state:
            bad.append(f"{it['src']}: credit target {o.get('target')} isn't in the catalog")
    return bad


def cmd_apply(a):
    work = os.path.join(WORK, a.slug)
    with open(os.path.join(work, "plan.json"), encoding="utf-8") as fh:
        plan = json.load(fh)
    if os.path.exists(os.path.join(work, "applied.json")):
        sys.exit("already applied")
    items = plan["items"]
    ok, missing = report.coverage_ok(items)
    if not ok:
        sys.exit(f"plan has files without an outcome: {missing[:5]}")
    state = sej.load_state()
    archive = courses.Archive(REPO, state, sej, rules, local_root=REPO)
    bad = recheck(items, state, archive, plan.get("permission"))
    if bad:
        sys.exit("NOT APPLIED — fix these in plan.json:\n  " + "\n  ".join(bad))
    finals = {}
    for it in items:
        if (it.get("outcome") or {}).get("action") == "place":
            if not os.path.exists(it["path"]):
                sys.exit(f"working file missing for {it['src']}; run plan again")
            f = pipeline.finalize(it, work, scrub)
            if f:
                finals[it["id"]] = f
    ops = pipeline.ops_for(items, state, finals, plan.get("credit"), plan.get("permission"))
    if a.dry_run:
        print(json.dumps(ops[:20], indent=1, ensure_ascii=False))
        print(f"{len(ops)} ops (dry run)")
        return
    cache = sej.HashCache()
    for it in items:
        o = it.get("outcome") or {}
        if o.get("action") == "place" and it["id"] in finals:
            dst = os.path.join(REPO, o["path"])
            if os.path.exists(dst):
                sys.exit(f"refusing to overwrite {o['path']}")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(finals[it["id"]]["path"], dst)
            cache.get(o["path"], dst)
    cache.save()
    source = {"kind": "local", "by": "maintainer", "sorted_by": "sorter"}
    for k in ("credit", "note", "permission"):
        if plan.get(k):
            source[k] = plan[k]
    path = sej.write_change(ops, source, re.sub(r"[^a-z0-9-]", "", plan["slug"].lower())[:40] or "sort")
    report.dump(items, os.path.join(work, "manifest.json"))
    # originals out of the inbox (kept, never deleted)
    moved = 0
    for src in plan["inputs"]:
        if os.path.exists(src) and os.path.commonpath([os.path.abspath(src), os.path.join(REPO, "_Inbox")]) == os.path.join(REPO, "_Inbox"):
            dest = os.path.join(KEEP, plan["slug"], os.path.basename(src.rstrip("/")))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(src, dest)
            moved += 1
    with open(os.path.join(work, "applied.json"), "w") as fh:
        json.dump({"change": os.path.basename(path), "ops": len(ops)}, fh)
    c = report.counts(items)
    print(f"Applied: {c.get('place', 0)} placed, {c.get('credit', 0) + c.get('duplicate', 0)} credited to existing copies, "
          f"{c.get('link', 0)} links → {os.path.relpath(path, REPO)}")
    print(f"Inbox originals moved to {os.path.relpath(os.path.join(KEEP, plan['slug']), REPO)} ({moved} inputs)")
    print("Next: python3 site/build_index.py, then check the site.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("paths", nargs="*")
    p.add_argument("--credit")
    p.add_argument("--note")
    p.add_argument("--permission")
    p.add_argument("--link", action="append")
    p.add_argument("--name")
    p.add_argument("--limit", type=int)
    s = sub.add_parser("apply")
    s.add_argument("slug")
    s.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "plan":
        if not a.paths and not a.link:
            ap.error("give a folder, file or --link")
        cmd_plan(a)
    else:
        cmd_apply(a)


if __name__ == "__main__":
    main()
