#!/usr/bin/env python3
"""
Contributions from the site's Contribute page (worker/, at intake.sej.org.in) → the sorting pipeline.

    python3 tools/intake.py list [--status received]   what came in (default: everything not abandoned)
    python3 tools/intake.py pull <id>                   download its files to _Inbox/Contrib-<id>/, mark it 'sorting',
                                                        and print the sort_inbox.py prepare command to run next
    python3 tools/intake.py status <id> <status> [--note "…"]
                                                        in_review | needs_info | rejected | … (the contributor sees it)
    python3 tools/intake.py done <id> <change-set.json> [--note "…"]
                                                        mark published, with the archive paths from that change set
    python3 tools/intake.py purge <id>                  delete its files from the private inbox (after done/rejected)
    (done also lists a named contributor on the Sources & credits page; `credit <id> <change-set>` does just that)

Reads catalog/intake-config.json: {"api": "https://intake.sej.org.in", "admin_token": "…"} (written by
worker/setup.sh; never commit it).
"""
import argparse
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CONFIG = os.path.join(REPO, "catalog", "intake-config.json")


def cfg():
    try:
        c = json.load(open(CONFIG))
        return c["api"].rstrip("/"), c["admin_token"]
    except (OSError, KeyError, ValueError):
        sys.exit(f"Set up {os.path.relpath(CONFIG, REPO)} first (worker/setup.sh writes it).")


def call(path, method="GET", body=None, raw=False):
    api, token = cfg()
    req = urllib.request.Request(api + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                                          "User-Agent": "sej-intake-cli"})
    res = urllib.request.urlopen(req, timeout=600)
    return res if raw else json.load(res)


def safe_rel(name):
    """A contributor's file name, possibly with the folder path inside an uploaded folder, as a safe relative path."""
    parts = [re.sub(r'[\x00-\x1f<>:"|?*\\]', "_", p).strip(" .") for p in name.replace("\\", "/").split("/")]
    parts = [p[:120] for p in parts if p and p not in (".", "..")]
    return os.path.join(*parts) if parts else "file"


def cmd_list(a):
    rows = call("/api/admin/submissions" + (f"?status={a.status}" if a.status else ""))["submissions"]
    for s in rows:
        from datetime import datetime
        when = datetime.fromtimestamp(s["created"] / 1000).strftime("%Y-%m-%d %H:%M")
        print(f"{s['id']}  {s['status']:10} {when}  {s['total_bytes'] / 1e6:8.1f} MB  "
              f"{(s.get('credit') or 'anonymous')[:24]:24} {(s.get('course_hint') or '')[:50]}")
    if not rows:
        print("nothing")


def cmd_pull(a):
    s = call(f"/api/admin/submissions/{a.id}")
    out = os.path.join(REPO, "_Inbox", f"Contrib-{a.id}")
    os.makedirs(out, exist_ok=True)
    got = 0
    for f in s["files"]:
        if not f["done"]:
            print(f"  skipped (not fully uploaded): {f['name']}")
            continue
        dest = os.path.join(out, safe_rel(f["name"]))
        if os.path.exists(dest) and os.path.getsize(dest) == f["size"]:
            got += 1
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with call(f"/api/admin/submissions/{a.id}/files/{f['idx']}", raw=True) as r, open(dest + ".part", "wb") as fh:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                fh.write(b)
        os.replace(dest + ".part", dest)
        got += 1
        print(f"  {f['name']}  ({f['size'] / 1e6:.1f} MB)")
    note = [f"Contribution {a.id} from the site's Contribute page."]
    if s.get("course_hint"):
        note.append(f"Course they picked: {s['course_hint']}")
    if s.get("context"):
        note.append(f"Their description: {s['context']}")
    with open(out + ".json", "w") as fh:  # the record, next to the folder (not inside it, where the sorter would read it)
        json.dump({k: s.get(k) for k in ("id", "created", "credit", "course_hint", "context", "links")}, fh, indent=1, ensure_ascii=False)
    call(f"/api/admin/submissions/{a.id}", "POST", {"status": "sorting"})
    print(f"{got}/{len(s['files'])} files in {os.path.relpath(out, REPO)}/; marked 'sorting'")
    for l in s.get("links") or []:
        print(f"  link: {l}")
    credit = s.get("credit") or "anonymous contributor"
    print("\nNext:\n  python3 tools/sort_inbox.py prepare " + json.dumps(os.path.relpath(out, REPO))
          + f" --name contrib-{a.id} --credit {json.dumps(credit)} --note {json.dumps(' '.join(note)[:1500])}")


def cmd_status(a):
    body = {"status": a.status}
    if a.note:
        body["note"] = a.note
    call(f"/api/admin/submissions/{a.id}", "POST", body)
    print(f"{a.id}: {a.status}")


def cmd_done(a):
    ch = json.load(open(a.change))
    paths = [o.get("p") or o.get("to") for o in ch.get("ops", []) if o.get("op") in ("add", "link", "move")]
    body = {"status": "published", "published": paths[:200]}
    body["note"] = a.note or f"{len(paths)} file{'s' if len(paths) != 1 else ''} added to the archive. Thank you!"
    call(f"/api/admin/submissions/{a.id}", "POST", body)
    print(f"{a.id}: published, {len(paths)} paths")
    credit_contributor(a.id, paths)


def credit_contributor(sid, paths):
    """List a named contributor on the Sources & credits page (catalog/sources.json), one entry per person."""
    s = call(f"/api/admin/submissions/{sid}")
    who = (s.get("credit") or "").strip()
    if not who or not paths:
        return
    courses = []
    for p in paths:
        m = re.search(r"/([A-Z]{2,4}\d[0-9A-Z]\d{1,2}[A-Z]?(?:-[A-Z]{2,4}\d[0-9A-Z]\d{1,2}[A-Z]?)*)_([^/]+)/", p)
        if m:
            c = f"{m.group(2).replace('-', ' ')} ({m.group(1).split('-')[0]})"
            if c not in courses:
                courses.append(c)
    f = os.path.join(REPO, "catalog", "sources.json")
    data = json.load(open(f, encoding="utf-8"))
    title = f"{who} · contributed on the site"
    e = next((x for x in data["sources"] if x.get("title") == title), None)
    if e is None:
        e = {"title": title, "url": "", "covers": "", "status": "merged", "credit": who, "submissions": []}
        data["sources"].append(e)
    if sid not in e.setdefault("submissions", []):
        e["submissions"].append(sid)
    have = [c for c in e.get("covers", "").split("; ") if c]
    e["covers"] = "; ".join(have + [c for c in courses if c not in have])
    json.dump(data, open(f, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"Sources & credits: {title} ({e['covers']})")


def cmd_purge(a):
    print(call(f"/api/admin/submissions/{a.id}/purge", "POST", {}))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list"); p.add_argument("--status")
    p = sub.add_parser("pull"); p.add_argument("id")
    p = sub.add_parser("status"); p.add_argument("id"); p.add_argument("status"); p.add_argument("--note")
    p = sub.add_parser("done"); p.add_argument("id"); p.add_argument("change"); p.add_argument("--note")
    p = sub.add_parser("purge"); p.add_argument("id")
    p = sub.add_parser("credit"); p.add_argument("id"); p.add_argument("change")
    a = ap.parse_args()
    {"list": cmd_list, "pull": cmd_pull, "status": cmd_status, "done": cmd_done, "purge": cmd_purge,
     "credit": lambda a: credit_contributor(a.id, [o.get("p") or o.get("to") for o in json.load(open(a.change)).get("ops", [])])}[a.cmd](a)


if __name__ == "__main__":
    main()
