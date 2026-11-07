#!/usr/bin/env python3
"""
Sort material you drop into _Inbox: any folders, files, zips, Drive or web links. Two steps, like a pull request:

    python3 tools/sort_inbox.py plan "_Inbox/Mechanical Engineering, ZHCET" --credit "Farhan Baig"
        unpacks, converts, de-duplicates and asks Claude where each file goes. Nothing in the archive changes.
        Writes .sej-cache/sort/<name>/report.md (read this) and plan.json (edit it if you disagree).

    python3 tools/sort_inbox.py apply <name>
        re-checks every decision against the rules and carries the plan out: files are copied into the archive
        (converted, hidden metadata stripped), credits recorded, one catalog change set written, and the inbox
        inbox originals whose content is now in the archive are removed (recorded with sha256 in originals.json);
        rejected or for-review files move to .sej-cache/inbox-merged-originals/<name>/. No AI calls.
        --keep-originals keeps every original as before.

Options for plan:
    --credit NAME          who shared it (shown as "shared by NAME"); several: "A, B"
    --note TEXT            what you know about the material (course, year, source…); Claude reads it
    --permission TEXT      the material is cleared by this rights holder (e.g. "Engineering College Library");
                           books are then placed instead of rejected, and the catalog records the permission
    --link URL             a link to include (repeatable); Drive folders/files are downloaded when DRIVE_API_KEY is set
    --limit N              only the first N files (to try it out)

AI provider: catalog/llm-config.json or SEJ_PROVIDER + SEJ_API_KEY + SEJ_MODEL (see tools/sorter/llm.py).
"""
import argparse
import json
import os
import re
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import rules  # noqa: E402
import scrub  # noqa: E402
import sej  # noqa: E402
from sorter import agent, courses, decide, dedup, ingest, pipeline, report, stages, util  # noqa: E402

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


def usage_line(call):
    u = call.usage
    cached = u.get("cache_read", 0)
    if call.__class__.__name__ == "AnthropicClient":   # Anthropic counts cache reads apart from input
        total = u["input"] + cached
    else:                                               # OpenAI-style providers include them in prompt tokens
        total = u["input"]
    fresh = max(0, total - cached)
    pct = f" ({100 * cached / total:.0f}% from cache)" if total else ""
    return (f"AI ({getattr(call, 'model', '?')}): {u['calls']} calls · input {total:,} tokens = {fresh:,} new + "
            f"{cached:,} cached{pct} · output {u['output']:,}")


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
    from sorter import llm
    call = llm.client()
    print(f"Sorting {len(items)} inputs as '{slug}'")
    notes = pipeline.sort(items, archive=archive, rules=rules, workdir=work, upload_text=note.strip(), call=call,
                          conventions=load_conventions(), permission=a.permission, log=print)
    plan = {"slug": slug, "inputs": [os.path.abspath(p) for p in a.paths], "credit": a.credit, "note": a.note,
            "permission": a.permission, "notes": notes, "usage": call.usage, "items": items}
    stages.save_json(os.path.join(work, "plan.json"), plan, REPO)
    md = report.markdown(items, f"{slug}: sorting plan", extra=[f"Credit: **{a.credit or '—'}**"] +
                         ([f"Permission: {a.permission}"] if a.permission else []) +
                         [usage_line(call)])
    with open(os.path.join(work, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    c = report.counts(items)
    print("  " + ", ".join(f"{v} {k}" for k, v in c.most_common()))
    print(f"Read {os.path.relpath(os.path.join(work, 'report.md'), REPO)}; then: python3 tools/sort_inbox.py apply {slug}")


def cmd_prepare(a):
    """Stage 1, where the files are: everything before the AI. Budgeted; run again to continue."""
    slug = a.name or slug_of(a.paths)
    work = os.path.join(WORK, slug)
    os.makedirs(work, exist_ok=True)
    ip = os.path.join(work, "items.json")
    if os.path.exists(ip):
        meta = stages.load_json(ip, REPO)
        items = meta["items"]
    else:
        items = []
        for p in a.paths:
            if os.path.isdir(p):
                ingest.from_folder(p, items)
            elif os.path.isfile(p):
                ingest.new_item(items, os.path.basename(p), p)
            else:
                sys.exit(f"not found: {p}")
        meta = {"slug": slug, "inputs": [os.path.abspath(p) for p in a.paths], "credit": a.credit, "note": a.note,
                "permission": a.permission, "items": items}
        print(f"{slug}: {len(items)} files collected")
    done = stages.prepare(items, work, rules, a.budget)
    meta["prepared"] = done
    stages.save_json(ip, meta, REPO)
    left = sum(1 for it in items if not it.get("prep_inspected") and not it.get("outcome"))
    c = report.counts(items)
    print(f"{slug}: {'PREPARED' if done else 'in progress'} · {len(items)} items · {left} still to inspect · "
          + ", ".join(f"{v} {k}" for k, v in c.most_common()))


def cmd_decide(a):
    """Stage 2, anywhere: AI decisions from the prepared evidence. Resumable; writes plan.json and report.md."""
    work = os.path.join(WORK, a.name)
    meta = stages.load_json(os.path.join(work, "items.json"), REPO)
    if not meta.get("prepared"):
        sys.exit("not prepared yet: run prepare until it says PREPARED")
    items = meta["items"]
    state = sej.load_state()
    archive = courses.Archive(REPO, state, sej, rules, local_root=REPO)
    for it in items:   # a spreadsheet tab with no cells (a chart sheet) has nothing to publish
        if not it.get("outcome") and it.get("ext") == "csv" and len(re.sub(r'[\s,;"]', "", (it.get("info") or {}).get("text", ""))) < 5:
            it["outcome"] = {"action": "ignored", "reason": "empty sheet (no data)", "auto": True}
    dedup.exact(items, archive)
    dedup.batch_lookalikes(items)
    note = meta.get("note") or ""
    if meta.get("credit"):
        note += f"\n(Credit for this upload: {meta['credit']})"
    if meta.get("permission"):
        note += f"\n(The maintainer has permission from {meta['permission']} to publish this material, books included.)"
    from sorter import llm
    call = llm.client()
    ctx = decide.Context(archive, rules, items, meta.get("permission"))
    ck_path = os.path.join(work, "checkpoint.json")
    notes = []
    if os.path.exists(ck_path):
        with open(ck_path, encoding="utf-8") as fh:
            ck = json.load(fh)
        notes = ck.get("notes", [])
        print(f"  resumed {stages.ck_load(items, ck, ctx)} decisions from an earlier run")

    def save(_):
        tmp = ck_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(stages.ck_dump(items, notes), fh, ensure_ascii=False)
        os.replace(tmp, ck_path)

    todo = [it for it in items if not it.get("outcome")]
    print(f"{a.name}: {len(todo)} files for the AI ({len(items) - len(todo)} settled already)")
    if todo:
        agent.run(items, archive, ctx, call, load_conventions(), note.strip(), print, on_batch=save, notes=notes,
                  jobs=a.jobs, work=work)
    open_left = [it for it in items if not it.get("outcome")]
    if open_left:
        save(None)
        sys.exit(f"{len(open_left)} files still undecided (a batch failed); run decide again to continue")
    decide.resolve_twins(items)
    plan = {**{k: meta.get(k) for k in ("slug", "inputs", "credit", "note", "permission")}, "notes": notes,
            "usage": call.usage, "items": items, "staged": True}
    stages.save_json(os.path.join(work, "plan.json"), plan, REPO)
    md = report.markdown(items, f"{a.name}: sorting plan", extra=[f"Credit: **{meta.get('credit') or '—'}**"] + [usage_line(call)])
    with open(os.path.join(work, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    c = report.counts(items)
    print("  " + ", ".join(f"{v} {k}" for k, v in c.most_common()))
    print(f"Read {os.path.relpath(os.path.join(work, 'report.md'), REPO)}; then: python3 tools/sort_inbox.py apply {a.name}")


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


SETTLED = ("place", "duplicate", "credit", "twin", "ignored", "unpacked", "link")


def settle_originals(items, plan, work, keep_all=False):
    """After a successful apply: an inbox file whose content is now in the archive (placed, already there, a copy of a
    kept file, or system junk) is removed, and recorded in originals.json (name, size, sha256, where it went). Files
    that were rejected, need review, or weren't settled are moved to inbox-merged-originals/<slug>/ for a person.
    If removing isn't allowed on this machine, everything is moved instead (the old behaviour)."""
    inbox = os.path.join(REPO, "_Inbox")
    kids = {}
    for it in items:
        if it.get("parent"):
            kids.setdefault(it["parent"], []).append(it)

    def settled(it):
        if (it.get("outcome") or {}).get("action") not in SETTLED:
            return False
        return all(settled(k) for k in kids.get(it["id"], []))

    record, removed, parked = [], 0, 0
    for it in items:
        src = it.get("orig_path")
        if it.get("parent") or not src or not os.path.isfile(src):
            continue
        if os.path.commonpath([os.path.abspath(src), inbox]) != inbox:
            continue
        o = it.get("outcome") or {}
        rel = os.path.relpath(src, inbox)
        record.append({"src": rel, "size": it.get("size"), "sha256": it.get("orig_sha"), "action": o.get("action"),
                       "to": o.get("path") or o.get("target") or o.get("of")})
        if settled(it) and not keep_all:
            try:
                os.remove(src)
                removed += 1
                continue
            except OSError:
                keep_all = True   # deleting isn't permitted here: keep everything from now on
        dest = os.path.join(KEEP, plan["slug"], rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(src, dest)
        parked += 1
    with open(os.path.join(work, "originals.json"), "w", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=1)
    for src in plan["inputs"]:        # drop the now-empty inbox folders
        if os.path.isdir(src):
            for dp, dns, fns in os.walk(src, topdown=False):
                for f in fns:
                    if f in (".DS_Store", "Thumbs.db", "desktop.ini"):
                        try:
                            os.remove(os.path.join(dp, f))
                        except OSError:
                            pass
                try:
                    os.rmdir(dp)
                except OSError:
                    pass
    return removed, parked


def clear_workdir(work):
    """Converted and unpacked working copies are only needed until apply; keep the plan, report and records."""
    keep = {"plan.json", "report.md", "manifest.json", "originals.json", "checkpoint.json", "applied.json"}
    for name in os.listdir(work):
        p = os.path.join(work, name)
        if name in keep:
            continue
        try:
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        except OSError:
            pass


def cmd_apply(a):
    work = os.path.join(WORK, a.slug)
    plan = stages.load_json(os.path.join(work, "plan.json"), REPO)
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
    # copy into the archive one file at a time; progress survives an interruption (re-run apply to continue)
    prog_path = os.path.join(work, "apply-progress.json")
    finals = {}
    if os.path.exists(prog_path):
        with open(prog_path, encoding="utf-8") as fh:
            finals = json.load(fh)
    placed = [it for it in items if (it.get("outcome") or {}).get("action") == "place"]
    if a.dry_run:
        print(f"{len(placed)} files would be placed (dry run)")
        return
    t0 = time.time()
    cache = sej.HashCache()
    for it in placed:
        if it["id"] in finals:
            continue
        if a.budget and time.time() - t0 > a.budget:
            break
        o = it["outcome"]
        if not os.path.exists(it["path"]):
            sys.exit(f"working file missing for {it['src']}; run prepare again")
        dst = os.path.join(REPO, o["path"])
        if os.path.exists(dst):
            sys.exit(f"refusing to overwrite {o['path']}")
        f = pipeline.finalize(it, work, scrub)
        if not f:
            finals[it["id"]] = {"failed": True}
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(f["path"], dst)
        cache.get(o["path"], dst)
        os.remove(f["path"])
        finals[it["id"]] = {k: f[k] for k in ("size", "sha")}
        with open(prog_path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(finals, fh)
        os.replace(prog_path + ".tmp", prog_path)
    cache.save()
    left = [it for it in placed if it["id"] not in finals]
    if left:
        print(f"copied {len(placed) - len(left)}/{len(placed)} files into the archive; run apply again to continue")
        return
    finals = {k: v for k, v in finals.items() if not v.get("failed")}
    ops = pipeline.ops_for(items, state, finals, plan.get("credit"), plan.get("permission"))
    source = {"kind": "local", "by": "maintainer", "sorted_by": "sorter"}
    for k in ("credit", "note", "permission"):
        if plan.get(k):
            source[k] = plan[k]
    path = sej.write_change(ops, source, re.sub(r"[^a-z0-9-]", "", plan["slug"].lower())[:40] or "sort")
    report.dump(items, os.path.join(work, "manifest.json"))
    removed, parked = settle_originals(items, plan, work, keep_all=a.keep_originals)
    clear_workdir(work)
    with open(os.path.join(work, "applied.json"), "w") as fh:
        json.dump({"change": os.path.basename(path), "ops": len(ops)}, fh)
    c = report.counts(items)
    print(f"Applied: {c.get('place', 0)} placed, {c.get('credit', 0) + c.get('duplicate', 0)} credited to existing copies, "
          f"{c.get('link', 0)} links → {os.path.relpath(path, REPO)}")
    print(f"Inbox originals: {removed} removed (their content is in the archive; list in originals.json), "
          f"{parked} kept in {os.path.relpath(os.path.join(KEEP, plan['slug']), REPO)} (rejected, for review, or not settled)")
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
    s = sub.add_parser("prepare", help="stage 1 (where the files are): collect, convert, inspect; budgeted")
    s.add_argument("paths", nargs="+")
    s.add_argument("--name")
    s.add_argument("--credit")
    s.add_argument("--note")
    s.add_argument("--permission")
    s.add_argument("--budget", type=float, default=150, help="stop after N seconds (run again to continue); 0 = no limit")
    s = sub.add_parser("decide", help="stage 2 (anywhere): AI decisions from the prepared evidence")
    s.add_argument("name")
    s.add_argument("--jobs", type=int, default=4, help="batches in parallel")
    s = sub.add_parser("apply")
    s.add_argument("slug")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--keep-originals", action="store_true", help="move every inbox original aside instead of removing settled ones")
    s.add_argument("--budget", type=float, default=0, help="stop copying after N seconds (run again to continue)")
    a = ap.parse_args()
    if a.cmd == "plan":
        if not a.paths and not a.link:
            ap.error("give a folder, file or --link")
        cmd_plan(a)
    elif a.cmd == "prepare":
        cmd_prepare(a)
    elif a.cmd == "decide":
        cmd_decide(a)
    else:
        cmd_apply(a)


if __name__ == "__main__":
    main()
