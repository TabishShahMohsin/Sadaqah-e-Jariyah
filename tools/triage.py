#!/usr/bin/env python3
"""
Sort one website contribution with the sorter (tools/sorter) and open a pull request for a maintainer to approve.

    python tools/triage.py --issue 41

Files, whole folders, zips and links of any number come in; every one of them leaves with exactly one outcome
(placed, credited to an existing copy, duplicate, link entry, needs review, or not added with a reason).

Security model
- The issue must carry a signature only the intake Worker can make; forged issues are ignored.
- Contributor text and file contents are untrusted. Claude sees them as data and can only act through its tools;
  every decision is checked by tools/rules.py. It cannot run commands, touch other paths or publish anything.
  A maintainer approves the pull request.
"""
import argparse
import mimetypes
import os
import re
import sys
import tempfile

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import rules  # noqa: E402
import sej  # noqa: E402
import scrub  # noqa: E402
from sorter import util  # noqa: E402
from common import Intake, env, gh, git, r2  # noqa: E402

MODEL = os.environ.get("SEJ_MODEL") or "claude-sonnet-5-5"  # empty repo variable → default
MAX_DRIVE_FILES = 3000
MAX_DRIVE_BYTES = 30 * 1024**3
MARKER = re.compile(r"<!--\s*sej-submission:([a-z0-9]{12}):([0-9a-f]{64})\s*-->")


# ── Google Drive (official API, public links only) ─────────────────────────
DRIVE = "https://www.googleapis.com/drive/v3/files"
EXPORTS = {
    "application/vnd.google-apps.document": ("application/pdf", "pdf"),
    "application/vnd.google-apps.presentation": ("application/pdf", "pdf"),
    "application/vnd.google-apps.spreadsheet": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
}
FOLDER = "application/vnd.google-apps.folder"


def drive_id(url):
    for pat in (r"/folders/([A-Za-z0-9_-]{10,})", r"/file/d/([A-Za-z0-9_-]{10,})", r"/d/([A-Za-z0-9_-]{10,})", r"[?&]id=([A-Za-z0-9_-]{10,})"):
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def drive_list(link, key, problems):
    """Expand a Drive link into [{id,name,mimeType,size,folder}] (folders walked 8 levels deep; shortcuts kept as such)."""
    fid = drive_id(link)
    if not fid:
        problems.append(f"Couldn't read the Drive link {link}")
        return []
    r = requests.get(f"{DRIVE}/{fid}", params={"fields": "id,name,mimeType,size,shortcutDetails", "supportsAllDrives": "true", "key": key}, timeout=30)
    if r.status_code in (403, 404):
        problems.append(f"The Drive link {link} isn't shared publicly (set it to 'Anyone with the link'); it is kept as a link entry.")
        return []
    r.raise_for_status()
    meta = r.json()
    if meta.get("mimeType") != FOLDER:
        return [meta]
    out, queue = [], [(fid, meta.get("name", ""), 0)]
    while queue and len(out) < MAX_DRIVE_FILES:
        folder, prefix, depth = queue.pop(0)
        token = None
        while True:
            params = {"q": f"'{folder}' in parents and trashed=false", "fields": "nextPageToken,files(id,name,mimeType,size,shortcutDetails)",
                      "pageSize": 200, "supportsAllDrives": "true", "includeItemsFromAllDrives": "true", "key": key}
            if token:
                params["pageToken"] = token
            r = requests.get(DRIVE, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            for f in data.get("files", []):
                if f["mimeType"] == FOLDER:
                    if depth < 8:
                        queue.append((f["id"], f"{prefix}/{f['name']}", depth + 1))
                else:
                    f["folder"] = prefix
                    out.append(f)
            token = data.get("nextPageToken")
            if not token:
                break
    if len(out) >= MAX_DRIVE_FILES:
        problems.append(f"The Drive folder has more than {MAX_DRIVE_FILES} files; only the first {MAX_DRIVE_FILES} were taken.")
    return out[:MAX_DRIVE_FILES]


def drive_download(f, key, dest_dir):
    """Download one Drive file with size limits. Returns (path, name) or raises ValueError(reason)."""
    name = f["name"]
    if f["mimeType"] in EXPORTS:
        mime, ext = EXPORTS[f["mimeType"]]
        url, params = f"{DRIVE}/{f['id']}/export", {"mimeType": mime, "key": key}
        name = f"{name}.{ext}"
    elif f["mimeType"].startswith("application/vnd.google-apps"):
        raise ValueError("this kind of Google file can't be downloaded")
    else:
        url, params = f"{DRIVE}/{f['id']}", {"alt": "media", "supportsAllDrives": "true", "key": key}
        size = int(f.get("size") or 0)
        err = rules.check_upload(name, size or 1)
        if err:
            raise ValueError(err)
    path = os.path.join(dest_dir, f"drive-{f['id']}")
    total = 0
    with requests.get(url, params=params, stream=True, timeout=60) as r:
        if r.status_code in (403, 404):
            raise ValueError("not shared publicly, or Google blocked the download")
        r.raise_for_status()
        with open(path, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                total += len(chunk)
                if total > rules.MAX_FILE_BYTES:
                    raise ValueError("larger than 500 MB")
                fh.write(chunk)
    return path, name


# ── inputs: uploaded files, Drive links, other links ───────────────────────
def collect(sub, s3, inbox, workdir, problems):
    """Every uploaded file and every link becomes an item. Folder paths inside uploads are kept (the best clue)."""
    from sorter import ingest
    items = []
    for f in sorted(sub["files"], key=lambda x: x["idx"]):
        if not f.get("done"):
            continue
        local = os.path.join(workdir, f"u{f['idx']:04d}.{f['ext'] or 'bin'}")
        s3.download_file(inbox, f["r2_key"], local)
        it = ingest.new_item(items, util.safe_rel(f["name"]) or f"file-{f['idx']}", local, r2_key=f["r2_key"])
        with open(local, "rb") as fh:
            head_bytes = fh.read(65536)
        if it["ext"] in ("pdf", "png", "jpg", "jpeg", "webp") and not rules.magic_ok(it["ext"], head_bytes):
            it["outcome"] = {"action": "reject", "category": "unreadable", "reason": f"content doesn't match .{it['ext']}"}
    key = os.environ.get("DRIVE_API_KEY")
    total = 0
    for link in sub.get("links") or []:
        if drive_id(link) and key:
            listed = drive_list(link, key, problems)
            if not listed:  # private or broken: still recorded, as a link entry the agent places
                link_item(items, link, "Google Drive link (not public)")
            for df in listed:
                folder = (df.get("folder") or "").strip("/")
                src = "drive/" + (folder + "/" if folder else "") + df.get("name", "?")
                if df.get("mimeType") == "application/vnd.google-apps.shortcut":
                    tid = (df.get("shortcutDetails") or {}).get("targetId")
                    link_item(items, f"https://drive.google.com/file/d/{tid}/view" if tid else link, df.get("name", ""), src=src)
                    continue
                try:
                    if total > MAX_DRIVE_BYTES:
                        raise ValueError("the Drive links add up to more than 30 GB")
                    path, name = drive_download(df, key, workdir)
                    total += os.path.getsize(path)
                    if name != df.get("name"):
                        src = src + name[len(df.get("name", "")):]
                    ingest.new_item(items, src, path)
                except (ValueError, requests.RequestException) as e:
                    it = ingest.new_item(items, src, None)
                    it["outcome"] = {"action": "review", "reason": f"couldn't download from Drive: {e}"}
        else:
            if drive_id(link) and not key:
                problems.append("Drive links can't be downloaded yet (no DRIVE_API_KEY); they were recorded as link entries.")
            link_item(items, link, page_title(link))
    return items


def link_item(items, url, title, src=None):
    from sorter import ingest
    it = ingest.new_item(items, src or url, None, url=url)
    it["ext"], it["page_title"] = "url", util.clean_text(title, 150)
    return it


def page_title(url):
    """The page's <title>, so a link can be named and placed. Public https pages only (the Worker checked the URL)."""
    try:
        with requests.get(url, timeout=10, stream=True, allow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (sej-sorter)"}) as r:
            head = r.raw.read(200000, decode_content=True).decode("utf-8", "ignore")
        m = re.search(r"<title[^>]*>(.*?)</title>", head, re.I | re.S)
        return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    except Exception:  # noqa: BLE001 — a title is a nicety
        return ""


def presign(s3, bucket, key):
    if os.environ.get("PREVIEW_LINKS", "true").lower() != "true" or not key:
        return None
    return s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=3 * 24 * 3600)


def conventions():
    with open(os.path.join(REPO, "CONVENTIONS.md"), encoding="utf-8") as fh:
        return fh.read()


# ── main flow ──────────────────────────────────────────────────────────────
def run(issue_no, force=False):
    from sorter import agent, courses, pipeline, report
    intake = Intake()
    body = gh("issue", "view", str(issue_no), "--json", "body", "--jq", ".body")
    m = MARKER.search(body or "")
    if not m or not intake.verify_sig(m.group(1), m.group(2)):
        print("Issue has no valid submission signature; ignoring.")
        return 0
    sid = m.group(1)
    sub = intake.get(sid)
    if sub["status"] in ("in_review", "published", "rejected") and not force:
        print(f"Submission {sid} is already {sub['status']}; nothing to do.")
        return 0
    intake.update(sid, status="sorting", issue=int(issue_no))

    s3 = r2()
    inbox = env("INBOX_BUCKET", "sej-inbox")
    state = sej.load_state()
    archive = courses.Archive(REPO, state, sej, rules)
    credit = util.clean_text(sub.get("credit"), 80) or None
    problems = []
    upload_text = (sub.get("context") or "").strip()
    if sub.get("course_hint"):
        upload_text += f"\n(Course picked in the form, may be wrong: {sub['course_hint']})"
    call = agent.ApiClient()
    with tempfile.TemporaryDirectory() as workdir:
        items = collect(sub, s3, inbox, workdir, problems)
        pipeline.sort(items, archive=archive, rules=rules, workdir=workdir, upload_text=upload_text, call=call,
                      conventions=conventions(), log=print)
        finals, staged = {}, {}
        for it in items:
            if (it.get("outcome") or {}).get("action") == "place":
                f = pipeline.finalize(it, workdir, scrub)
                if not f:
                    continue
                key = f"inbox/{sid}/sorted/{it['id']}-{rules.safe_name(it['name'])}"
                s3.upload_file(f["path"], inbox, key, ExtraArgs={"ContentType": mimetypes.guess_type(it["name"])[0] or "application/octet-stream"})
                finals[it["id"]], staged[it["id"]] = f, key
    ops = pipeline.ops_for(items, state, finals, credit, staged=staged)
    c = report.counts(items)

    def link(it):
        k = it.get("r2_key")
        return presign(s3, inbox, k) if k else (it.get("url") if it.get("ext") == "url" else None)

    if not ops:
        reasons = [f"- {report.md(it['src'])}: {report.md((it.get('outcome') or {}).get('reason', ''))}" for it in items
                   if (it.get("outcome") or {}).get("action") in ("reject", "review")][:40]
        msg = ["Thanks for contributing. Nothing from this contribution could be added yet:", "", *reasons, *[f"- {report.md(p)}" for p in problems]]
        gh("issue", "comment", str(issue_no), "--body-file", "-", input="\n".join(msg))
        status = "needs_info" if c.get("review") or problems else "rejected"
        intake.update(sid, status=status, note="; ".join(r[2:] for r in reasons[:5])[:1900])
        gh("issue", "edit", str(issue_no), "--add-label", "needs-info" if status == "needs_info" else "not-added", check=False)
        if status == "rejected":
            gh("issue", "close", str(issue_no), "--reason", "not planned", check=False)
        return 0

    source = {"kind": "submission", "id": sid, "issue": int(issue_no), "sorted_by": "sorter"}
    if credit:
        source["credit"] = credit
    branch = f"contrib/{sid}"
    git("config", "user.name", "sej-bot")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    git("checkout", "-B", branch)
    for old in [f for f in sej.change_files() if f.endswith(f"-{sid}.json")]:  # a re-run replaces the earlier plan
        os.remove(os.path.join(sej.CHANGES, old))
    path = sej.write_change(ops, source, sid)
    mdir = os.path.join(REPO, "catalog", "manifests")
    os.makedirs(mdir, exist_ok=True)
    report.dump(items, os.path.join(mdir, f"{sid}.json"))  # every input and its outcome, kept for audits
    full = report.markdown(items, f"Contribution {sid}", link=link,
                           extra=[f"Refs #{issue_no}. Credit: **{report.md(credit or 'anonymous')}**",
                                  *[f"- ⚠️ {report.md(p)}" for p in problems],
                                  f"Claude: {call.usage['calls']} calls · {call.usage['input'] + call.usage['cache_read']:,} in / {call.usage['output']:,} out tokens"])
    with open(os.path.join(mdir, f"{sid}.md"), "w", encoding="utf-8") as fh:
        fh.write(full)
    git("add", "-A", "catalog/changes", "catalog/manifests")
    git("commit", "-m", f"Contribution {sid}: {c.get('place', 0)} placed, {c.get('credit', 0) + c.get('duplicate', 0)} credited")
    git("push", "--force", "origin", branch)

    tail = ["", "File links expire in 3 days. To change a name or folder, edit the change file in this PR before merging.",
            "Merging publishes the placed files and records the credits."]
    pr_body = full + "\n".join(tail)
    if len(pr_body) > 60000:  # GitHub's limit is 65,536; the full page is in the branch
        pr_body = full[:52000].rsplit("\n", 1)[0] + f"\n\n… cut short: the full review page is `catalog/manifests/{sid}.md` in this PR." + "\n".join(tail)
    existing = gh("pr", "list", "--head", branch, "--state", "open", "--json", "number", "--jq", ".[0].number", check=False).strip()
    title = f"Contribution {sid}: {c.get('place', 0)} new file{'s' * (c.get('place', 0) != 1)}" + (f", {c['review']} to check" if c.get("review") else "")
    if existing:
        gh("pr", "edit", existing, "--title", title, "--body-file", "-", input=pr_body)
        pr = int(existing)
    else:
        url = gh("pr", "create", "--head", branch, "--base", os.environ.get("BASE_BRANCH", "main"), "--title", title,
                 "--body-file", "-", input=pr_body).strip()
        pr = int(url.rstrip("/").split("/")[-1])
    gh("issue", "comment", str(issue_no), "--body",
       f"Sorted {len(items)} files: {c.get('place', 0)} to add, {c.get('credit', 0) + c.get('duplicate', 0)} already in the archive (credited), "
       f"{c.get('review', 0)} for a person to check, {c.get('reject', 0)} not added. Review in #{pr}.")
    intake.update(sid, status="in_review", pr=pr, note=f"{c.get('place', 0)} file(s) sorted, waiting for review")
    print(f"PR #{pr}: {dict(c)} ({os.path.relpath(path, REPO)})")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue", required=True, type=int)
    ap.add_argument("--force", action="store_true", help="re-sort even if already in review")
    a = ap.parse_args()
    try:
        sys.exit(run(a.issue, a.force))
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}")
        gh("issue", "comment", str(a.issue), "--body",
           "Sorting this contribution failed. A maintainer will look at it (re-run the 'Sort contribution' workflow).", check=False)
        raise


if __name__ == "__main__":
    main()
