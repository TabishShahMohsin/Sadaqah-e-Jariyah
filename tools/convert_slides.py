#!/usr/bin/env python3
"""
Turn every slide deck in the archive into a PDF, check each conversion, and record it in the catalog.

    python3 tools/convert_slides.py convert [--budget S]   convert decks not done yet (into .sej-cache/slides/)
    python3 tools/convert_slides.py report [--sheets all]   what the checks found; contact sheets of flagged decks
    python3 tools/convert_slides.py apply [--accept PATH]   PDFs into the archive, decks to _Originals/, one change set
    python3 tools/convert_slides.py undo CHANGE_ID          put the decks back exactly as they were

Checks on every deck: PDF pages = visible slides, no symbol characters left without a font (they print as empty
boxes), fonts that fell back to a generic face (layout may shift), audio/video the PDF cannot carry.
Decks that pass are applied; flagged ones wait for a look (report --sheets) and an --accept.
Books-and-Resources decks are left alone (they are not published). Needs LibreOffice (soffice).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import sej  # noqa: E402
from sorter import ingest  # noqa: E402

WORK = os.path.join(REPO, ".sej-cache", "slides")
RESULTS = os.path.join(WORK, "results.json")
ORIGINALS = "_Originals"
DECK_EXT = {"ppt", "pptx", "pps", "ppsx", "pot", "potx", "odp"}
MEDIA_EXT = {"mp4", "m4v", "mov", "avi", "wmv", "mpg", "mpeg", "mkv", "webm", "mp3", "wav", "wma", "m4a", "aac"}
METRIC_OK = {"liberation sans", "liberation serif", "liberation mono", "liberation sans narrow", "carlito", "caladea",
             "nimbus sans", "nimbus roman", "nimbus mono ps", "urw bookman", "urw gothic", "c059", "p052", "dejavu sans"}
PUA = re.compile("[-�]")


def ext_of(p):
    return p.rsplit(".", 1)[-1].lower() if "." in p.rsplit("/", 1)[-1] else ""


def load(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)


def decks(state):
    return {p: e for p, e in state.items()
            if ext_of(p) in DECK_EXT and not e.get("link") and "/Books-and-Resources/" not in p}


# ── checks ──────────────────────────────────────────────────────────────────
def visible_slides(pptx):
    n = 0
    with zipfile.ZipFile(pptx) as z:
        for name in z.namelist():
            if re.fullmatch(r"ppt/slides/slide\d+\.xml", name):
                head = z.read(name)[:2000].decode("utf-8", "ignore")
                if not re.search(r'<p:sld\b[^>]*\bshow="(0|false)"', head):
                    n += 1
    return n


def media_in(pptx):
    with zipfile.ZipFile(pptx) as z:
        return sorted({n.rsplit("/", 1)[-1] for n in z.namelist() if n.startswith("ppt/media/") and ext_of(n) in MEDIA_EXT})


def fonts_requested(pptx):
    fams = set()
    with zipfile.ZipFile(pptx) as z:
        for name in z.namelist():
            if name.startswith(("ppt/slides/", "ppt/slideLayouts/", "ppt/slideMasters/", "ppt/theme/")) and name.endswith(".xml"):
                for m in re.finditer(r'<a:(?:latin|sym)\b[^>]*\btypeface="([^"]*)"', z.read(name).decode("utf-8", "ignore")):
                    f = m.group(1).strip()
                    if f and not f.startswith("+"):
                        fams.add(f)
    return fams


def alias_families():
    try:
        return {m.lower() for m in re.findall(r"<alias[^>]*><family>([^<]+)</family>", open(ingest.FONTS_CONF, encoding="utf-8").read())}
    except OSError:
        return set()


def font_fallbacks(fams):
    """Requested fonts that end up in a generic face: their text may be wider or narrower than designed."""
    if not sys.platform.startswith("linux") or not shutil.which("fc-match"):
        return []
    env = {**os.environ, "FONTCONFIG_FILE": ingest.FONTS_CONF}
    mapped = alias_families()
    out = []
    for f in sorted(fams):
        got = subprocess.run(["fc-match", "-f", "%{family[0]}", f], capture_output=True, text=True, env=env).stdout.strip()
        if got.lower() == f.lower() or f.lower() in mapped or got.lower() in METRIC_OK:
            continue
        out.append(f"{f} → {got}")
    return out


_GLYPHS = {}


def font_glyphs(name):
    """Code points a symbol font can draw (Wingdings etc. from the installed copy); None when unknown."""
    if name not in _GLYPHS:
        _GLYPHS[name] = None
        files = {"wingdings": "wingding.ttf", "webdings": "webdings.ttf", "marlett": "marlett.ttf"}
        f = next((os.path.join(dp, files[name]) for dp, _, fs in os.walk(os.path.expanduser("~/.local/share/fonts"))
                  if files.get(name) in fs), None) if name in files else None
        if f:
            try:
                from fontTools.ttLib import TTFont
                _GLYPHS[name] = set().union(*(st.cmap for st in TTFont(f)["cmap"].tables if st.platformID == 3))
            except Exception:  # noqa: BLE001
                pass
    return _GLYPHS[name]


def missing_glyphs(pdf):
    """Characters in the PDF that most likely print as an empty box: private-use code points drawn in a font
    that has no glyph for them (e.g. LibreOffice's OpenSymbol standing in for Wingdings 3)."""
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTChar
    bad = {}

    def walk(o):
        if isinstance(o, LTChar):
            ch = o.get_text()
            if ch and PUA.match(ch):
                font = o.fontname.split("+")[-1].lower()
                fam = next((k for k in ("symbol", "wingdings", "webdings", "marlett") if k in font), None)
                ok = fam == "symbol" and "opensymbol" not in font
                if fam in ("wingdings", "webdings", "marlett"):
                    g = font_glyphs(fam)
                    ok = g is None or ord(ch) in g
                if not ok:
                    key = f"{o.fontname.split('+')[-1]} U+{ord(ch):04X}"
                    bad[key] = bad.get(key, 0) + 1
        elif hasattr(o, "__iter__"):
            for x in o:
                walk(x)
    for page in extract_pages(pdf):
        walk(page)
    return bad


def strip_media(pdf):
    """Drop the embedded audio/video LibreOffice puts in the PDF (a viewer in the browser can't play it, and it makes
    the PDF as big as the video). The media files are published next to the PDF instead."""
    from pypdf import PdfReader, PdfWriter
    r = PdfReader(pdf)
    w = PdfWriter()
    for page in r.pages:
        annots = page.get("/Annots")
        if annots:
            keep = [a for a in annots if a.get_object().get("/Subtype") not in ("/Screen", "/RichMedia", "/Movie", "/Sound")]
            if keep:
                from pypdf.generic import ArrayObject, NameObject
                page[NameObject("/Annots")] = ArrayObject(keep)
            else:
                del page["/Annots"]
        w.add_page(page)
    if r.metadata:
        w.add_metadata(r.metadata)
    tmp = pdf + ".tmp"
    with open(tmp, "wb") as fh:
        w.write(fh)
    os.replace(tmp, pdf)


def media_files(deck):
    """(name inside the deck, published suffix) for each video/audio file, in slide order."""
    with zipfile.ZipFile(deck) as z:
        names = [n for n in z.namelist() if n.startswith("ppt/media/") and ext_of(n) in MEDIA_EXT]
    names.sort(key=lambda n: int(re.sub(r"\D", "", n.rsplit("/", 1)[-1]) or 0))
    out, count = [], {}
    for n in names:
        kind = "audio" if ext_of(n) in {"mp3", "wav", "wma", "m4a", "aac"} else "video"
        count[kind] = count.get(kind, 0) + 1
        out.append((n, f" ({kind} {count[kind]}).{ext_of(n)}"))
    return out


def pdf_text(path):
    return subprocess.run(["pdftotext", "-q", "-enc", "UTF-8", path, "-"], capture_output=True, text=True).stdout


def shingles(text):
    w = re.findall(r"[a-z0-9]+", text.lower())
    return {" ".join(w[i:i + 3]) for i in range(max(0, len(w) - 2))}


def similarity(a, b):
    sa, sb = shingles(a), shingles(b)
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(4 << 20), b""):
            h.update(b)
    return h.hexdigest()


def convert_one(p, e, state, root=REPO):
    t0 = time.time()
    src = os.path.join(root, p)
    import tempfile
    work = tempfile.mkdtemp(prefix="sej-slides-")  # scratch outside the repo: LibreOffice leaves lock files behind
    r = {"sha": e["h"], "pdf": None}
    try:
        out = ingest.to_pdf({"id": "x", "path": src, "ext": ext_of(p)}, work)
        conv = os.path.join(work, "conv-x")
        pptx = src if ext_of(p) in ("pptx", "ppsx", "potx") else next(
            (os.path.join(conv, "_pptx", f) for f in os.listdir(os.path.join(conv, "_pptx")) if f.endswith(".pptx")), None
        ) if os.path.isdir(os.path.join(conv, "_pptx")) else None
        if pptx is None and ext_of(p) in DECK_EXT - {"pptx", "ppsx", "potx"}:  # no fixes were needed: export once to inspect
            os.makedirs(os.path.join(conv, "_inspect"), exist_ok=True)
            ingest.soffice(src, "pptx", os.path.join(conv, "_inspect"))
            pptx = next((os.path.join(conv, "_inspect", f) for f in os.listdir(os.path.join(conv, "_inspect")) if f.endswith(".pptx")), None)
        if not out:
            r["error"] = "LibreOffice produced no PDF"
            return r
        dst = os.path.join(WORK, e["h"] + ".pdf")
        shutil.move(out, dst)
        if pptx and media_in(pptx):
            strip_media(dst)
        from pypdf import PdfReader
        r["pdf"] = os.path.relpath(dst, root)
        r["pages"] = len(PdfReader(dst).pages)
        text = pdf_text(dst)
        r["boxes"] = missing_glyphs(dst)
        if pptx:
            r["slides"] = visible_slides(pptx)
            r["media"] = media_in(pptx)
            r["fallbacks"] = font_fallbacks(fonts_requested(pptx))
        twin = p.rsplit(".", 1)[0] + ".pdf"
        twin = next((q for q in state if q.lower() == twin.lower()), None)
        if twin and os.path.exists(os.path.join(root, twin)):
            r["twin"] = twin
            r["twin_similarity"] = round(similarity(text, pdf_text(os.path.join(root, twin))), 2)
            from pypdf import PdfReader as _R
            r["twin_pages"] = len(_R(os.path.join(root, twin)).pages)
    except Exception as ex:  # noqa: BLE001
        r["error"] = f"{type(ex).__name__}: {ex}"[:300]
    finally:
        r["seconds"] = round(time.time() - t0, 1)
        shutil.rmtree(work, ignore_errors=True)
    return r


def problems(r):
    out = []
    if r.get("error"):
        out.append(r["error"])
    if r.get("pdf") and r.get("slides") is not None and r["pages"] != r["slides"]:
        out.append(f"{r['pages']} pages for {r['slides']} visible slides")
    if r.get("slides") is None and r.get("pdf"):
        out.append("slide count not checked")
    if r.get("boxes"):
        out.append(f"{sum(r['boxes'].values())} characters print as boxes ({', '.join(list(r['boxes'])[:4])})")
    return out


def cmd_convert(a, root=REPO):
    state = sej.load_state()
    res = load(RESULTS, {})
    if a.redo == []:
        a.redo = ["all"]
    if a.redo:  # e.g. after installing fonts: convert everything again
        res = {p: r for p, r in res.items() if p not in a.redo and "all" not in a.redo}
    todo = [(p, e) for p, e in sorted(decks(state).items()) if res.get(p, {}).get("sha") != e["h"]]
    print(f"{len(decks(state))} decks, {len(todo)} to convert", flush=True)
    t0, n = time.time(), 0
    with ThreadPoolExecutor(a.jobs) as pool:
        futs = {}
        it = iter(todo)
        while True:
            while len(futs) < a.jobs and not (a.budget and time.time() - t0 > a.budget):
                nxt = next(it, None)
                if nxt is None:
                    break
                futs[pool.submit(convert_one, nxt[0], nxt[1], state, root)] = nxt[0]
            if not futs:
                break
            f = next(iter(futs))
            res[futs.pop(f)] = f.result()
            n += 1
            save(RESULTS, res)  # after every deck: a run cut short keeps its work
    save(RESULTS, res)
    left = len(todo) - n
    print(f"converted {n} now; " + (f"{left} left — run again" if left else "all done"))


def contact_sheet(pdf, out, cols=5, width=300):
    from PIL import Image, ImageDraw
    tmp = out + ".pages"
    os.makedirs(tmp, exist_ok=True)
    subprocess.run(["pdftoppm", "-r", "36", "-png", pdf, os.path.join(tmp, "p")], check=True)
    pages = sorted(os.listdir(tmp))
    ims = [Image.open(os.path.join(tmp, f)).convert("RGB") for f in pages]
    ims = [i.resize((width, max(1, int(i.height * width / i.width)))) for i in ims]
    h = max(i.height for i in ims)
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (width + 6) + 6, rows * (h + 18) + 6), "#777")
    d = ImageDraw.Draw(sheet)
    for k, im in enumerate(ims):
        x, y = 6 + (k % cols) * (width + 6), 6 + (k // cols) * (h + 18)
        sheet.paste(im, (x, y + 14))
        d.text((x, y), str(k + 1), fill="white")
    sheet.save(out, quality=78)
    for f in pages:
        try:
            os.remove(os.path.join(tmp, f))
        except OSError:
            pass


def cmd_report(a, root=REPO):
    state = sej.load_state()
    res = load(RESULTS, {})
    ds = decks(state)
    flagged, ok = [], []
    for p in sorted(ds):
        r = res.get(p)
        if not r or r.get("sha") != ds[p]["h"]:
            continue
        (flagged if problems(r) else ok).append(p)
    print(f"{len(ok) + len(flagged)} of {len(ds)} converted: {len(ok)} pass, {len(flagged)} flagged")
    for p in flagged:
        print(f"  ✗ {p}\n      " + "; ".join(problems(res[p])))
    subs = {}
    for p in ds:
        for f in res.get(p, {}).get("fallbacks") or []:
            subs.setdefault(f, []).append(p)
    if subs:
        print("fonts with no close stand-in (text shown in Liberation Sans/Serif instead; layout may shift slightly):")
        for f, ps in sorted(subs.items(), key=lambda x: -len(x[1])):
            print(f"  {f}  ({len(ps)} deck{'s' if len(ps) > 1 else ''})")
    media = [p for p in ds if res.get(p, {}).get("media")]
    if media:
        print(f"{len(media)} decks contain audio/video: published next to the PDF as '… (video 1).mp4' etc.")
        for p in media:
            print(f"  ♪ {p}: {len(res[p]['media'])} file(s)")
    twins = [p for p in ds if res.get(p, {}).get("twin")]
    for p in twins:
        r = res[p]
        print(f"  = {p}\n      PDF of the same name exists (text overlap {r['twin_similarity']:.0%}): "
              + ("same deck: the existing PDF stays" if same_as_twin(r) else "different version: kept as '… (slides).pdf'"))
    if a.sheets:
        sheet_dir = os.path.join(WORK, "sheets")
        os.makedirs(sheet_dir, exist_ok=True)
        for p in (flagged if a.sheets == "flagged" else flagged + ok):
            r = res[p]
            if r.get("pdf"):
                out = os.path.join(sheet_dir, re.sub(r"[^A-Za-z0-9._-]+", "_", p.split("/", 2)[-1])[-120:] + ".jpg")
                contact_sheet(os.path.join(root, r["pdf"]), out)
        print(f"contact sheets in {os.path.relpath(sheet_dir, root)}/")


def same_as_twin(r):
    """The PDF beside the deck is the same deck: (almost) the same text on the same number of pages.
    Anything less (a shorter version, a scan, handouts) keeps both, the conversion as '… (slides).pdf'."""
    return bool(r.get("twin")) and r.get("twin_similarity", 0) >= 0.9 and abs(r.get("twin_pages", -99) - r.get("pages", 0)) <= 1


def unique(path, taken):
    stem, ext = path.rsplit(".", 1)
    n, cand = 1, path
    while cand.lower() in taken:
        n += 1
        cand = f"{stem}_{n}.{ext}"
    return cand


def cmd_apply(a, root=REPO):
    state = sej.load_state()
    res = load(RESULTS, {})
    ds = decks(state)
    accept = set(a.accept or [])
    taken = {p.lower() for p in state}
    ops, moved = [], 0
    now = int(time.time())
    for p, e in sorted(ds.items()):
        r = res.get(p)
        if not r or r.get("sha") != e["h"] or not r.get("pdf"):
            continue
        if problems(r) and p not in accept:
            continue
        kept = f"{ORIGINALS}/{p}"
        was = {k: e[k] for k in ("s", "h", "t", "meta") if k in e}
        if same_as_twin(r):
            ops.append({"op": "remove", "p": p, "why": f"same as {r['twin']}", "original_kept": kept, "was": was})
            if e.get("meta") and not (state.get(r["twin"], {}).get("meta") or {}).get("credit"):
                ops.append({"op": "meta", "p": r["twin"], "meta": e["meta"]})
        else:
            stem = p.rsplit(".", 1)[0]
            target = unique(stem + (" (slides)" if r.get("twin") else "") + ".pdf", taken)
            taken.add(target.lower())
            pdf = os.path.join(root, r["pdf"])
            os.makedirs(os.path.dirname(os.path.join(root, target)), exist_ok=True)
            shutil.copyfile(pdf, os.path.join(root, target))
            meta = dict(e.get("meta") or {})
            meta.setdefault("note", "converted from the original slides")
            ops.append({"op": "add", "p": target, "s": os.path.getsize(pdf), "h": sha256(pdf), "t": e.get("t", now),
                        "meta": meta, "from": p})
            if r.get("media") and zipfile.is_zipfile(os.path.join(root, p)):  # the deck's video/audio, as files
                with zipfile.ZipFile(os.path.join(root, p)) as z:
                    for inner, suffix in media_files(os.path.join(root, p)):
                        mp = unique(target[:-4] + suffix, taken)
                        taken.add(mp.lower())
                        with z.open(inner) as src_fh, open(os.path.join(root, mp), "wb") as out_fh:
                            shutil.copyfileobj(src_fh, out_fh)
                        ops.append({"op": "add", "p": mp, "s": os.path.getsize(os.path.join(root, mp)),
                                    "h": sha256(os.path.join(root, mp)), "t": e.get("t", now),
                                    "meta": {**meta, "note": "from the original slides"}, "from": p})
            ops.append({"op": "remove", "p": p, "why": f"converted to {target}", "original_kept": kept, "was": was})
        os.makedirs(os.path.dirname(os.path.join(root, kept)), exist_ok=True)
        os.replace(os.path.join(root, p), os.path.join(root, kept))
        moved += 1
    if not ops:
        return print("nothing to apply")
    path = sej.write_change(ops, {"kind": "local", "by": "maintainer", "tool": "convert_slides"}, "slides-to-pdf")
    print(f"{moved} decks replaced by PDFs; originals kept in {ORIGINALS}/ → {os.path.relpath(path, root)}")
    print("Undo with:  python3 tools/convert_slides.py undo " + os.path.basename(path)[:-5])


def cmd_undo(a, root=REPO):
    ch = load(os.path.join(sej.CHANGES, a.change_id + ".json"), None)
    if not ch:
        raise SystemExit(f"no change set {a.change_id}")
    ops = []
    for op in ch["ops"]:
        if op["op"] == "remove" and op.get("original_kept"):
            src, dst = os.path.join(root, op["original_kept"]), os.path.join(root, op["p"])
            if os.path.exists(src):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.replace(src, dst)
            w = op["was"]
            ops.append({"op": "add", "p": op["p"], **{k: w[k] for k in ("s", "h", "t") if k in w}, "meta": w.get("meta") or {}})
        elif op["op"] == "add" and op.get("from"):
            f = os.path.join(root, op["p"])
            if os.path.exists(f):
                aside = os.path.join(root, ORIGINALS, "_undone", op["p"])
                os.makedirs(os.path.dirname(aside), exist_ok=True)
                os.replace(f, aside)
            ops.append({"op": "remove", "p": op["p"], "why": f"undo {a.change_id}"})
    path = sej.write_change(ops, {"kind": "local", "by": "maintainer", "tool": "convert_slides", "undoes": a.change_id}, "undo-slides")
    print(f"restored {sum(o['op'] == 'add' for o in ops)} decks → {os.path.relpath(path, root)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("convert"); p.add_argument("--budget", type=float, default=0); p.add_argument("--jobs", type=int, default=3)
    p.add_argument("--redo", nargs="*", help="convert these decks again (no paths: all of them)")
    p = sub.add_parser("report"); p.add_argument("--sheets", nargs="?", const="flagged", choices=["flagged", "all"])
    p = sub.add_parser("apply"); p.add_argument("--accept", action="append", help="apply this flagged deck anyway")
    p = sub.add_parser("undo"); p.add_argument("change_id")
    a = ap.parse_args()
    {"convert": cmd_convert, "report": cmd_report, "apply": cmd_apply, "undo": cmd_undo}[a.cmd](a)


if __name__ == "__main__":
    main()
