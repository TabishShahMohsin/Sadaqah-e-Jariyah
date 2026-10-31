#!/usr/bin/env python3
"""
Publish the catalog: copy approved files from the private inbox to the public files bucket, apply moves and
removals, rebuild site/index.json, and tell contributors their files are live.

Idempotent: it replays the whole catalog every run and only copies what is missing, so re-running is always safe.

    python tools/publish.py            (in GitHub Actions after a merge to main)
    python tools/publish.py --dry-run  (show what would happen)
"""
import argparse
import json
import mimetypes
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import rules  # noqa: E402
import sej  # noqa: E402
from common import Intake, env, gh, head, r2  # noqa: E402

for ext, t in {".md": "text/markdown", ".ipynb": "application/json", ".c": "text/plain", ".cpp": "text/plain", ".h": "text/plain",
               ".hpp": "text/plain", ".java": "text/plain", ".py": "text/plain", ".csv": "text/plain", ".txt": "text/plain"}.items():
    mimetypes.add_type(t, ext)


def content_type(path):
    t = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return t + "; charset=utf-8" if t.startswith("text/") else t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    state = sej.load_state()
    known = set(sej.courses(sej.load_state(include_changes=False)))  # legacy folders from the first import
    new_courses = set(sej.courses(state)) - known                     # must follow CODE_Title
    s3 = None if a.dry_run else r2()
    inbox, files = env("INBOX_BUCKET", "sej-inbox"), env("FILES_BUCKET", "sej-files")

    # 1. validate staged additions before touching anything public
    staged = {p: e for p, e in state.items() if e.get("staged")}
    bad = []
    for p, e in staged.items():
        folder, _, name = p.rpartition("/")
        err = rules.check_destination(folder, name, rules.ext_of(name), known, new_courses)
        if err or not e["staged"].startswith("inbox/"):
            bad.append(f"{p}: {err or 'staged key outside inbox/'}")
    if bad:
        print("Refusing to publish; fix these entries in catalog/changes:\n  " + "\n  ".join(bad))
        sys.exit(1)

    # 2. copy approved files inbox → public
    published = {}  # submission id → [paths]
    change_of = {}
    for name in sej.change_files():
        with open(os.path.join(sej.CHANGES, name), encoding="utf-8") as fh:
            ch = json.load(fh)
        change_of[ch["id"]] = ch
    copied = 0
    for p, e in sorted(staged.items()):
        if a.dry_run:
            print("would publish", p)
            continue
        sid = ((change_of.get(e.get("change")) or {}).get("source") or {}).get("id")
        h = head(s3, files, p)
        if h and h["ContentLength"] == e["s"]:
            if sid:
                published.setdefault(sid, []).append(p)
            continue
        src = head(s3, inbox, e["staged"])
        if not src or src["ContentLength"] != e["s"]:
            print(f"! {p}: staged file {e['staged']} is missing or changed; skipped")
            continue
        s3.copy_object(Bucket=files, Key=p, CopySource={"Bucket": inbox, "Key": e["staged"]},
                       MetadataDirective="REPLACE", ContentType=content_type(p),
                       CacheControl="public, max-age=31536000, immutable", Metadata={"sha256": e["h"]})
        h = head(s3, files, p)
        assert h and h["ContentLength"] == e["s"], f"copy of {p} could not be verified"
        copied += 1
        if sid:
            published.setdefault(sid, []).append(p)
        print("published", p)

    # 3. moves and removals (only when the old object still exists)
    for ch in change_of.values():
        for op in ch.get("ops", []):
            if a.dry_run:
                continue
            if op["op"] == "move" and op["to"] in state and not head(s3, files, op["to"]) and head(s3, files, op["from"]):
                s3.copy_object(Bucket=files, Key=op["to"], CopySource={"Bucket": files, "Key": op["from"]})
                s3.delete_object(Bucket=files, Key=op["from"])
                print("moved", op["from"], "→", op["to"])
            elif op["op"] == "remove" and op["p"] not in state and head(s3, files, op["p"]):
                s3.copy_object(Bucket=files, Key="_trash/" + op["p"], CopySource={"Bucket": files, "Key": op["p"]})
                s3.delete_object(Bucket=files, Key=op["p"])
                print("removed", op["p"], "(kept in _trash/)")

    # 4. rebuild the index the website reads
    build = argparse.Namespace(file_base=env("FILES_BASE_URL"), api=os.environ.get("INTAKE_API"),
                               turnstile=os.environ.get("TURNSTILE_SITEKEY"), out=None)
    sej.cmd_build(build)

    # 5. tell contributors
    if not a.dry_run and published:
        intake = Intake() if os.environ.get("INTAKE_API") and os.environ.get("ADMIN_TOKEN") else None
        site = os.environ.get("SITE_URL", "").rstrip("/")
        for sid, paths in published.items():
            src = next((c["source"] for c in change_of.values() if (c.get("source") or {}).get("id") == sid), {})
            if intake:
                try:
                    if intake.get(sid).get("status") == "published":
                        continue  # announced on an earlier run
                except Exception as e:  # noqa: BLE001
                    print(f"warning: status lookup for {sid} failed: {e}")
                intake.update(sid, status="published", published=paths, note=f"{len(paths)} file(s) are live")
            if src.get("issue"):
                links = "\n".join(f"- {site}/#/{p}" if site else f"- `{p}`" for p in paths)
                gh("issue", "comment", str(src["issue"]), "--body", f"Published, thank you!\n\n{links}", check=False)
                gh("issue", "close", str(src["issue"]), "--reason", "completed", check=False)
    print(f"done: {copied} copied, {len(state):,} files in catalog")


if __name__ == "__main__":
    main()
