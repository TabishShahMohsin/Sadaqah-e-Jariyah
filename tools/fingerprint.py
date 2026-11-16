#!/usr/bin/env python3
"""
Fingerprints of every archive file (PDFs and images), so the sorter can spot re-scans and re-exports of material the
archive already has, even on GitHub where the files themselves aren't present.

    python3 tools/fingerprint.py            # adds what's missing; safe to stop and run again
    python3 tools/fingerprint.py --budget 150   # stop after ~150 s (for short sessions)
    python3 tools/fingerprint.py --prefix Faculty-of-Engineering-and-Technology/BTech-and-BE/Mechanical-Engineering/Sem4   # one part of the archive

Writes catalog/fingerprints.jsonl.gz: per file {p, h, n (pages), tsig (text hash), mh (64-number text MinHash),
vsig (24×32 first-page thumbnail)}. About 1.5 KB per file. Commit it with the catalog.
"""
import argparse
import gzip
import json
import os
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import sej  # noqa: E402
from sorter import courses, evidence  # noqa: E402

OUT = os.path.join(REPO, courses.FP_FILE)


def one(p, h):
    it = {"path": os.path.join(REPO, p), "ext": p.rsplit(".", 1)[-1].lower()}
    try:
        return courses.record(p, evidence.inspect(it), h)
    except Exception as e:  # noqa: BLE001 — a damaged file just gets no fingerprint
        print(f"  skipped {p}: {type(e).__name__}")
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--budget", type=float, default=0, help="seconds to work before saving and stopping (0 = until done)")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1), help="files processed in parallel")
    ap.add_argument("--prefix", default="", help="only files under this archive path (e.g. one branch or course)")
    a = ap.parse_args()
    state = sej.load_state()
    have = {}
    if os.path.exists(OUT):
        with gzip.open(OUT, "rt", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    r = json.loads(line)
                    if r["p"] in state and r.get("h") == state[r["p"]].get("h"):
                        have[r["p"]] = r
    todo = [p for p, e in sorted(state.items()) if p not in have and not e.get("link") and p.startswith(a.prefix)
            and p.rsplit(".", 1)[-1].lower() in ("pdf", "jpg", "jpeg", "png") and os.path.exists(os.path.join(REPO, p))]
    t0, done = time.time(), 0
    print(f"{len(have)} fingerprinted, {len(todo)} to go ({a.jobs} at a time)")
    with ProcessPoolExecutor(a.jobs) as pool:
        futs = {}
        it = iter(todo)
        for p in it:  # keep the pool busy without queueing work past the time budget
            futs[pool.submit(one, p, state[p].get("h"))] = p
            if len(futs) >= a.jobs * 2:
                break
        while futs:
            fin, _ = wait(futs, return_when=FIRST_COMPLETED)
            for f in fin:
                p = futs.pop(f)
                r = f.result()
                if r:
                    have[p] = r
                    done += 1
                    if done % 200 == 0:
                        print(f"  {done} done ({time.time() - t0:.0f}s)")
                if not (a.budget and time.time() - t0 > a.budget):
                    nxt = next(it, None)
                    if nxt:
                        futs[pool.submit(one, nxt, state[nxt].get("h"))] = nxt
    tmp = OUT + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        for p in sorted(have):
            fh.write(json.dumps(have[p], separators=(",", ":")) + "\n")
    os.replace(tmp, OUT)
    left = len([p for p in todo if p not in have])
    print(f"{len(have)} fingerprinted → {os.path.relpath(OUT, REPO)} ({os.path.getsize(OUT) / 1e6:.1f} MB)" + (f"; {left} left, run again" if left else ""))
    return 3 if left else 0


if __name__ == "__main__":
    sys.exit(main())
