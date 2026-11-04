#!/usr/bin/env python3
"""
Publish the archive: study files to the Hugging Face dataset, website to Cloudflare Pages.

    python3 tools/hf_publish.py scan            check every file once: ISBN on the first pages, personal metadata
                                                (re-run until it says "done"; results are cached by content)
    python3 tools/hf_publish.py push --dry-run  what would change on Hugging Face
    python3 tools/hf_publish.py push            make the dataset match (re-run until "in sync")
    python3 tools/hf_publish.py site            build _dist/ (website + index.json; the files stay on Hugging Face)
    npx wrangler pages deploy _dist --project-name sadaqah-e-jariyah --branch production

What is published, and how (one rule set for the dataset and the website, so they always agree):
  left out   Books-and-Resources sections, book-like names, PDFs with an ISBN on the first pages (except syllabi),
             anything in catalog/exclude.txt, formats that are not study material (.exe, .out, .class, .html …)
  renamed    enrolment numbers are taken out of file names
  cleaned    author fields of PDFs/Office files and personal photo EXIF (GPS, owner) are blanked in the copy

Hugging Face is made to match exactly: missing files are uploaded, renamed ones are copied on the server (no re-upload),
anything no longer published is deleted. Config: catalog/hf-config.json {"repo_id", "token"} or HF_REPO / HF_TOKEN.
"""
import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "site"))
import exclude  # noqa: E402
import rules  # noqa: E402
import scrub  # noqa: E402
import sej  # noqa: E402

CONFIG = os.path.join(REPO, "catalog", "hf-config.json")
SCAN = os.path.join(REPO, ".sej-cache", "hf-scan.json")      # sha256 → {"isbn": bool, "personal": [...]}
STAGE = os.path.join(REPO, ".sej-cache", "hf-stage")        # cleaned copies, named by the original's sha256
KEEP_REMOTE = {".gitattributes", "README.md"}
STUDY_EXT = (rules.ALLOWED_EXT | rules.CODE_EXT | {"tsv", "c++", "cc", "cxx"}) - {"zip"}
SCAN_EXT = {"pdf"} | scrub.IMAGE_EXT | scrub.OFFICE_EXT
_W = r"(?<![a-z])", r"(?![a-z])"  # whole words, with '_' '-' digits as separators
BOOKISH = re.compile(r"(?i)" + _W[0] + r"(book|book[-_ ]copy|textbook|edition|\d+(st|nd|rd|th)[-_ ]?ed|schaum'?s?|"
                     r"pearson|mcgraw|wiley|springer|o.?reilly|elsevier|cengage|prentice|addison|pressman|tocci|"
                     r"silberschatz|tanenbaum|forouzan|korth|navathe|cormen|kreyszig|halliday|sedra|boylestad|"
                     r"stallings|kurose|galvin|sullivan)" + _W[1])
ENROLL = re.compile(r"(?<![A-Za-z0-9])\d{2}[A-Z]{3,5}\d{3}(?![0-9])")
logging.getLogger("pypdf").setLevel(logging.ERROR)  # damaged-but-readable PDFs are common; their warnings are noise
ISBN = re.compile(r"ISBN[\s:-]*(?:1[03][\s:-]*)?[0-9][0-9\- ]{8,}[0-9Xx]")


# ── selection ───────────────────────────────────────────────────────────────
def clean_name(name):
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    def join(m):  # keep one separator where the number sat between two words, none at an edge
        left, right = m.group(1), m.group(2)
        if not left or not right:
            return ""
        return "_" if "_" in left + right else left[-1]
    s = re.sub(r"([-_ ]*)" + ENROLL.pattern + r"([-_ ]*)", join, stem)
    s = s.strip(" _-") or "file"
    return f"{s}.{ext}" if ext else s


def why_left_out(p, e, scan, skip):
    name = p.rsplit("/", 1)[-1]
    ext = rules.ext_of(name)
    if e.get("link"):
        return "link"
    if "/Books-and-Resources/" in p:
        return "Books-and-Resources"
    if ext not in STUDY_EXT:
        return f".{ext or '?'} is not study material"
    if not e.get("s"):
        return "empty file"
    if skip(p):
        return "catalog/exclude.txt"
    if BOOKISH.search(name):
        return "book-like name"
    if (scan.get(e.get("h")) or {}).get("isbn") and "/Syllabus/" not in p:
        return "ISBN on first pages (published book)"
    return None


def select(state, scan, root=REPO):
    """→ (published {path_in_repo: {"src", "e", "clean"}}, left_out [(path, reason)], unscanned count)."""
    skip = exclude.make(root)
    pub, left, unscanned, taken = {}, [], 0, set()
    for p in sorted(state):
        e = state[p]
        why = why_left_out(p, e, scan, skip)
        if why:
            if why != "link":
                left.append((p, why))
            continue
        ext = rules.ext_of(p)
        info = scan.get(e.get("h"))
        if ext in SCAN_EXT and info is None:
            unscanned += 1
        parent, _, name = p.rpartition("/")
        dest = rules.unique_path(f"{parent}/{clean_name(name)}", taken)
        taken.add(dest)
        pub[dest] = {"src": p, "e": e, "clean": bool(info and info.get("personal"))}
    return pub, left, unscanned


# ── scan ────────────────────────────────────────────────────────────────────
def scan_one(path, ext):
    out = {"isbn": False, "personal": []}
    try:
        if ext == "pdf":
            from pypdf import PdfReader
            r = PdfReader(path)
            text = " ".join((r.pages[i].extract_text() or "") for i in range(min(4, len(r.pages))))
            out["isbn"] = bool(ISBN.search(text))
            author = str((r.metadata or {}).get("/Author") or "").strip()
            out["personal"] = ["Author"] if is_person(author) else (["XMP author"] if xmp_author(r) else [])
        elif ext in scrub.IMAGE_EXT:
            out["personal"] = scrub.image_findings(path)[0]
        elif ext in scrub.OFFICE_EXT:
            out["personal"] = scrub.office_findings(path)
    except Exception as ex:  # noqa: BLE001 — unreadable file: publish as is, note it
        out["error"] = type(ex).__name__
    return out


PLACEHOLDER = re.compile(r"(?i)^(anonymous|unknown|admin(istrator)?|user|owner|author|default|guest|pc|hp|dell|lenovo|acer|asus|"
                         r"microsoft( office)? user|windows user|student|test|abc|xyz|none|n/?a|\W*)$")


def is_person(name):
    return bool(name) and not PLACEHOLDER.match(name.strip())


def xmp_author(reader):
    try:
        xmp = reader.trailer["/Root"].get("/Metadata")
        data = xmp.get_object().get_data().decode("utf-8", "ignore") if xmp else ""
        m = re.search(r"<dc:creator>.*?<rdf:li[^>]*>\s*([^<]*?)\s*<", data, re.S)
        return bool(m and is_person(m.group(1)))
    except Exception:  # noqa: BLE001
        return False


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(path + ".tmp", path)


def cmd_scan(a, root=REPO):
    state = sej.load_state()
    scan = load_json(SCAN, {})
    todo = {}
    for p, e in state.items():
        h, ext = e.get("h"), rules.ext_of(p)
        if h and h not in scan and ext in SCAN_EXT and not e.get("link") and os.path.exists(os.path.join(root, p)):
            todo.setdefault(h, (os.path.join(root, p), ext))
    print(f"{len(scan):,} scanned, {len(todo):,} to go ({a.jobs} at a time)", flush=True)
    t0, n = time.time(), 0
    with ProcessPoolExecutor(a.jobs) as pool:
        it = iter(todo.items())
        futs = {}
        for h, (path, ext) in it:
            futs[pool.submit(scan_one, path, ext)] = h
            if len(futs) >= a.jobs * 3:
                break
        while futs:
            done, _ = wait(futs, return_when=FIRST_COMPLETED)
            for f in done:
                scan[futs.pop(f)] = f.result()
                n += 1
                if n % 200 == 0:
                    save_json(SCAN, scan)
            if not (a.budget and time.time() - t0 > a.budget):
                for h, (path, ext) in it:
                    futs[pool.submit(scan_one, path, ext)] = h
                    if len(futs) >= a.jobs * 3:
                        break
    save_json(SCAN, scan)
    left = len(todo) - n
    isbn = sum(1 for v in scan.values() if v.get("isbn"))
    pers = sum(1 for v in scan.values() if v.get("personal"))
    print(f"scanned {n:,} now; {isbn} with an ISBN, {pers} with personal metadata so far; "
          + (f"{left:,} left — run again" if left else "done"))
    return left


# ── published bytes ─────────────────────────────────────────────────────────
def staged(item, root=REPO):
    """Path of the bytes to publish: the original, or a cleaned copy made once and kept in .sej-cache/hf-stage."""
    src = os.path.join(root, item["src"])
    if not item["clean"]:
        return src
    ext = rules.ext_of(item["src"])
    dst = os.path.join(STAGE, f"{item['e']['h']}.{ext}")
    if os.path.exists(dst):
        return dst
    failed = os.path.join(STAGE, "failed", f"{item['e']['h']}.{ext}")
    if os.path.exists(failed):
        item["clean"] = False
        return src
    os.makedirs(os.path.dirname(failed), exist_ok=True)
    tmp = dst + ".tmp." + ext
    shutil.copyfile(src, tmp)
    ok = scrub.clean(tmp, ext)
    if ok and ext == "pdf":
        try:
            from pypdf import PdfReader
            ok = len(PdfReader(tmp).pages) == len(PdfReader(src).pages)
        except Exception:  # noqa: BLE001
            ok = False
    if ok:
        os.replace(tmp, dst)
        return dst
    os.replace(tmp, failed)  # kept as a marker (not deleted) so it is not retried
    item["clean"] = False  # could not be cleaned safely: the original is published
    return src


def file_ids(path):
    """(sha256, git blob sha1, size) of a file."""
    h256, size = hashlib.sha256(), os.path.getsize(path)
    h1 = hashlib.sha1(b"blob %d\0" % size)
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(4 << 20), b""):
            h256.update(b)
            h1.update(b)
    return h256.hexdigest(), h1.hexdigest(), size


# ── plan: what to change on Hugging Face ────────────────────────────────────
def plan(local, remote):
    """local {path: {"sha256", "sha1", "size", "file"}}; remote {path: {"size", "sha256" or None, "sha1"}}.
    → (copies [(from, to)], uploads [path], deletes [path]). Copies reuse bytes already on the server."""
    by_sha = {}
    for rp, r in remote.items():
        if r.get("sha256"):
            by_sha.setdefault(r["sha256"], rp)
    copies, uploads = [], []
    for p, l in sorted(local.items()):
        r = remote.get(p)
        if r and r["size"] == l["size"] and (r.get("sha256") == l["sha256"] if r.get("sha256") else r.get("sha1") == l["sha1"]):
            continue
        src = by_sha.get(l["sha256"])
        if src and src != p:
            copies.append((src, p))
        else:
            uploads.append(p)
    deletes = sorted(rp for rp in remote if rp not in local and rp not in KEEP_REMOTE)
    return copies, uploads, deletes


def config():
    cfg = load_json(CONFIG, {})
    repo = (os.environ.get("HF_REPO") or cfg.get("repo_id") or "").strip()
    token = (os.environ.get("HF_TOKEN") or cfg.get("token") or "").strip()
    if not repo or not token or "YOUR" in repo or "YOUR" in token:
        raise SystemExit(f"Set repo_id and token in {os.path.relpath(CONFIG, REPO)} (or HF_REPO / HF_TOKEN)")
    return repo, token


def remote_files(api, repo):
    out = {}
    for f in api.list_repo_tree(repo, repo_type="dataset", recursive=True):
        if f.__class__.__name__ == "RepoFile":
            out[f.path] = {"size": f.size, "sha256": f.lfs.sha256 if f.lfs else None, "sha1": f.blob_id}
    return out


CARD = """---
license: other
license_name: mixed
pretty_name: Sadaqah-e-Jariyah — AMU B.Tech / B.E. study archive
language: [en]
tags: [education, engineering, aligarh-muslim-university, past-papers, lecture-notes]
viewer: false
---

# Sadaqah-e-Jariyah

Past papers, class notes, slides, lab records and syllabi for the B.Tech and B.E. programmes of
Zakir Husain College of Engineering & Technology, Aligarh Muslim University, sorted by branch, semester and course.

**Browse and search it at <https://sadaqah-e-jariyah.pages.dev>.** This dataset is the file store behind that site.

## Rights

Everything here was shared for students' study, free of charge. Rights stay with the people who made each file:
teachers, students and the University. Contributors' own work is shared under CC BY 4.0 with the credit they chose;
the website shows each file's credit. Published textbooks are not included.

If something here is yours and you want it removed or credited differently, open a discussion on this dataset
and it will be taken down.
"""


def cmd_push(a, root=REPO):
    from huggingface_hub import CommitOperationAdd, CommitOperationCopy, CommitOperationDelete, HfApi
    repo, token = config()
    state = sej.load_state()
    scan = load_json(SCAN, {})
    pub, left, unscanned = select(state, scan, root)
    if unscanned and not a.allow_unscanned:
        raise SystemExit(f"{unscanned:,} files have not been checked yet: run  scan  first")
    t0 = time.time()
    local = {}
    for p, item in pub.items():
        f = staged(item, root)
        if item["clean"] or not item["e"].get("h"):
            s256, s1, size = file_ids(f)
        else:  # untouched original: the catalog already knows its sha256; sha1 only matters for small files
            s256, size = item["e"]["h"], item["e"]["s"]
            s1 = file_ids(f)[1] if size < 10 << 20 else None
        local[p] = {"sha256": s256, "sha1": s1, "size": size, "file": f}
    api = HfApi(token=token)
    remote = remote_files(api, repo)
    copies, uploads, deletes = plan(local, remote)
    card_ok = remote.get("README.md", {}).get("sha1") == file_ids_bytes(CARD.encode())[1]
    up_bytes = sum(local[p]["size"] for p in uploads)
    print(f"{len(pub):,} files published ({sum(v['size'] for v in local.values()) / 1e9:.2f} GB), {len(left):,} left out")
    print(f"Hugging Face: {len(remote):,} files. To do: {len(copies):,} renames (server-side copy), "
          f"{len(uploads):,} uploads ({up_bytes / 1e9:.2f} GB), {len(deletes):,} deletions"
          + ("" if card_ok else ", dataset card"))
    if a.dry_run:
        for title, rows in (("rename", [f"{s}  →  {d}" for s, d in copies]), ("upload", uploads), ("delete", deletes)):
            for r in rows[: a.show]:
                print(f"  {title}: {r}")
            if len(rows) > a.show:
                print(f"  … {len(rows) - a.show:,} more to {title}")
        return
    budget = lambda: a.budget and time.time() - t0 > a.budget  # noqa: E731

    def commit(ops, msg):
        api.create_commit(repo, repo_type="dataset", operations=ops, commit_message=msg)
        print(f"  committed: {msg}", flush=True)

    for i in range(0, len(copies), 400):
        if budget():
            return print("time is up; run again to continue")
        part = copies[i:i + 400]
        commit([CommitOperationCopy(src_path_in_repo=s, path_in_repo=d) for s, d in part], f"Rename {len(part)} files")
    batch, size = [], 0
    for p in uploads + [None]:
        if p is not None:
            batch.append(p)
            size += local[p]["size"]
        if batch and (p is None or len(batch) >= 100 or size >= 400 << 20):
            if budget():
                return print("time is up; run again to continue")
            commit([CommitOperationAdd(path_in_repo=q, path_or_fileobj=local[q]["file"]) for q in batch],
                   f"Add {len(batch)} files")
            batch, size = [], 0
    for i in range(0, len(deletes), 1000):
        if budget():
            return print("time is up; run again to continue")
        part = deletes[i:i + 1000]
        commit([CommitOperationDelete(path_in_repo=d) for d in part], f"Remove {len(part)} files no longer published")
    if not card_ok:
        commit([CommitOperationAdd(path_in_repo="README.md", path_or_fileobj=CARD.encode())], "Dataset card")
    print("in sync")


def file_ids_bytes(b):
    return hashlib.sha256(b).hexdigest(), hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest(), len(b)


# ── website ─────────────────────────────────────────────────────────────────
HEADERS = ("/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: no-referrer\n  X-Frame-Options: SAMEORIGIN\n"
           "/fonts/*\n  Cache-Control: public, max-age=31536000, immutable\n/vendor/*\n  Cache-Control: public, max-age=2592000\n"
           "/brand/*\n  Cache-Control: public, max-age=604800\n/index.json\n  Cache-Control: no-cache\n")


def build_index(state, scan, repo_id, root=REPO):
    import build_index as bi
    pub, left, unscanned = select(state, scan, root)
    view = {}
    for p, item in pub.items():
        e = item["e"]
        size = os.path.getsize(staged(item, root)) if item["clean"] else e["s"]
        view[p] = {"s": size, "t": e.get("t", 0), "meta": e.get("meta")}
    for p, e in state.items():  # link entries appear in their folder like files
        if e.get("link"):
            view[p] = e
    idx = {"name": "Sadaqah-e-Jariyah", "version": 1, "generated": sej.now_iso(),
           "fileBase": f"https://huggingface.co/datasets/{repo_id}/resolve/main/",
           "programmes": bi.programmes(root), "root": sej.build_tree(view)}
    return idx, pub, left, unscanned


def cmd_site(a, root=REPO):
    repo_id = (os.environ.get("HF_REPO") or load_json(CONFIG, {}).get("repo_id") or "").strip()
    if not repo_id:
        raise SystemExit(f"Set repo_id in {os.path.relpath(CONFIG, REPO)} (or HF_REPO)")
    idx, pub, left, unscanned = build_index(sej.load_state(), load_json(SCAN, {}), repo_id, root)
    if unscanned:
        print(f"WARNING: {unscanned:,} files not checked yet (run scan); they are listed as they are")
    out = a.out
    if os.path.isdir(os.path.join(out, "files")):
        raise SystemExit(f"{out}/files exists: the files are on Hugging Face now; move it out of the way first")
    os.makedirs(out, exist_ok=True)
    for f in ("index.html", "app.css", "app.js"):
        shutil.copyfile(os.path.join(root, "site", f), os.path.join(out, f))
    for sub in ("fonts", "brand", "vendor"):
        shutil.copytree(os.path.join(root, "site", sub), os.path.join(out, sub), dirs_exist_ok=True)
    with open(os.path.join(out, "_headers"), "w") as fh:
        fh.write(HEADERS)
    with open(os.path.join(out, "index.json.tmp"), "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(os.path.join(out, "index.json.tmp"), os.path.join(out, "index.json"))
    report = os.path.join(root, ".sej-cache", "left-out.txt")
    with open(report, "w", encoding="utf-8") as fh:
        fh.writelines(f"{why:40s} {p}\n" for p, why in sorted(left, key=lambda x: (x[1], x[0])))
    print(f"{out}: {len(pub):,} files listed, {len(left):,} left out (see {os.path.relpath(report, root)})")
    print("Deploy (from the repo folder, so functions/ is included):\n"
          "  npx wrangler pages deploy _dist --project-name sadaqah-e-jariyah --branch production")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan"); p.add_argument("--budget", type=float, default=0, help="stop after N seconds")
    p.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    p = sub.add_parser("push"); p.add_argument("--dry-run", action="store_true"); p.add_argument("--show", type=int, default=15)
    p.add_argument("--budget", type=float, default=0, help="stop starting new commits after N seconds")
    p.add_argument("--allow-unscanned", action="store_true")
    p = sub.add_parser("site"); p.add_argument("--out", default=os.path.join(REPO, "_dist"))
    a = ap.parse_args()
    {"scan": cmd_scan, "push": cmd_push, "site": cmd_site}[a.cmd](a)


if __name__ == "__main__":
    main()
