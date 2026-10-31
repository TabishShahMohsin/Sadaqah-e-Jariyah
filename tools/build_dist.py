#!/usr/bin/env python3
"""
Build _dist/: a self-contained copy of the site + files that Cloudflare Pages can host on its own
(free plan: files ≤ 25 MiB, ≤ 20,000 files). Your originals are never modified.

    python3 tools/build_dist.py          # re-run until it says "ready" (it pauses every ~2.5 min)
    npx wrangler pages deploy _dist --project-name sadaqah-e-jariyah --branch production

Left out: Books-and-Resources, book-like file names, PDFs whose first pages show an ISBN, files over 25 MiB,
non-study formats. Enrollment numbers are removed from file names. PDF/Office author fields are blanked
in the published copy. A report of everything left out goes to .sej-cache/dist-report.txt (not published).
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
sys.path.insert(0, os.path.join(REPO, "site"))
import rules  # noqa: E402
import scrub  # noqa: E402
import sej  # noqa: E402
import build_index  # noqa: E402

CACHE = os.path.join(REPO, ".sej-cache", "dist-scan.json")
DONE = os.path.join(REPO, ".sej-cache", "dist-done.json")
REPORT = os.path.join(REPO, ".sej-cache", "dist-report.txt")
KEEP_EXT = rules.ALLOWED_EXT | {"tsv", "gif"}
BOOKISH = re.compile(r"(?i)(\bbook\b|book[-_ ]copy|textbook|edition|\d+(st|nd|rd|th)[-_ ]?ed\b|schaum|pearson|mcgraw|wiley|springer|"
                     r"o.?reilly|elsevier|cengage|prentice|addison|pressman|tocci|silberschatz|tanenbaum|forouzan|korth|navathe|"
                     r"cormen|kreyszig|halliday|sedra|boylestad|stallings|kurose|galvin)")
ENROLL = re.compile(r"(?<![A-Za-z0-9])\d{2}[A-Z]{3,5}\d{3}(?![0-9])")


def clean_name(name):
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    s = ENROLL.sub("", stem)
    s = re.sub(r"[_ ]{2,}", lambda m: "_" if "_" in m.group(0) else " ", s).strip(" _-") or "file"
    return f"{s}.{ext}" if ext else s


def pdf_scan(path):
    """→ {"isbn": bool, "meta": bool}"""
    out = {"isbn": False, "meta": False}
    try:
        from pypdf import PdfReader
        r = PdfReader(path)
        text = " ".join((r.pages[i].extract_text() or "") for i in range(min(4, len(r.pages))))
        out["isbn"] = bool(re.search(r"ISBN[\s:-]*[0-9][0-9\- ]{8,}", text))
        out["meta"] = bool(scrub.pdf_findings(path))
    except Exception:  # noqa: BLE001
        pass
    return out


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as fh:
        json.dump(data, fh)
    os.replace(path + ".tmp", path)


def save_cache(cache):
    save_json(CACHE, cache)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(REPO, "_dist"))
    ap.add_argument("--max-mb", type=float, default=25)
    ap.add_argument("--budget", type=float, default=90, help="seconds of scanning per run")
    a = ap.parse_args()
    limit = int(a.max_mb * 1024 * 1024)
    t0 = time.time()

    try:
        with open(CACHE) as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        cache = {}

    state = sej.load_state()
    skipped, chosen = [], []
    for p, e in sorted(state.items()):
        name = p.rsplit("/", 1)[-1]
        ext = rules.ext_of(name)
        if "/Books-and-Resources/" in p:
            skipped.append((p, "Books-and-Resources")); continue
        if e["s"] > limit:
            skipped.append((p, f"over {a.max_mb:g} MB (needs R2)")); continue
        if ext not in KEEP_EXT:
            skipped.append((p, f".{ext} not a study format")); continue
        if BOOKISH.search(name):
            skipped.append((p, "book-like name")); continue
        chosen.append((p, e, ext))

    # slow part: look inside PDFs/Office files (cached, time-boxed so each run stays short)
    pending = 0
    for p, e, ext in chosen:
        key = f"{p}|{e['h']}"
        if key in cache:
            continue
        if time.time() - t0 > a.budget:
            pending += 1
            continue
        src = os.path.join(REPO, p)
        if ext == "pdf":
            cache[key] = pdf_scan(src)
        elif ext in scrub.OFFICE_EXT:
            cache[key] = {"isbn": False, "meta": bool(scrub.office_findings(src))}
        else:
            cache[key] = {"isbn": False, "meta": False}
        if len(cache) % 20 == 0:
            save_cache(cache)
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w") as fh:
        json.dump(cache, fh)
    if pending:
        print(f"Scanned {len(cache):,}/{len(chosen):,} files; {pending:,} left. Run again to continue.")
        sys.exit(3)

    try:
        with open(DONE) as fh:
            done = json.load(fh)
    except (OSError, ValueError):
        done = {}
    files_root = os.path.join(a.out, "files")
    os.makedirs(files_root, exist_ok=True)
    used, linked, cleaned, kept_meta, renamed = set(), 0, 0, 0, 0
    for p, e, ext in chosen:
        info = cache[f"{p}|{e['h']}"]
        if info["isbn"] and "/Syllabus/" not in p:  # syllabi list reference books with ISBNs
            skipped.append((p, "ISBN on first pages (published book)")); continue
        parts = p.split("/")
        new_name = clean_name(parts[-1])
        if new_name != parts[-1]:
            renamed += 1
        rel = rules.unique_path("/".join(parts[:-1] + [new_name]), used) if "." in new_name else "/".join(parts[:-1] + [new_name])
        used.add(rel)
        src, dst = os.path.join(REPO, p), os.path.join(files_root, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        done_key = f"{rel}|{e['h']}"
        if os.path.exists(dst) and os.path.getsize(dst) and (not info["meta"] or done.get(done_key)):
            continue
        if time.time() - t0 > a.budget + 40:
            save_json(DONE, done)
            print("Copying is taking a while; run again to continue.")
            sys.exit(3)
        if info["meta"]:
            shutil.copyfile(src, dst)
            ok = scrub.clean(dst, ext)
            if ok and ext == "pdf":
                try:
                    from pypdf import PdfReader
                    ok = len(PdfReader(dst).pages) == len(PdfReader(src).pages)
                except Exception:  # noqa: BLE001
                    ok = False
            if ok:
                cleaned += 1
            else:  # cleaning failed or changed the document: publish the original bytes instead
                with open(src, "rb") as fi, open(dst, "wb") as fo:
                    shutil.copyfileobj(fi, fo)
                kept_meta += 1
            done[done_key] = "clean" if ok else "kept"
            if len(done) % 15 == 0:
                save_json(DONE, done)
        else:
            try:
                os.link(src, dst)
            except OSError:
                shutil.copyfile(src, dst)
            linked += 1

    save_json(DONE, done)
    cleaned = sum(v == "clean" for v in done.values())
    kept_meta = sum(v == "kept" for v in done.values())
    for f in ("index.html", "app.css", "app.js"):
        shutil.copyfile(os.path.join(REPO, "site", f), os.path.join(a.out, f))
    for sub in ("fonts", "brand", "vendor"):
        shutil.copytree(os.path.join(REPO, "site", sub), os.path.join(a.out, sub), dirs_exist_ok=True)
    idx = {"name": "Sadaqah-e-Jariyah", "version": 1,
           "generated": sej.now_iso(), "fileBase": "files/", "root": build_index.walk(files_root, top=False)}
    idx["root"]["n"] = ""
    with open(os.path.join(a.out, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(a.out, "_headers"), "w") as fh:
        fh.write("/*\n  X-Content-Type-Options: nosniff\n  Referrer-Policy: no-referrer\n  X-Frame-Options: SAMEORIGIN\n"
                 "/files/*\n  Cache-Control: public, max-age=86400\n"
                 "/fonts/*\n  Cache-Control: public, max-age=31536000, immutable\n"
                 "/vendor/*\n  Cache-Control: public, max-age=2592000\n"
                 "/brand/*\n  Cache-Control: public, max-age=604800\n"
                 "/index.json\n  Cache-Control: no-cache\n")

    n, size = build_index.stats(idx["root"])
    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write(f"Left out of the hosted copy ({len(skipped)}):\n")
        fh.writelines(f"  {why:40s} {p}\n" for p, why in sorted(skipped, key=lambda x: (x[1], x[0])))
    print(f"ready: {n:,} files, {size / 1e9:.2f} GB in {os.path.relpath(a.out, REPO)}/")
    print(f"  {cleaned} author fields blanked, {kept_meta} kept (couldn't be cleaned safely), {renamed} names had enrollment numbers removed")
    print(f"  {len(skipped)} left out; list in {os.path.relpath(REPORT, REPO)}")
    if n > 19500:
        print("  WARNING: close to the 20,000 file limit")


if __name__ == "__main__":
    main()
