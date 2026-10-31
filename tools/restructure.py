#!/usr/bin/env python3
"""
One-time restructure into the B.Tech & B.E. layout (plan made from AMU's official curricula).

    python3 tools/restructure.py              # dry run: prints what would change
    python3 tools/restructure.py --apply      # do it

Reads tools/restructure_plan.json. Moves existing branches under the umbrella folder, relocates the few
course folders the plan places elsewhere, creates the new course folders (with their section folders),
cuts each course's own pages out of the official syllabus PDFs, and copies the official PDFs into each
branch's _Curriculum/Official/. Never deletes anything. Safe to run again: finished steps are skipped.
"""
import argparse
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC = os.path.join(REPO, "_Sources", "AMU-Official")
CACHE = os.path.join(REPO, ".sej-cache", "hashes.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--plan", default=os.path.join(HERE, "restructure_plan.json"))
    a = ap.parse_args()
    plan = json.load(open(a.plan))
    U = plan["umbrella"]
    J = lambda *p: os.path.join(REPO, *p)  # noqa: E731
    log = []
    renames = {}  # old rel prefix -> new rel prefix (for the hash cache)

    # 1. existing branches go under the umbrella
    for top in plan["tops"]:
        if os.path.isdir(J(top)) and not os.path.exists(J(U, top)):
            log.append(f"move  {top}/  ->  {U}/{top}/")
            renames[top + "/"] = f"{U}/{top}/"
            if a.apply:
                os.makedirs(J(U), exist_ok=True)
                os.rename(J(top), J(U, top))

    # 2. the few course folders that move to a better place
    for src, dst in plan["relocate"].items():
        s = f"{U}/{src}"
        if dst == s:
            continue
        here = J(s) if (a.apply or not renames) else J(src)
        if os.path.isdir(here) and not os.path.exists(J(dst)):
            log.append(f"move  {src}  ->  {dst}")
            renames[s + "/"] = dst + "/"
            if a.apply:
                os.makedirs(os.path.dirname(J(dst)), exist_ok=True)
                os.rename(J(s), J(dst))

    # 3. new course folders
    made = 0
    for c in plan["create"]:
        if not os.path.isdir(J(c["path"])):
            made += 1
            if a.apply:
                for sec in c["sections"]:
                    os.makedirs(J(c["path"], sec), exist_ok=True)
    log.append(f"create {made} new course folders (with their section folders)")

    # 4. each course's own syllabus pages
    cut = 0
    if a.apply:
        from pypdf import PdfReader, PdfWriter
        readers = {}
    for c in plan["cuts"]:
        dst = J(c["dst"])
        if os.path.exists(dst) or not os.path.exists(os.path.join(SRC, c["src"])):
            continue
        cut += 1
        if a.apply:
            r = readers.setdefault(c["src"], PdfReader(os.path.join(SRC, c["src"])))
            w = PdfWriter()
            for p in c["pages"]:
                if 1 <= p <= len(r.pages):
                    w.add_page(r.pages[p - 1])
            w.add_metadata({"/Title": os.path.basename(dst)[:-4].replace("_", " "), "/Author": "", "/Creator": "", "/Producer": ""})
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as fh:
                w.write(fh)
    log.append(f"cut   {cut} course syllabus files out of the official syllabi")

    # 5. official PDFs per branch
    cp = 0
    for o in plan["official"]:
        if os.path.exists(J(o["dst"])) or not os.path.exists(os.path.join(SRC, o["src"])):
            continue
        cp += 1
        if a.apply:
            os.makedirs(os.path.dirname(J(o["dst"])), exist_ok=True)
            shutil.copyfile(os.path.join(SRC, o["src"]), J(o["dst"]))
    log.append(f"copy  {cp} official curriculum/syllabus PDFs into _Curriculum/Official/")

    # 6. keep the hash cache valid for moved files (avoids re-hashing ~20 GB)
    if a.apply and renames and os.path.exists(CACHE):
        d = json.load(open(CACHE))
        out = {}
        for k, v in d.items():
            nk = k
            for old, new in sorted(renames.items(), key=lambda x: -len(x[0])):
                if nk.startswith(old):
                    nk = new + nk[len(old):]
            out[nk] = v
        json.dump(out, open(CACHE, "w"))
        log.append(f"cache {len(out):,} hashes carried over to the new paths")

    print(("APPLIED" if a.apply else "DRY RUN") + "\n  " + "\n  ".join(log))


if __name__ == "__main__":
    sys.exit(main())
