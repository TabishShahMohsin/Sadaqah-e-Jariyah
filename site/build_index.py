#!/usr/bin/env python3
"""
Builds site/index.json — the entire Sadaqah-e-Jariyah archive described as one JSON file.

    python3 site/build_index.py                       # files served locally by serve.py
    python3 site/build_index.py --file-base https://cdn.example.com/sej/   # files hosted elsewhere

Format (kept compact on purpose):
    { "generated": ISO-8601, "fileBase": "files/",
      "root": { "n": "", "d": [<dir>...], "f": [[name, bytes, mtime], ...] } }
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

EXCLUDE_TOP = {".git", "_Inbox", "site", "node_modules", "__pycache__", "catalog", "tools", "worker", "setup", ".sej-cache", "_dist", "_Sources", "_Originals"}
SKIP_FILES = {".DS_Store", "Thumbs.db", "desktop.ini", "Icon\r"}


_META = None
_LINKS = None
META_KEYS = ("author", "professor", "year", "credit", "source", "note", "url")


def catalog_meta(repo=REPO):
    """Credits and other file metadata kept in the catalog (catalog/base.jsonl + changes), keyed by archive path."""
    global _META, _LINKS
    if _META is None:
        _META, _LINKS = {}, {}
        try:
            sys.path.insert(0, os.path.join(repo, "tools"))
            import sej
            for p, e in sej.load_state().items():
                m = {k: v for k, v in (e.get("meta") or {}).items() if k in META_KEYS and v}
                if e.get("link"):  # link entries live only in the catalog; they appear in their folder like a file
                    parent, _, name = p.rpartition("/")
                    _LINKS.setdefault(parent, []).append([name, 0, e.get("t", 0), m])
                elif m:
                    _META[p] = m
        except Exception:  # noqa: BLE001 — no catalog: the index simply carries no credits
            _META, _LINKS = {}, {}
    return _META


def catalog_links(rel_dir):
    catalog_meta()
    return _LINKS.get(rel_dir, [])


def walk(path, top=False, base=None):
    base = base or path
    node = {"n": "" if top else os.path.basename(path), "d": [], "f": []}
    try:
        entries = sorted(os.scandir(path), key=lambda e: e.name.lower())
    except (PermissionError, FileNotFoundError):
        return node
    for e in entries:
        if e.name.startswith(".") or e.name in SKIP_FILES:
            continue
        if top and e.name in EXCLUDE_TOP:
            continue
        if e.is_dir(follow_symlinks=False):
            node["d"].append(walk(e.path, base=base))
        elif e.is_file(follow_symlinks=False):
            if top:  # README, LICENSE, scripts at the root are not course content
                continue
            st = e.stat()
            row = [e.name, st.st_size, int(st.st_mtime)]
            m = catalog_meta().get(os.path.relpath(e.path, base).replace(os.sep, "/"))
            if m:
                row.append(m)
            node["f"].append(row)
    if not top:
        node["f"].extend(catalog_links(os.path.relpath(path, base).replace(os.sep, "/")))
    return node


def programmes(repo=REPO):
    """catalog/programmes.json: each programme's semester-by-semester course list (made from the official curricula)."""
    try:
        with open(os.path.join(repo, "catalog", "programmes.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def sources(repo=REPO):
    """catalog/sources.json for the credits page: public fields only (never the Drive owners' e-mail addresses)."""
    try:
        with open(os.path.join(repo, "catalog", "sources.json"), encoding="utf-8") as fh:
            src = json.load(fh).get("sources", [])
    except (OSError, ValueError):
        return []
    out = []
    for s in src:
        if s.get("status") == "dead" or not (s.get("url") or s.get("status") == "merged"):
            continue
        out.append({"t": s.get("title") or "", "u": s.get("url") or "", "c": s.get("credit") or "", "w": s.get("covers") or "",
                    "s": "in" if s.get("status") == "merged" else "out"})
    return out


def build(repo=REPO, file_base="files/"):
    return {
        "name": "Sadaqah-e-Jariyah",
        "version": 1,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fileBase": file_base,
        "programmes": programmes(repo),
        "sources": sources(repo),
        "root": walk(repo, top=True),
    }


def stats(node):
    files = len(node["f"])
    size = sum(f[1] for f in node["f"])
    for d in node["d"]:
        a, b = stats(d)
        files += a
        size += b
    return files, size


def write(index, out=os.path.join(HERE, "index.json")):
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, out)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file-base", default="files/", help="URL prefix the website uses to fetch files")
    ap.add_argument("--out", default=os.path.join(HERE, "index.json"))
    a = ap.parse_args()
    idx = build(file_base=a.file_base)
    n, s = stats(idx["root"])
    print(f"Indexed {n:,} files ({s / 1e9:.1f} GB) → {write(idx, a.out)}")
