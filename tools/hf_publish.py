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
  left out   book-like names, PDFs with an ISBN on the first pages (except syllabi), Books-and-Resources PDFs over
             40 pages (whole books; shorter reference pages, figures and papers are published),
             anything in catalog/exclude.txt, formats that are not study material (.exe, .out, .class, .html …)
  renamed    enrolment numbers are taken out of file names
  cleaned    author fields of PDFs/Office files and personal photo EXIF (GPS, owner) are blanked in the copy

Hugging Face is made to match exactly: missing files are uploaded, renamed ones are copied on the server (no re-upload),
anything no longer published is deleted. Config: catalog/hf-config.json {"repo_id", "token"} or HF_REPO / HF_TOKEN.
"""
import argparse
from urllib.parse import quote
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
STUDY_EXT = (rules.ALLOWED_EXT | rules.CODE_EXT | {"tsv", "c++", "cc", "cxx", "mp3", "m4a", "wav"}) - {"zip"}
SCAN_EXT = {"pdf"} | scrub.IMAGE_EXT | scrub.OFFICE_EXT
REFERENCE_MAX_PAGES = 40   # Books-and-Resources PDFs up to this length are published as reference material
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


_PUBLISH = None


def publish_anyway():
    """catalog/publish.txt: one archive path per line, reviewed and cleared despite the book heuristics."""
    global _PUBLISH
    if _PUBLISH is None:
        f = os.path.join(REPO, "catalog", "publish.txt")
        _PUBLISH = {l.strip() for l in open(f, encoding="utf-8")
                    if l.strip() and not l.startswith("#")} if os.path.exists(f) else set()
    return _PUBLISH


def why_left_out(p, e, scan, skip):
    name = p.rsplit("/", 1)[-1]
    ext = rules.ext_of(name)
    if e.get("link"):
        return "link"
    if ext not in STUDY_EXT:
        return f".{ext or '?'} is not study material"
    if not e.get("s"):
        return "empty file"
    if skip(p):
        return "catalog/exclude.txt"
    if p in publish_anyway():
        return None  # reviewed by hand: shared by the teacher or author for students
    if BOOKISH.search(name):
        return "book-like name"
    if (scan.get(e.get("h")) or {}).get("isbn") and "/Syllabus/" not in p:
        return "ISBN on first pages (published book)"
    if "/Books-and-Resources/" in p and ext == "pdf":
        # reference pages, figures, papers and short excerpts are shared; long PDFs here are usually whole books
        pages = (scan.get(e.get("h")) or {}).get("pages")
        if pages is None:
            return "Books-and-Resources (page count not checked yet: run scan)"
        if pages > REFERENCE_MAX_PAGES:
            return f"Books-and-Resources, {pages} pages (likely a whole book)"
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
            out["pages"] = len(r.pages)
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
        stale = h in scan and ext == "pdf" and "pages" not in scan[h] and "/Books-and-Resources/" in p
        if h and (h not in scan or stale) and ext in SCAN_EXT and not e.get("link") and os.path.exists(os.path.join(root, p)):
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
    if a.skip_over:                       # very large files can't finish inside a short session; leave them for a long run
        big = [p for p in uploads if local[p]["size"] > a.skip_over * 1e6]
        uploads = [p for p in uploads if p not in set(big)]
        if big:
            print(f"  leaving {len(big)} files over {a.skip_over:g} MB for a run without --skip-over")
    batch, size = [], 0
    for p in uploads + [None]:
        if p is not None:
            batch.append(p)
            size += local[p]["size"]
        if batch and (p is None or len(batch) >= 100 or size >= a.batch_mb * 1e6):
            if budget():
                return print("time is up; run again to continue")
            commit([CommitOperationAdd(path_in_repo=q, path_or_fileobj=local[q]["file"]) for q in batch],
                   f"Add {len(batch)} files")
            batch, size = [], 0
    if a.no_delete and deletes:
        print(f"  keeping {len(deletes):,} files that are no longer published (--no-delete)")
        deletes = []
    for i in range(0, len(deletes), 1000):
        if budget():
            return print("time is up; run again to continue")
        part = deletes[i:i + 1000]
        commit([CommitOperationDelete(path_in_repo=d) for d in part], f"Remove {len(part)} files no longer published")
    if not card_ok:
        commit([CommitOperationAdd(path_in_repo="README.md", path_or_fileobj=CARD.encode())], "Dataset card")
    print("in sync" + (" (nothing deleted)" if a.no_delete else ""))
    if not a.no_tag:
        print(f"release tag: {tag_release(api, repo)}")


def tag_release(api, repo):
    """Name the current state of the dataset (v2026-11-04, v2026-11-04.2 …) so it can always be fetched again."""
    head = api.dataset_info(repo).sha
    tags = {t.name: t.target_commit for t in api.list_repo_refs(repo, repo_type="dataset").tags}
    for name, commit_id in tags.items():
        if commit_id == head:
            return name + " (already)"
    base = "v" + time.strftime("%Y-%m-%d")
    name, n = base, 1
    while name in tags:
        n += 1
        name = f"{base}.{n}"
    api.create_tag(repo, tag=name, revision=head, repo_type="dataset")
    return name


def file_ids_bytes(b):
    return hashlib.sha256(b).hexdigest(), hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest(), len(b)


# ── website ─────────────────────────────────────────────────────────────────
HEADERS = ("/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: no-referrer\n  X-Frame-Options: SAMEORIGIN\n"
           "/fonts/*\n  Cache-Control: public, max-age=31536000, immutable\n  Access-Control-Allow-Origin: *\n/vendor/*\n  Cache-Control: public, max-age=2592000\n"
           "/brand/*\n  Cache-Control: public, max-age=604800\n/index.json\n  Cache-Control: no-cache\n  Access-Control-Allow-Origin: *\n"
           "/api/v1/*\n  Cache-Control: no-cache\n  Access-Control-Allow-Origin: *\n"
           "/app.js\n  Access-Control-Allow-Origin: *\n/app.css\n  Access-Control-Allow-Origin: *\n"
           # search engines list sej.org.in only, not the pages.dev copies of the same pages
           "https://:project.pages.dev/*\n  X-Robots-Tag: noindex\n")
# Where the page's code, styles and fonts load from (see FAST in site/app.js): pages.dev is served from India,
# the custom domain sej.org.in from Europe. The page itself may come from either; these always come from here.
FAST_ORIGIN = "https://sadaqah-e-jariyah.pages.dev/"


def build_index(state, scan, repo_id, root=REPO, ref=None):
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
           "fileBase": f"https://huggingface.co/datasets/{repo_id}/resolve/{ref or pinned_ref(repo_id)}/",
           "programmes": bi.programmes(root), "sources": bi.sources(root), "root": sej.build_tree(view)}
    # the Contribute page opens when the intake API and its Turnstile site key are both set (worker/setup.sh)
    sc = load_json(os.path.join(root, "catalog", "site-config.json"), {})
    if sc.get("api") and sc.get("turnstile_sitekey"):
        idx["api"], idx["turnstile"] = sc["api"].rstrip("/"), sc["turnstile_sitekey"]
    return idx, pub, left, unscanned


COURSE_RE = re.compile(r"^((?:[A-Z]{2,4}\d[0-9A-Z]\d{1,2}[A-Z]?|(?:DE|OE)-\d+)(?:-[A-Z]{2,4}\d[0-9A-Z]\d{1,2}[A-Z]?)*)_(.+)$")  # as in site/app.js


def pinned_ref(repo_id):
    """The release the site's file links point to: the newest tag, when it is the dataset's current state.
    A deployed site then keeps working while the dataset changes for the next one (files moved or removed on
    main don't break it); the next build pins the next tag. Falls back to main (with a warning) when the dataset
    has changes not yet tagged (run push first) or Hugging Face can't be reached."""
    try:
        from huggingface_hub import HfApi
        api = HfApi()
        head = api.dataset_info(repo_id).sha
        tags = {t.name: t.target_commit for t in api.list_repo_refs(repo_id, repo_type="dataset").tags}
        key = lambda t: [int(x) for x in re.findall(r"\d+", t)]  # noqa: E731
        for name in sorted(tags, key=key, reverse=True):
            if tags[name] == head:
                return name
        print("WARNING: the dataset's current state has no release tag (run push); file links point to main")
    except Exception as e:  # noqa: BLE001
        print(f"WARNING: couldn't read the dataset's release tags ({e}); file links point to main")
    return "main"


def latest_release(repo_id):
    """Newest release tag of the dataset (v<date>[.n]); None when Hugging Face can't be reached."""
    try:
        from huggingface_hub import HfApi
        tags = [t.name for t in HfApi().list_repo_refs(repo_id, repo_type="dataset").tags]
        key = lambda t: [int(x) for x in re.findall(r"\d+", t)]
        return max(tags, key=key) if tags else None
    except Exception:  # noqa: BLE001
        return None


def write_api(out, idx, pub, repo_id):
    """Static, read-only API next to the site (documented on the site's Download & API page):
    api/v1/files.json  every published file as a flat list, with its download URL and credits
    api/v1/sources.json the collections the archive was gathered from
    api/v1/programmes.json  degree programmes, branches and their semester-wise courses (what the site's menus use)
    Calls to api/v1/* are counted by functions/api/v1/[[path]].js (D1); totals at api/v1/stats.json."""
    state = sej.load_state()
    labels = (idx.get("programmes") or {}).get("labels", {})

    def where(p):  # the same reading of a path the website does: branch, semester (or group), course, section
        parts = p.split("/")
        w = {}
        k = rules.umbrella_depth(parts)
        if k and len(parts) > k + 1:
            w["branch"] = labels.get(parts[k], parts[k].replace("-", " "))
            m = re.fullmatch(r"(Sem|Prof-)(\d+)", parts[k + 1])
            if m:
                w["professional" if m.group(1) == "Prof-" else "semester"] = int(m.group(2))
            else:
                w["group"] = parts[k + 1].strip("_").replace("-", " ")
        for i, part in enumerate(parts[:-1]):
            m = COURSE_RE.match(part)
            if m:
                codes = [m.group(1)] if re.match(r"(DE|OE)-", m.group(1)) else m.group(1).split("-")
                w["course"] = {"codes": codes, "title": m.group(2).replace("-", " "), "path": "/".join(parts[:i + 1])}
                if i + 2 < len(parts):
                    w["section"] = parts[i + 1]
                break
        return w

    files = []
    for p, item in sorted(pub.items()):
        e = item["e"]
        meta = {k: v for k, v in (e.get("meta") or {}).items() if k in sej.META_KEYS and v}
        files.append({"path": p, "name": p.rsplit("/", 1)[-1], "size": e.get("s"), "modified": e.get("t"),
                      "url": idx["fileBase"] + quote(p), **where(p), **({"meta": meta} if meta else {})})
    links = [{"path": p, "url": e["meta"]["url"], **where(p), **({"title": e["meta"]["title"]} if e["meta"].get("title") else {})}
             for p, e in sorted(state.items()) if e.get("link") and (e.get("meta") or {}).get("url")]
    api = os.path.join(out, "api", "v1")
    os.makedirs(api, exist_ok=True)
    head = {"name": "Sadaqah-e-Jariyah", "generated": idx["generated"], "dataset": f"https://huggingface.co/datasets/{repo_id}",
            "fileBase": idx["fileBase"], "release": latest_release(repo_id), "count": len(files),
            "bytes": sum(f["size"] or 0 for f in files)}
    for name, body in (("files.json", {**head, "files": files, "links": links}),
                       ("sources.json", {**head, "sources": idx["sources"]}),
                       ("programmes.json", {**head, "programmes": idx["programmes"]})):
        with open(os.path.join(api, name), "w", encoding="utf-8") as fh:
            json.dump(body, fh, ensure_ascii=False, separators=(",", ":"))


def cmd_site(a, root=REPO):
    repo_id = (os.environ.get("HF_REPO") or load_json(CONFIG, {}).get("repo_id") or "").strip()
    if not repo_id:
        raise SystemExit(f"Set repo_id in {os.path.relpath(CONFIG, REPO)} (or HF_REPO)")
    staging = a.env == "staging"
    # staging reads the dataset's main (what the next release will be); production is pinned to a release tag
    idx, pub, left, unscanned = build_index(sej.load_state(), load_json(SCAN, {}), repo_id, root, "main" if staging else None)
    idx["env"] = a.env
    if unscanned:
        print(f"WARNING: {unscanned:,} files not checked yet (run scan); they are listed as they are")
    out = a.out or os.path.join(REPO, "_dist-staging" if staging else "_dist")
    if os.path.isdir(os.path.join(out, "files")):
        raise SystemExit(f"{out}/files exists: the files are on Hugging Face now; move it out of the way first")
    os.makedirs(out, exist_ok=True)
    for f in ("app.css", "app.js"):
        shutil.copyfile(os.path.join(root, "site", f), os.path.join(out, f))
    with open(os.path.join(root, "site", "index.html"), encoding="utf-8") as fh:
        html = fh.read()
    if staging:  # its own code and data, never listed by search engines, marked for the app
        html = html.replace("<head>", '<head>\n<meta name="sej-env" content="staging">\n<meta name="robots" content="noindex, nofollow">', 1)
    else:
        for asset in ('href="/app.css"', 'src="/app.js"', 'href="/fonts/'):
            attr, path = asset.split('="/')
            html = html.replace(asset, f'{attr}="{FAST_ORIGIN}{path}')
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(html)
    # Addresses that are no page get a real 404 status (stray and bot requests), still with the app, which shows a
    # file's preview when the address is a file and "Nothing at this address" (noindex) otherwise.
    with open(os.path.join(out, "404.html"), "w", encoding="utf-8") as fh:
        fh.write(html)
    for sub in ("fonts", "brand", "vendor"):
        shutil.copytree(os.path.join(root, "site", sub), os.path.join(out, sub), dirs_exist_ok=True)
    with open(os.path.join(out, "_headers"), "w") as fh:
        fh.write(HEADERS + ("/*\n  X-Robots-Tag: noindex, nofollow\n" if staging else ""))
    # Pages Functions run only where needed: pages (functions/_middleware.js sends pages.dev page requests to
    # sej.org.in) and the API; the code, styles, data and fonts that sej.org.in loads from pages.dev skip them.
    with open(os.path.join(out, "_routes.json"), "w") as fh:
        json.dump({"version": 1, "include": ["/*"],
                   "exclude": ["/app.js", "/app.css", "/index.json", "/fonts/*", "/vendor/*", "/brand/*",
                               "/sitemap.xml", "/robots.txt"]}, fh)
    import seo_pages
    with open(os.path.join(root, "site", "app.js"), encoding="utf-8") as fh:
        n_pages = seo_pages.write(out, idx, html, fh.read())
    if staging:
        os.remove(os.path.join(out, "sitemap.xml"))
        with open(os.path.join(out, "robots.txt"), "w") as fh:
            fh.write("User-agent: *\nDisallow: /\n")
        print(f"{n_pages:,} pages (staging: not for search engines)")
    else:
        print(f"{n_pages:,} pages for search engines, sitemap.xml, robots.txt")
    with open(os.path.join(out, "index.json.tmp"), "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(os.path.join(out, "index.json.tmp"), os.path.join(out, "index.json"))
    write_api(out, idx, pub, repo_id)
    report = os.path.join(root, ".sej-cache", "left-out.txt")
    with open(report, "w", encoding="utf-8") as fh:
        fh.writelines(f"{why:40s} {p}\n" for p, why in sorted(left, key=lambda x: (x[1], x[0])))
    print(f"{out}: {len(pub):,} files listed, {len(left):,} left out (see {os.path.relpath(report, root)})")
    print(f"File links point to: {idx['fileBase']}")
    print("Deploy (from the repo folder, so functions/ is included):\n"
          f"  npx wrangler pages deploy {os.path.relpath(out, REPO)} --project-name sadaqah-e-jariyah --branch {a.env}"
          + ("\n  → https://staging.sej.org.in" if staging else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan"); p.add_argument("--budget", type=float, default=0, help="stop after N seconds")
    p.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    p = sub.add_parser("push"); p.add_argument("--dry-run", action="store_true"); p.add_argument("--show", type=int, default=15)
    p.add_argument("--budget", type=float, default=0, help="stop starting new commits after N seconds")
    p.add_argument("--allow-unscanned", action="store_true")
    p.add_argument("--no-tag", action="store_true", help="don't name this state of the dataset")
    p.add_argument("--no-delete", action="store_true", help="leave files on Hugging Face that are no longer published")
    p.add_argument("--batch-mb", type=float, default=400, help="start a new commit once a batch reaches this size")
    p.add_argument("--skip-over", type=float, default=0, help="leave files bigger than this many MB for a later run")
    p = sub.add_parser("site"); p.add_argument("--out", default=None, help="default: _dist, or _dist-staging")
    p.add_argument("--env", choices=["production", "staging"], default="production",
                   help="staging: its own address, file links to the dataset's main, noindex, features on trial")
    a = ap.parse_args()
    {"scan": cmd_scan, "push": cmd_push, "site": cmd_site}[a.cmd](a)


if __name__ == "__main__":
    main()
