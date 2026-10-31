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
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

EXCLUDE_TOP = {".git", "_Inbox", "site", "node_modules", "__pycache__", "catalog", "tools", "worker", "setup", ".sej-cache", "_dist", "_Sources"}
SKIP_FILES = {".DS_Store", "Thumbs.db", "desktop.ini", "Icon\r"}


def walk(path, top=False):
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
            node["d"].append(walk(e.path))
        elif e.is_file(follow_symlinks=False):
            if top:  # README, LICENSE, scripts at the root are not course content
                continue
            st = e.stat()
            node["f"].append([e.name, st.st_size, int(st.st_mtime)])
    return node


def programmes(repo=REPO):
    """catalog/programmes.json: each programme's semester-by-semester course list (made from the official curricula)."""
    try:
        with open(os.path.join(repo, "catalog", "programmes.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def build(repo=REPO, file_base="files/"):
    return {
        "name": "Sadaqah-e-Jariyah",
        "version": 1,
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fileBase": file_base,
        "programmes": programmes(repo),
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
