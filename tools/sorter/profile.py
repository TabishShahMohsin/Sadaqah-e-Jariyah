"""Free (no-AI) profile of a file: what kind it is, how big, whether it is handwritten, and who is named on it.

    kind:   slides | typed | scan-printed | scan-handwritten | photo | video | other
    people: names found on the first pages ("Prof. M. M. Sufyan Beg", "Rashid Sir" -> "Rashid")

Signals: page shape (landscape = slides), text per page (none = scan), full-page images, the app that made the PDF
(PowerPoint, Adobe Scan, CamScanner/intsig), and Tesseract's confidence on page 1 of a scan (handwriting reads at
20-45, print at 70-95). Everything here is cheap enough to run on the whole archive."""
import os
import re
import statistics
import subprocess
import tempfile

TITLE = r"(?:Prof(?:essor)?\.?|Dr\.?|Mr\.?|Ms\.?|Mrs\.?|Er\.?)"
NAME = r"[A-Z][A-Za-z.'-]+(?:[ \t]+(?:[A-Z][A-Za-z.'-]*|ul|al|ud|bin))*"
PEOPLE = [re.compile(rf"\b{TITLE}[ \t]*{NAME}"),
          re.compile(rf"\b({NAME})[ \t]+(?:Sir|Sahab|Ma'?am|Madam)\b"),
          re.compile(rf"(?i:presented by|prepared by|instructor|course coordinator|faculty|teacher)[ \t]*[:\-]?[ \t]*({TITLE}[ \t]*{NAME})")]
TAIL = re.compile(r"[ \t]+(Department|Dept|Associate|Assistant|Professor|Faculty|Lecturer|AMU|ZHCET|Aligarh|Civil|Mechanical|"
                  r"Electrical|Electronics|Computer|Chemical|Engg|Engineering)\b.*$")
NOT_NAMES = {"partial", "institutions", "reference", "references", "chapter", "unit", "lecture", "figure", "table"}
SCAN_APPS = re.compile(r"camscanner|intsig|adobe scan|scanner|genius scan|microsoft lens|office lens|tapscanner|notebloc|image conversion", re.I)
SLIDE_APPS = re.compile(r"powerpoint|impress|keynote|google slides", re.I)


def _run(*a, timeout=60):
    try:
        return subprocess.run(a, capture_output=True, text=True, timeout=timeout).stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def people_in(text):
    found = []
    for rx in PEOPLE:
        for m in rx.finditer(text or ""):
            s = re.sub(r"\s+", " ", m.group(1) if rx.groups else m.group(0)).strip(" .,:;-")
            s = TAIL.sub("", s).strip(" .,:;-")
            bare = re.sub(rf"^{TITLE}\s*", "", s).strip()
            if len(bare) < 3 or len(s) > 45 or len(s.split()) > 6 or bare.lower() in NOT_NAMES:
                continue
            if s not in found:
                found.append(s)
    return found[:5]


def ocr_page1(path, ext):
    """(text, mean confidence 0-100, words) of page 1, or ("", None, 0)."""
    with tempfile.TemporaryDirectory() as d:
        img = path
        if ext == "pdf":
            _run("pdftoppm", "-f", "1", "-l", "1", "-r", "150", "-gray", "-png", path, os.path.join(d, "p"))
            pngs = [os.path.join(d, f) for f in os.listdir(d)]
            if not pngs:
                return "", None, 0
            img = pngs[0]
        tsv = _run("tesseract", img, "-", "--psm", "3", "tsv").splitlines()[1:]
    words, confs = [], []
    for line in tsv:
        c = line.split("\t")
        if len(c) > 11 and c[11].strip():
            try:
                cf = float(c[10])
            except ValueError:
                continue
            if cf >= 0:
                words.append(c[11])
                confs.append(cf)
    return " ".join(words), (round(statistics.mean(confs)) if confs else None), len(words)


def profile(path, ext=None):
    ext = (ext or path.rsplit(".", 1)[-1]).lower()
    r = {"size_mb": round(os.path.getsize(path) / 1e6, 2)}
    if ext in ("mp4", "mkv", "mov", "webm"):
        r["kind"] = "video"
        return r
    if ext in ("jpg", "jpeg", "png", "webp"):
        text, conf, n = ocr_page1(path, ext)
        r.update(kind="photo", pages=1, ocr_conf=conf, ocr_words=n)
        r["handwritten"] = bool(conf is not None and n >= 15 and conf < 55)
        r["people"] = people_in(text) if conf and conf >= 60 else []
        return r
    if ext != "pdf":
        r["kind"] = "other"
        return r
    info = {}
    for line in _run("pdfinfo", path).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()
    pages = int(info.get("Pages", "0") or 0)
    m = re.search(r"([\d.]+) x ([\d.]+)", info.get("Page size", ""))
    aspect = round(float(m[1]) / float(m[2]), 2) if m and float(m[2]) else None
    made = " | ".join(x for x in (info.get("Creator", ""), info.get("Producer", "")) if x)[:80]
    n = max(1, min(pages, 5))
    text = _run("pdftotext", "-l", str(n), path, "-")
    cpp = len(re.sub(r"\s", "", text)) // n
    big = 0
    for line in _run("pdfimages", "-l", str(n), "-list", path).splitlines()[2:]:
        c = line.split()
        if len(c) > 4 and c[3].isdigit() and c[4].isdigit() and int(c[3]) * int(c[4]) > 1_500_000:
            big += 1
    r.update(pages=pages, aspect=aspect, made_with=made, chars_per_page=cpp, full_page_images=round(big / n, 2))
    landscape = bool(aspect and aspect > 1.2)
    scanned = cpp < 40 or SCAN_APPS.search(made) or (big / n >= 0.8 and cpp < 200)
    if scanned and not (landscape and SLIDE_APPS.search(made)):
        ocr, conf, words = ocr_page1(path, "pdf")
        r.update(ocr_conf=conf, ocr_words=words)
        if landscape and not SCAN_APPS.search(made) and (conf or 0) >= 60:
            r["kind"] = "slides"          # slides exported as pictures
        elif conf is not None and words >= 15 and conf < 55:
            r["kind"] = "scan-handwritten"
        elif conf is None or words < 15:
            r["kind"] = "scan"            # mostly a drawing or blank: can't tell
        else:
            r["kind"] = "scan-printed"
        r["people"] = people_in(ocr) if (conf or 0) >= 60 else []
    else:
        r["kind"] = "slides" if (landscape or SLIDE_APPS.search(made)) else "typed"
        r["people"] = people_in(_run("pdftotext", "-l", "2", path, "-"))
    if pages > 150 or re.search(r"\bISBN\b", text):
        r["book_like"] = True
    return r


def describe(r):
    """One line for people and for the sorter: 'slides · 7 pages · 0.9 MB · names on page 1: Prof. X'."""
    bits = [r.get("kind", "?")]
    if r.get("pages"):
        bits.append(f"{r['pages']} page" + ("s" if r["pages"] != 1 else ""))
    bits.append(f"{r.get('size_mb', 0)} MB")
    if r.get("ocr_conf") is not None:
        bits.append(f"OCR confidence {r['ocr_conf']}")
    if r.get("book_like"):
        bits.append("book-like")
    if r.get("people"):
        bits.append("names on first pages: " + "; ".join(r["people"]))
    return " · ".join(bits)
