#!/usr/bin/env python3
"""
Merge duplicate course folders into one and fix folder titles.

    python3 tools/merge_courses.py PLAN.json            # dry run: prints what would change
    python3 tools/merge_courses.py PLAN.json --apply    # do it

PLAN is a JSON list of {"to": <folder>, "from": [[<folder>, <kind>, <code>], ...], "why": <note>}, folders relative
to the umbrella (BTech-and-BE/). <kind> says how files from that folder are named in the merged one:

    ""      same course and code (a misread folder name)  files keep their names
    "be"    the B.E. twin                                 _<code> is added before the extension
    "old"   an older code of the same course              _<code> is added; <code>_Syllabus_* → <code>_Old-Syllabus_*
    "alt"   the same course under another current code    _<code> is added (e.g. MEC2912 + AEC2912 for ME and Auto)

Never deletes anything. A file identical to one already in the target (same SHA-256) is moved to
.sej-cache/merge-duplicates/ instead, and emptied folders go to .sej-cache/merged-folders/. Every move is recorded in
the archive catalog as one change set (catalog/changes/), the hash cache follows the files, and
catalog/programmes.json follows renamed course folders (older and B.E. codes are added to the course's entry).
Safe to run again: merges already done are skipped.
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import sej  # noqa: E402

U = "BTech-and-BE"
CACHE_DIR = os.path.join(REPO, ".sej-cache")
PROGS = os.path.join(REPO, "catalog", "programmes.json")
SPARE = re.compile(r"_[2-9]$")


def merged_name(fn, kind, code):
    if kind not in ("be", "old", "alt") or not code:
        return fn
    stem, ext = os.path.splitext(fn)
    if code in stem:
        if kind == "old" and stem.startswith(code + "_Syllabus"):
            return code + "_Old-Syllabus" + stem[len(code) + len("_Syllabus"):] + ext
        return fn
    return SPARE.sub("", stem) + "_" + code + ext


def free_path(dst, src_hash, hashes):
    """Where a file may go in the target: (path, None), or (None, existing) if an identical file is already there."""
    stem, ext = os.path.splitext(dst)
    stem = SPARE.sub("", stem)
    cand, n = dst, 1
    while os.path.exists(cand):
        if hashes.get(cand) == src_hash:
            return None, cand
        n += 1
        cand = f"{stem}_{n}{ext}"
    return cand, None


def files_under(d):
    for dp, dns, fns in os.walk(d):
        dns.sort()
        for f in sorted(fns):
            if not f.startswith("."):
                yield os.path.join(dp, f)


def course_codes(folder):
    m = re.match(r"^([A-Z0-9-]+?)_", os.path.basename(folder))
    return m.group(1).split("-") if m else []


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    plan = json.load(open(a.plan))
    rel = lambda p: os.path.relpath(p, REPO)  # noqa: E731
    cache = sej.HashCache()

    def h(fp):
        return cache.get(rel(fp), fp)[2]

    ops, log, renamed = [], [], {}
    moved = dups = parked = merges = 0
    for m in plan:
        tgt = os.path.join(REPO, U, m["to"])
        srcs = [(os.path.join(REPO, U, s), k, c) for s, k, c in m["from"]]
        live = [s for s in srcs if os.path.isdir(s[0]) and os.path.normpath(s[0]) != os.path.normpath(tgt)]
        # a case-only rename (Rf → RF): on a case-insensitive disk the target *is* the source, so rename the folder itself
        for s_ in list(live):
            if os.path.isdir(tgt) and os.path.samefile(s_[0], tgt) and not s_[1]:
                live.remove(s_)
                merges += 1
                log.append(f"{m['to']}  ←  {os.path.basename(s_[0])} (renamed)")
                for fp in files_under(s_[0]):
                    ops.append({"op": "move", "from": rel(fp), "to": rel(os.path.join(tgt, os.path.relpath(fp, s_[0])))})
                renamed[f"{U}/{os.path.relpath(s_[0], os.path.join(REPO, U))}"] = (f"{U}/{m['to']}", "", "")
                if a.apply:
                    tmp = s_[0] + ".renaming"
                    os.rename(s_[0], tmp)
                    os.rename(tmp, tgt)
                    for k in [k for k in cache.d if k.startswith(rel(s_[0]) + "/")]:
                        cache.d[rel(tgt) + k[len(rel(s_[0])):]] = cache.d.pop(k)
        if not live:
            continue
        merges += 1
        log.append(f"{m['to']}  ←  " + ", ".join(os.path.basename(s[0]) + (f" ({s[1]})" if s[1] else "") for s in live))
        for src, kind, code in live:
            renamed[f"{U}/{os.path.relpath(src, os.path.join(REPO, U))}"] = (f"{U}/{m['to']}", kind, code)
            for fp in list(files_under(src)):
                sub = os.path.relpath(os.path.dirname(fp), src)
                dst = os.path.normpath(os.path.join(tgt, sub, merged_name(os.path.basename(fp), kind, code)))
                fh = h(fp)
                existing = {}
                if os.path.isdir(os.path.dirname(dst)):
                    for e in os.listdir(os.path.dirname(dst)):
                        ep = os.path.join(os.path.dirname(dst), e)
                        if os.path.isfile(ep) and not os.path.samefile(ep, fp) and os.path.splitext(e)[1] == os.path.splitext(dst)[1]:
                            existing[ep] = h(ep)
                if fh in existing.values():
                    dup_of = next(p for p, v in existing.items() if v == fh)
                    spare = os.path.join(CACHE_DIR, "merge-duplicates", rel(fp))
                    log.append(f"    same file already there: {os.path.basename(fp)} = {os.path.basename(dup_of)}")
                    dups += 1
                    ops.append({"op": "remove", "p": rel(fp), "reason": f"duplicate of {rel(dup_of)}"})
                    if a.apply:
                        os.makedirs(os.path.dirname(spare), exist_ok=True)
                        os.rename(fp, spare)
                        cache.d.pop(rel(fp), None)
                    continue
                dst, _ = free_path(dst, fh, {})
                moved += 1
                ops.append({"op": "move", "from": rel(fp), "to": rel(dst)})
                if a.apply:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    os.rename(fp, dst)
                    hit = cache.d.pop(rel(fp), None)
                    if hit:
                        cache.d[rel(dst)] = hit
            if a.apply:
                os.makedirs(tgt, exist_ok=True)  # a folder that held only empty sections still lands in the target
                for dp, dns, _ in os.walk(src):
                    for d in dns:
                        os.makedirs(os.path.join(tgt, os.path.relpath(os.path.join(dp, d), src)), exist_ok=True)
                if not any(True for _ in files_under(src)):
                    park = os.path.join(CACHE_DIR, "merged-folders", rel(src))
                    while os.path.exists(park):
                        park += "_"
                    os.makedirs(os.path.dirname(park), exist_ok=True)
                    os.rename(src, park)
                    parked += 1

    # programmes.json: course entries follow their folders; older and B.E. codes are kept for search
    progs_changed = 0
    if renamed and os.path.exists(PROGS):
        P = json.load(open(PROGS))
        C = P.get("courses", {})
        for old_p, (new_p, kind, code) in renamed.items():
            e = C.pop(old_p, None)
            t = C.get(new_p)
            if e is None and t is None:
                continue
            base = t or e
            if e is not None and t is not None:
                for k in ("cr", "ltp", "kind"):
                    base[k] = base.get(k) or e.get(k)
                base["progs"] = {**(e.get("progs") or {}), **(base.get("progs") or {})}
                for k in ("be", "old"):
                    base[k] = list(dict.fromkeys((base.get(k) or []) + (e.get(k) or [])))
            base.setdefault("be", [])
            base.setdefault("old", [])
            if kind == "be" and code and code not in base["be"]:
                base["be"].append(code)
            if kind == "old" and code and code not in base["old"]:
                base["old"].append(code)
            if e is not None and kind == "old":
                base["old"] += [c for c in e.get("codes", []) if c not in base["old"] and c not in course_codes(new_p)]
            base["codes"] = [c for c in course_codes(new_p) if c not in base["be"]]
            base["title"] = re.sub(r"^[A-Z0-9-]+?_", "", os.path.basename(new_p)).replace("-", " ")
            C[new_p] = base
            progs_changed += 1
        for prog in P.get("programmes", []):
            for sem in prog.get("semesters", {}).values():
                for c in sem:
                    if c.get("p") in renamed:
                        c["p"] = renamed[c["p"]][0]
                        progs_changed += 1
        if a.apply and progs_changed:
            tmp = PROGS + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(P, fh, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, PROGS)

    change = None
    if a.apply:
        cache.save()
        if ops:
            change = sej.write_change(ops, {"kind": "local", "by": "maintainer", "note": "merge duplicate course folders"}, "merge")
    print("\n".join(log))
    print(f"\n{'APPLIED' if a.apply else 'DRY RUN'}: {merges} merges, "
          f"{moved} files moved, {dups} identical copies set aside, {parked} emptied folders parked, "
          f"{progs_changed} programmes.json updates" + (f", catalog change {rel(change)}" if change else ""))


if __name__ == "__main__":
    sys.exit(main())
