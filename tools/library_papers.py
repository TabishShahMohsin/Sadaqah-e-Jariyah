#!/usr/bin/env python3
"""
File the Engineering College Library's scanned exam papers into course folders.

The library posts one scanned PDF per (programme, semester, year) with every paper of that semester inside.
This reads the top of each page (OCR), finds where each paper starts and its course code, and writes each paper
to <course>/PYQs-and-Assignments/Sem-End_<YYYY-YY>[_<CODE>].pdf.

    python3 tools/library_papers.py ocr      # read page headers (resumable, stops after --budget seconds)
    python3 tools/library_papers.py plan     # decide where every paper goes  -> .sej-cache/library-plan.json + report
    python3 tools/library_papers.py apply    # write the papers (never overwrites; skips years a course already has)

Source PDFs: _Sources/Library-Papers/SEJPYQ_<BT|BE>_<prog>_S<sem>_<YYYY-YY>.pdf
"""
import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC = os.path.join(REPO, "_Sources", "Library-Papers")
OCR = os.path.join(REPO, ".sej-cache", "library-ocr.json")
PLAN = os.path.join(REPO, ".sej-cache", "library-plan.json")
REPORT = os.path.join(REPO, ".sej-cache", "library-report.txt")
UMB = "Faculty-of-Engineering-and-Technology/BTech-and-BE"

PROG_BRANCH = {"AI": "Computer-Engineering", "CoE": "Computer-Engineering", "Auto": "Mechanical-Engineering",
               "ME": "Mechanical-Engineering", "ChE": "Chemical-Engineering", "CE": "Civil-Engineering",
               "EE": "Electrical-Engineering", "ECE": "Electronics-Engineering", "VLSI": "Electronics-Engineering",
               "FT": "Food-Technology", "PK": "Chemical-Technology", "FY": "First-Year-Common",
               "OE-Odd": "Shared-Courses", "OE-Even": "Shared-Courses"}


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as fh:
        json.dump(data, fh)
    os.replace(path + ".tmp", path)


def load(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


# ── 1. OCR the top of every page ───────────────────────────
def tesseract(png):
    try:
        env = dict(os.environ, OMP_THREAD_LIMIT="1")           # one thread per tesseract; we run several at once
        return subprocess.run(["tesseract", png, "stdout", "--psm", "6"], capture_output=True, text=True, timeout=90, env=env).stdout
    except subprocess.TimeoutExpired:
        return ""
    finally:
        os.unlink(png)


def cmd_ocr(a):
    # pdfium is not thread/fork safe: render pages here, one at a time, and let threads run tesseract
    import pypdfium2 as pdfium
    from concurrent.futures import ThreadPoolExecutor
    data = load(OCR, {})
    t0, done = time.time(), 0
    tmp = tempfile.mkdtemp(prefix="sej-ocr-")
    with ThreadPoolExecutor(a.workers) as ex:
        pending = []
        def drain(limit):
            nonlocal done
            while len(pending) > limit:
                f, i, fu = pending.pop(0)
                data[f]["pages"][str(i)] = fu.result()
                done += 1
                if done % 50 == 0:
                    save(OCR, data)
        for f in sorted(os.listdir(SRC)):
            if not f.endswith(".pdf") or time.time() - t0 > a.budget:
                continue
            try:
                doc = pdfium.PdfDocument(os.path.join(SRC, f))
            except Exception as e:  # noqa: BLE001
                data[f] = {"error": str(e)}
                continue
            entry = data.setdefault(f, {"n": len(doc), "pages": {}})
            entry["n"] = len(doc)
            for i in range(len(doc)):
                if str(i) in entry["pages"] or time.time() - t0 > a.budget:
                    continue
                page = doc[i]
                w, h = page.get_size()
                img = page.render(scale=1250 / max(w, 1), crop=(0, h * 0.68, 0, 0)).to_pil().convert("L")
                png = os.path.join(tmp, f"{len(pending)}-{i}-{f}.png")
                img.save(png)
                pending.append((f, i, ex.submit(tesseract, png)))
                drain(a.workers * 2)
            doc.close()
        drain(0)
    save(OCR, data)
    left = sum(v.get("n", 0) - len(v.get("pages", {})) for v in data.values() if "n" in v)
    unseen = sum(1 for f in os.listdir(SRC) if f.endswith(".pdf") and f not in data)
    print(f"read {done} page headers this run; {left} pages left in started files, {unseen} files not started"
          + ("  → run again" if left or unseen else "  → done, now run: plan"))


# ── 2. where does each paper go ────────────────────────────
CODE = re.compile(r"(?<![A-Za-z0-9])(E?[A-Z]{2,4})\s?[-.]?\s?([0-9OIlSB]{3,4})(?![0-9])")
START = re.compile(r"(?i)(course\s*(no|code|number|title)|max(imum)?\.?\s*marks|time\s*[:\-]?\s*\d|full\s*marks|examination|exam\s*\d{4}|semester\s*(end|exam)|instructions)")


def fix_code(letters, digits):
    letters = letters.replace("0", "O").upper()
    digits = digits.translate(str.maketrans("OIlSB", "01158"))
    return letters + digits


def course_index():
    """every code we know -> the course folder that should receive its papers"""
    idx = {}
    pg = load(os.path.join(REPO, "catalog", "programmes.json"), {})
    for path, c in pg.get("courses", {}).items():
        for code in c.get("codes", []) + c.get("be", []) + c.get("old", []):
            idx.setdefault(code, path)
    cre = re.compile(r"^((?:[A-Z]{2,4}\d{3,4})(?:-[A-Z]{2,4}\d{3,4})*)_")
    for dirpath, dirnames, _ in os.walk(os.path.join(REPO, UMB)):
        for d in dirnames:
            m = cre.match(d)
            if m:
                rel = os.path.relpath(os.path.join(dirpath, d), REPO).replace(os.sep, "/")
                for code in m.group(1).split("-"):
                    idx.setdefault(code, rel)
    return idx


NOISE = re.compile(r"(?i)examination|semester|session|maximum|marks|credits|duration|time|b\.?\s?tech|b\.?\s?e\.|autumn|winter|spring|page|^\(?\s*[A-Z]+\s+ENGINEERING\s*\)?$|^\(.*\)$")


def title_near(text, code):
    """the course title is printed on the line just above the code (or on the code's line)"""
    lines = [l.strip(" |*/:;.,'\"-") for l in text.splitlines()]
    for k, l in enumerate(lines):
        flat = re.sub(r"[\s\-.]", "", l).upper()
        if code[-6:].replace(" ", "") in flat or code in flat:
            same = re.sub(r"(?<![A-Za-z])E?[A-Z]{2,4}\s?[-.]?\s?\d{3,4}.*$", "", l).strip(" -:,")
            for cand in ([same] if len(same) > 6 else []) + [lines[j] for j in (k - 1, k - 2) if j >= 0]:
                c = re.sub(r"[^A-Za-z&,()\- ]", " ", cand)
                c = re.sub(r"\s+", " ", c).strip(" -,")
                letters = [ch for ch in c if ch.isalpha()]
                if len(letters) >= 6 and sum(ch.isupper() for ch in letters) / len(letters) > .7 and not NOISE.search(c):
                    return c.title().replace(" And ", " and ").replace(" Of ", " of ").replace(" In ", " in ")
    return ""


ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "1": "1", "2": "2", "3": "3", "4": "4", "il": "2", "ll": "2", "lil": "3", "iil": "3"}


def norm_title(t):
    """lower-case words without filler; a trailing part number (I, II, -1…) is kept as one token: '#2'"""
    t = t.lower().replace("&", " and ")
    t = re.sub(r"\blab(oratory)?\b", "lab", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    ws = [w for w in t.split() if w not in ("and", "of", "the", "old", "new")]
    if ws and ws[-1] in ROMAN:
        ws[-1] = "#" + ROMAN[ws[-1]]
    elif ws and len(ws[-1]) == 1:                     # an unreadable part number ("H" for "II"): mark it unknown
        ws[-1] = "#?"
    return " ".join(ws)


def same_course(a, b):
    """fuzzy title match that never confuses part I with part II"""
    pa = a.split()[-1] if a.split() and a.split()[-1].startswith("#") else ""
    pb = b.split()[-1] if b.split() and b.split()[-1].startswith("#") else ""
    if pa != pb or "#?" in (pa, pb):
        return False
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.88


def title_index():
    """(branch, normalised title) -> course folder, for courses in the current curricula"""
    out = {}
    pg = load(os.path.join(REPO, "catalog", "programmes.json"), {})
    for path, c in pg.get("courses", {}).items():
        br = path.split("/")[1]
        out.setdefault((br, norm_title(c.get("title", ""))), path)
    return out


def resolve(code, idx):
    """code as printed on the paper -> (folder, how)"""
    bases = [(code, "exact")]
    if code.startswith("E") and len(code) >= 8:
        bases.append((code[1:], "be"))                                     # B.E. paper of a B.Tech course
    for base, how in bases:
        if base in idx:
            return idx[base], how
    for base, how in bases:
        for alt in (base[:-1] + "2", base[:-1] + "0"):                   # 2017/2021 scheme <-> 2023 scheme
            if alt in idx:
                return idx[alt], "scheme" if how == "exact" else "be+scheme"
    return None, None


def cmd_plan(a):
    data = load(OCR, {})
    idx = course_index()
    tidx = title_index()
    plan, report = [], []
    for f, v in sorted(data.items()):
        if "pages" not in v:
            report.append(f"SKIP {f}: {v.get('error')}")
            continue
        m = re.match(r"SEJPYQ_(BT|BE)_(.+?)_S(\d)_(\d{4}-\d{2}|unknown)", f)
        if not m:
            continue
        deg, prog, sem, year = m.groups()
        n = v["n"]
        heads = [v["pages"].get(str(i), "") for i in range(n)]
        starts = []
        for i, t in enumerate(heads):
            codes = [fix_code(*c) for c in CODE.findall(t)]
            codes = [c for c in codes if not re.fullmatch(r"(MAX|TIME|PAGE|NOTE|SEM|PART|UNIT)\d+", c)]
            if START.search(t) and codes:
                starts.append((i, codes))
            elif i == 0:
                starts.append((0, codes))
        papers = []
        for k, (i, codes) in enumerate(starts):
            end = starts[k + 1][0] - 1 if k + 1 < len(starts) else n - 1
            papers.append((i, end, codes))
        for i, end, codes in papers:
            dest, how, code = None, None, None
            for c in codes:
                dest, how = resolve(c, idx)
                if dest:
                    code = c
                    break
            branch = PROG_BRANCH.get(prog, "Shared-Courses")
            title = title_near(heads[i], codes[0]) if codes else ""
            if codes and re.match(r"E?AR", codes[0]):              # B.Arch papers bound into the first-year files
                continue
            if not dest and title:                                   # older codes: match the printed title
                for br in (branch, "Shared-Courses", "First-Year-Common"):
                    lab = "lab" in norm_title(title).split()
                    keys = [k[1] for k in tidx if k[0] == br and ("lab" in k[1].split()) == lab]   # theory never matches a lab
                    nt = norm_title(title)
                    hit = [k for k in keys if same_course(nt, k)]
                    if hit:
                        hit.sort(key=lambda k: -difflib.SequenceMatcher(None, nt, k).ratio())
                        dest, how, code = tidx[(br, hit[0])], "title", codes[0]
                        break
            if dest:
                primary = dest.rsplit("/", 1)[-1].split("_")[0].split("-")
                suffix = "" if code in primary else f"_{code}"
                out = f"{dest}/PYQs-and-Assignments/Sem-End_{year}{suffix}.pdf"
            elif codes and title:                                    # a course from an older scheme: its own folder
                old = f"{codes[0]}_{re.sub(r'[^A-Za-z0-9]+', '-', title).strip('-')[:70]}"
                out = f"{UMB}/{branch}/_Old-Syllabus-Subjects/{old}/PYQs-and-Assignments/Sem-End_{year}.pdf"
                how = "old-course"
            else:
                label = f"{deg}_{prog}_S{sem}_{year}"
                guess = codes[0] if codes else "unread"
                out = f"{UMB}/{branch}/_Library-Papers-Unsorted/{label}_{guess}_p{i + 1}-{end + 1}.pdf"
            plan.append({"src": f, "pages": [i, end], "dst": out, "code": code or (codes[0] if codes else None), "title": title, "how": how or "unsorted", "year": year})
    # one folder per old code, named with the title most papers agree on
    JUNK = re.compile(r"(?i)course|department|faculty|university|engineering$|^[a-z]{1,3}$")
    votes = {}
    for p in plan:
        if p["how"] == "old-course" and p["title"] and not JUNK.search(p["title"]):
            key = (p["dst"].split("/")[1], p["code"])
            votes.setdefault(key, []).append(p["title"])
    for p in plan:
        if p["how"] != "old-course":
            continue
        br = p["dst"].split("/")[1]
        ts = votes.get((br, p["code"]))
        best = max(set(ts), key=ts.count) if ts else p["title"]
        name = f"{p['code']}_{re.sub(r'[^A-Za-z0-9]+', '-', best).strip('-')[:70]}"
        p["dst"] = f"{UMB}/{br}/_Old-Syllabus-Subjects/{name}/PYQs-and-Assignments/Sem-End_{p['year']}.pdf"
    # skip years a course already has from earlier collections
    for p in plan:
        if p["how"] == "unsorted":
            continue
        folder = os.path.join(REPO, os.path.dirname(p["dst"]))
        have = os.listdir(folder) if os.path.isdir(folder) else []
        stem = os.path.basename(p["dst"])[:-4]
        if any(h.startswith(f"Sem-End_{p['year']}") and (h[:-4] == stem or "_" not in stem[len('Sem-End_' + p['year']):]) for h in have):
            p["skip"] = "course already has this year's paper"
    # two papers landing on one name -> _2, _3
    seen = {}
    for p in plan:
        if p.get("skip"):
            continue
        d = p["dst"]
        if d in seen:
            seen[d] += 1
            p["dst"] = d[:-4] + f"_{seen[d]}.pdf"
        else:
            seen[d] = 1
    save(PLAN, plan)
    from collections import Counter
    c = Counter(("skip" if p.get("skip") else p["how"]) for p in plan)
    with open(REPORT, "w") as fh:
        fh.write("\n".join(report) + "\n")
        for p in plan:
            fh.write(f"{p.get('skip') or p['how']:10} {p['src']} p{p['pages'][0] + 1}-{p['pages'][1] + 1}  {p['code']}  -> {p['dst']}\n")
    print(f"{len(plan)} papers found: " + ", ".join(f"{k} {v}" for k, v in c.most_common()))
    print(f"details: {os.path.relpath(REPORT, REPO)}")


def cmd_apply(a):
    from pypdf import PdfReader, PdfWriter
    plan = load(PLAN, [])
    readers, made = {}, 0
    for p in plan:
        if p.get("skip"):
            continue
        dst = os.path.join(REPO, p["dst"])
        if os.path.exists(dst):
            continue
        r = readers.get(p["src"]) or readers.setdefault(p["src"], PdfReader(os.path.join(SRC, p["src"])))
        w = PdfWriter()
        for i in range(p["pages"][0], p["pages"][1] + 1):
            w.add_page(r.pages[i])
        w.add_metadata({"/Title": os.path.basename(dst)[:-4], "/Author": "", "/Creator": "", "/Producer": ""})
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as fh:
            w.write(fh)
        made += 1
        if len(readers) > 6:
            readers.clear()
    print(f"wrote {made} papers")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["ocr", "plan", "apply"])
    ap.add_argument("--budget", type=float, default=150, help="seconds per ocr run")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    a = ap.parse_args()
    {"ocr": cmd_ocr, "plan": cmd_plan, "apply": cmd_apply}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
