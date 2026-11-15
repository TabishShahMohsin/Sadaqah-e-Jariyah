#!/usr/bin/env python3
"""
sej — the archive catalog.

The catalog is append-only:
  catalog/base.jsonl          one line per file from the first import   {"p","s","h","t"}
  catalog/changes/*.json      one file per change set, applied in name order
                              {"id","created","source":{...},"ops":[...]}
  ops:  {"op":"add","p","s","h","t","staged"?,"meta"?}   add or replace a file
        {"op":"move","from","to"}
        {"op":"remove","p","reason"}
        {"op":"meta","p","meta"}                          update metadata only
        {"op":"link","p","url","t","meta"?}               a link entry (shown on the site like a file; p ends in .link)

Commands
  python3 tools/sej.py import            hash the local folder into catalog/base.jsonl (first time only)
  python3 tools/sej.py scan              record files added/changed locally as a new change set
  python3 tools/sej.py build [--file-base URL] [--api URL] [--turnstile KEY]   catalog → site/index.json
  python3 tools/sej.py status            compare the local folder with the catalog
  python3 tools/sej.py check             validate every change file
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CAT = os.path.join(REPO, "catalog")
BASE = os.path.join(CAT, "base.jsonl")
CHANGES = os.path.join(CAT, "changes")
CACHE = os.path.join(REPO, ".sej-cache", "hashes.json")

sys.path.insert(0, HERE)
import rules  # noqa: E402

EXCLUDE_TOP = {".git", "_Inbox", "site", "node_modules", "__pycache__", "catalog", "tools", "worker", ".github", ".sej-cache", "setup", "_dist", "_Sources"}
SKIP_FILES = {".DS_Store", "Thumbs.db", "desktop.ini", "Icon\r"}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── local folder ───────────────────────────────────────────
def iter_local(root=REPO):
    for top in sorted(os.listdir(root)):
        full = os.path.join(root, top)
        if top.startswith(".") or top in EXCLUDE_TOP or not os.path.isdir(full):
            continue
        for dirpath, dirnames, filenames in os.walk(full):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for f in sorted(filenames):
                if f.startswith(".") or f in SKIP_FILES:
                    continue
                fp = os.path.join(dirpath, f)
                if os.path.islink(fp):
                    continue
                yield os.path.relpath(fp, root).replace(os.sep, "/"), fp


def sha256_file(fp, bufsize=4 << 20):
    h = hashlib.sha256()
    with open(fp, "rb") as fh:
        while True:
            b = fh.read(bufsize)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


class HashCache:
    def __init__(self):
        try:
            with open(CACHE) as fh:
                self.d = json.load(fh)
        except (OSError, ValueError):
            self.d = {}
        self.dirty = 0

    def get(self, rel, fp):
        st = os.stat(fp)
        key = [st.st_size, int(st.st_mtime)]
        hit = self.d.get(rel)
        if hit and hit[:2] == key:
            return st.st_size, int(st.st_mtime), hit[2]
        h = sha256_file(fp)
        self.d[rel] = key + [h]
        self.dirty += 1
        if self.dirty % 50 == 0:
            self.save()
        return st.st_size, int(st.st_mtime), h

    def save(self):
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        tmp = CACHE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(self.d, fh)
        os.replace(tmp, CACHE)


# ── catalog state ──────────────────────────────────────────
def change_files():
    if not os.path.isdir(CHANGES):
        return []
    return sorted(f for f in os.listdir(CHANGES) if f.endswith(".json"))


def load_state(include_changes=True):
    """Returns {path: entry}. entry = {"p","s","h","t", "meta"?, "staged"?, "change"?}"""
    state = {}
    if os.path.exists(BASE):
        with open(BASE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    e = json.loads(line)
                    state[e["p"]] = e
    if include_changes:
        for name in change_files():
            with open(os.path.join(CHANGES, name), encoding="utf-8") as fh:
                ch = json.load(fh)
            apply_change(state, ch, name)
    return state


def apply_change(state, ch, name="?"):
    credit = (ch.get("source") or {}).get("credit")
    for op in ch.get("ops", []):
        kind = op.get("op")
        if kind == "add":
            e = {k: op[k] for k in ("p", "s", "h", "t") if k in op}
            meta = dict(op.get("meta") or {})
            if credit and "credit" not in meta:
                meta["credit"] = credit
            if meta:
                e["meta"] = meta
            if op.get("staged"):
                e["staged"] = op["staged"]
            e["change"] = ch.get("id", name)
            state[op["p"]] = e
        elif kind == "move":
            e = state.pop(op["from"], None)
            if e is not None:
                e = dict(e, p=op["to"])
                state[op["to"]] = e
        elif kind == "remove":
            state.pop(op["p"], None)
        elif kind == "link":
            e = {"p": op["p"], "s": 0, "h": "", "t": op.get("t", 0), "url": op["url"], "link": True,
                 "meta": {**(op.get("meta") or {}), "url": op["url"]}}
            e["change"] = ch.get("id", name)
            state[op["p"]] = e
        elif kind == "meta":
            if op["p"] in state:
                state[op["p"]]["meta"] = {**state[op["p"]].get("meta", {}), **op.get("meta", {})}


def courses(state):
    """Course folders present in the catalog: path -> {sections: {name: count}}"""
    out = {}
    for p in state:
        parts = p.split("/")
        k = 1 if parts[0] in rules.UMBRELLAS else 0  # 'BTech-and-BE/Branch/Term/Course/...'
        if parts[k:k + 1] == ["Shared-Courses"] and len(parts) >= k + 4 and not rules.TERM_RE.match(parts[k + 1]):
            cp = "/".join(parts[:k + 2])  # Shared-Courses/Course/Section/file
            c = out.setdefault(cp, {"sections": {}})
            c["sections"][parts[k + 2]] = c["sections"].get(parts[k + 2], 0) + 1
        elif len(parts) >= k + 5 and rules.TERM_RE.match(parts[k + 1]):
            cp = "/".join(parts[:k + 3])
            c = out.setdefault(cp, {"sections": {}})
            c["sections"][parts[k + 3]] = c["sections"].get(parts[k + 3], 0) + 1
        elif len(parts) == k + 4 and rules.TERM_RE.match(parts[k + 1]):  # files directly in a course folder
            out.setdefault("/".join(parts[:k + 3]), {"sections": {}})
    return out


def write_change(ops, source, tag):
    os.makedirs(CHANGES, exist_ok=True)
    cid = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + tag
    path, n = os.path.join(CHANGES, cid + ".json"), 1
    while os.path.exists(path):  # never overwrite a change set written in the same second
        n += 1
        cid = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + f"-{tag}-{n}"
        path = os.path.join(CHANGES, cid + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"id": cid, "created": now_iso(), "source": source, "ops": ops}, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    return path


# ── index.json for the website ─────────────────────────────
META_KEYS = ("author", "professor", "year", "credit", "source", "note", "url", "orig")
def build_tree(state):
    root = {"n": "", "d": [], "f": []}
    dirs = {"": root}

    def get_dir(path):
        if path in dirs:
            return dirs[path]
        parent, _, name = path.rpartition("/")
        node = {"n": name, "d": [], "f": []}
        get_dir(parent)["d"].append(node)
        dirs[path] = node
        return node

    for p in sorted(state, key=str.lower):
        e = state[p]
        parent, _, name = p.rpartition("/")
        row = [name, e.get("s", 0), e.get("t", 0)]
        meta = e.get("meta")
        if meta:
            row.append({k: v for k, v in meta.items() if k in META_KEYS and v})
        get_dir(parent)["f"].append(row)
    return root


def cmd_build(a):
    state = load_state()
    idx = {"name": "Sadaqah-e-Jariyah", "version": 1, "generated": now_iso(),
           "fileBase": a.file_base, "root": build_tree(state)}
    if a.api:
        idx["api"] = a.api.rstrip("/")
    if a.turnstile:
        idx["turnstile"] = a.turnstile
    out = a.out or os.path.join(REPO, "site", "index.json")
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(idx, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, out)
    print(f"{len(state):,} files → {out}")


# ── commands ───────────────────────────────────────────────
def cmd_import(a):
    if os.path.exists(BASE) and not a.force:
        sys.exit("catalog/base.jsonl already exists. Use `scan` to record new files (or --force to redo the import).")
    cache = HashCache()
    files = list(iter_local())
    total = sum(os.path.getsize(fp) for _, fp in files)
    done, t0 = 0, time.time()
    rows = []
    for i, (rel, fp) in enumerate(files, 1):
        s, m, h = cache.get(rel, fp)
        rows.append({"p": rel, "s": s, "h": h, "t": m})
        done += s
        if i % 25 == 0 or i == len(files):
            rate = done / max(time.time() - t0, 0.001) / 1e6
            print(f"\r  {i:,}/{len(files):,} files · {done / 1e9:.1f}/{total / 1e9:.1f} GB · {rate:.0f} MB/s", end="", flush=True)
    cache.save()
    os.makedirs(CAT, exist_ok=True)
    with open(BASE, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"\nWrote {len(rows):,} entries → {BASE}")


def diff_local(cache):
    state = load_state()
    local = {}
    for rel, fp in iter_local():
        local[rel] = cache.get(rel, fp)
    new, changed = [], []
    for rel, (s, m, h) in local.items():
        e = state.get(rel)
        if e is None:
            new.append((rel, s, m, h))
        elif e.get("h") != h:
            changed.append((rel, s, m, h))
    missing = sorted(p for p in state if p not in local)
    return state, new, changed, missing


def cmd_status(a):
    cache = HashCache()
    _, new, changed, missing = diff_local(cache)
    cache.save()
    print(f"new locally: {len(new)}   changed: {len(changed)}   in catalog but not here: {len(missing)}")
    for r in (new + changed)[:50]:
        print("  +", r[0])
    for p in missing[:50]:
        print("  ?", p)


def cmd_scan(a):
    cache = HashCache()
    state, new, changed, missing = diff_local(cache)
    cache.save()
    ops = [{"op": "add", "p": r, "s": s, "h": h, "t": m} for r, s, m, h in new + changed]
    if a.remove:
        ops += [{"op": "remove", "p": p, "reason": "deleted locally"} for p in missing]
    if not ops:
        print("Catalog already matches the local folder.")
        return
    path = write_change(ops, {"kind": "local", "by": a.by}, "local")
    print(f"{len(ops)} changes → {os.path.relpath(path, REPO)}")
    if missing and not a.remove:
        print(f"{len(missing)} catalog files aren't in your local folder (kept; pass --remove to drop them).")
    print("Next: upload the new files (see DEPLOY.md, 'Adding files yourself'), then commit catalog/.")


def cmd_check(a):
    bad = 0
    seen = set()
    for name in change_files():
        try:
            with open(os.path.join(CHANGES, name), encoding="utf-8") as fh:
                ch = json.load(fh)
            assert isinstance(ch.get("ops"), list), "ops missing"
            assert ch.get("id") not in seen, "duplicate id"
            seen.add(ch.get("id"))
            for op in ch["ops"]:
                assert op.get("op") in ("add", "move", "remove", "meta", "link"), f"unknown op {op.get('op')}"
                for key in ("p", "from", "to"):
                    if key in op:
                        v = op[key]
                        assert isinstance(v, str) and v and ".." not in v.split("/") and not v.startswith("/"), f"bad path {v!r}"
                        assert v.split("/")[0] not in EXCLUDE_TOP, f"path in reserved folder: {v}"
                if op["op"] == "add":
                    assert isinstance(op.get("s"), int) and len(op.get("h", "")) == 64, "add needs size and sha256"
                if op["op"] == "link":
                    assert str(op.get("url", "")).startswith("https://") and op["p"].endswith(".link"), "link needs an https url and a .link path"
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"✗ {name}: {e}")
    state = load_state()
    print(f"{len(change_files())} change files, {len(state):,} files in catalog, {bad} problems")
    sys.exit(1 if bad else 0)


def cmd_paths(a):
    """Print catalog paths (for `rclone copy --files-from`). --local-only: files with no 'staged' copy (added by hand)."""
    for p, e in sorted(load_state().items()):
        if a.local_only and e.get("staged"):
            continue
        print(p)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("paths"); p.add_argument("--local-only", action="store_true"); p.set_defaults(fn=cmd_paths)
    p = sub.add_parser("import"); p.add_argument("--force", action="store_true"); p.set_defaults(fn=cmd_import)
    p = sub.add_parser("scan"); p.add_argument("--remove", action="store_true"); p.add_argument("--by", default="maintainer"); p.set_defaults(fn=cmd_scan)
    p = sub.add_parser("status"); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("check"); p.set_defaults(fn=cmd_check)
    p = sub.add_parser("build")
    p.add_argument("--file-base", default="files/")
    p.add_argument("--api"); p.add_argument("--turnstile"); p.add_argument("--out")
    p.set_defaults(fn=cmd_build)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
