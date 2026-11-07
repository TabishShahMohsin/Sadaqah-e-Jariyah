"""Evidence about each file: what it is, what its first pages say, and fingerprints to spot near-duplicates."""
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile

import numpy as np
from PIL import Image

from . import util

SIG_W, SIG_H = 24, 32
MH_N = 64
_P = (1 << 61) - 1
_RNG = np.random.default_rng(20261102)
_A = _RNG.integers(1, _P - 1, MH_N, dtype=np.uint64)
_B = _RNG.integers(0, _P - 1, MH_N, dtype=np.uint64)


def pdf_info(path):
    out = subprocess.run(["pdfinfo", path], capture_output=True, text=True, errors="ignore", timeout=60).stdout
    info = {}
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    info["pages"] = int(m.group(1)) if m else 0
    for k in ("Title", "Author", "Creator", "Producer"):
        m = re.search(rf"^{k}:\s+(.*)$", out, re.M)
        if m and m.group(1).strip():
            info[k.lower()] = util.clean_text(m.group(1), 80)
    return info


def pdf_text(path, first=1, last=3):
    return subprocess.run(["pdftotext", "-f", str(first), "-l", str(last), "-layout", path, "-"],
                          capture_output=True, text=True, errors="ignore", timeout=120).stdout


def render(path, ext, page=1, px=1100):
    """A JPEG of one page (PDF) or the image itself, scaled to at most px on its long side."""
    if ext == "pdf":
        with tempfile.TemporaryDirectory() as td:
            subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-scale-to", str(px), "-jpeg", "-singlefile", path,
                            os.path.join(td, "p")], capture_output=True, timeout=120)
            fp = os.path.join(td, "p.jpg")
            if not os.path.exists(fp):
                return None
            im = Image.open(fp)
            im.load()
    elif ext in ("jpg", "jpeg", "png"):
        im = Image.open(path)
        im.load()
    else:
        return None
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    im.thumbnail((px, px))
    return im


def jpeg_b64(im, q=72):
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=q)
    return base64.b64encode(buf.getvalue()).decode()


def visual_sig(im):
    """Page-1 fingerprint that survives rescans and phone photos: ink bounding box, normalised grayscale."""
    if im is None:
        return None
    a = np.asarray(im.convert("L").resize((300, int(300 * im.height / max(1, im.width)))), dtype=float)
    ink = a < 128
    rows, cols = np.where(ink.any(1))[0], np.where(ink.any(0))[0]
    if len(rows) and len(cols):
        a = a[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    small = np.asarray(Image.fromarray(a.astype("uint8")).resize((SIG_W, SIG_H)), dtype=float)
    q = np.clip(small, 0, 255).astype(np.uint8)
    return base64.b64encode(q.tobytes()).decode()


def sig_vec(b64):
    v = np.frombuffer(base64.b64decode(b64), dtype=np.uint8).astype(float)
    v = v - v.mean()
    n = np.linalg.norm(v)
    return v / n if n else v


def sig_sim(a, b):
    if not a or not b:
        return 0.0
    return float(np.dot(sig_vec(a), sig_vec(b)))


def ocr_page(path, ext, page=1):
    """Text of a scanned page or photo (Tesseract). Empty when OCR isn't available or is switched off (SEJ_OCR=0)."""
    if os.environ.get("SEJ_OCR", "1") == "0" or not shutil.which("tesseract"):
        return ""
    with tempfile.TemporaryDirectory() as td:
        img = os.path.join(td, "p.png")
        if ext == "pdf":
            subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-r", "150", "-gray", "-png", "-singlefile", path,
                            os.path.join(td, "p")], capture_output=True, timeout=120)
        else:
            try:
                im = Image.open(path)
                im.thumbnail((2200, 2200))
                im.convert("L").save(img)
            except OSError:
                return ""
        if not os.path.exists(img):
            return ""
        try:
            return subprocess.run(["tesseract", img, "-", "--psm", "3"], capture_output=True, text=True, timeout=120,
                                  env={**os.environ, "OMP_THREAD_LIMIT": "1"}).stdout
        except subprocess.TimeoutExpired:
            return ""


def text_sig(text):
    t = re.sub(r"[^a-z0-9]", "", text.lower())
    return hashlib.md5(t.encode()).hexdigest() if len(t) > 300 else None


def shingles(text, k=3):
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {" ".join(words[i:i + k]) for i in range(max(0, len(words) - k + 1))}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def minhash(sh):
    """64-number summary of a text's 3-word shingles; equal positions estimate how much two texts overlap."""
    if not sh:
        return None
    h = np.array([int.from_bytes(hashlib.md5(s.encode()).digest()[:7], "big") for s in sh], dtype=np.uint64)
    # (a*h + b) mod p, computed in Python ints to avoid uint64 overflow
    mins = []
    for a, b in zip(_A.tolist(), _B.tolist()):
        mins.append(min((a * x + b) % _P for x in h.tolist()))
    return mins


def mh_sim(a, b):
    if not a or not b:
        return 0.0
    return sum(1 for x, y in zip(a, b) if x == y) / len(a)


def inspect(it):
    """Fill in it['info']: pages, text excerpt, document fields, fingerprints."""
    path, ext = it["path"], it["ext"]
    info = {}
    try:
        if ext == "pdf":
            info.update(pdf_info(path))
            text = pdf_text(path, 1, 4)
            info["text"] = util.norm_space(text)
            im = render(path, "pdf", 1, 600)
            info["vsig"] = visual_sig(im)
        elif ext in ("jpg", "jpeg", "png"):
            im = render(path, ext, 1, 600)
            info["pages"] = 1
            info["dims"] = list(Image.open(path).size)
            info["text"] = ""
            info["vsig"] = visual_sig(im)
        elif ext == "ipynb":
            with open(path, encoding="utf-8", errors="ignore") as fh:
                nb = json.load(fh)
            cells = nb.get("cells", [])
            info["text"] = "\n---\n".join("".join(c.get("source", "")) if isinstance(c.get("source"), list) else str(c.get("source", ""))
                                          for c in cells)[:6000]
        elif ext == "mp4":
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                                 capture_output=True, text=True, timeout=60).stdout.strip()
            info["duration_s"] = round(float(out or 0))
            info["text"] = ""
        else:  # code, csv, txt, md
            with open(path, encoding="utf-8", errors="ignore") as fh:
                info["text"] = fh.read(6000)
    except (OSError, ValueError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        info["error"] = type(e).__name__
        info.setdefault("text", "")
    info["tl"] = len(re.sub(r"\s", "", info.get("text", "")))
    if info["tl"] < 40 and ext in ("pdf", "jpg", "jpeg", "png"):  # a scan or photo: read the first page
        info["ocr"] = util.norm_space(ocr_page(path, ext, 1))[:6000]
    body = info.get("text", "") if info["tl"] >= 40 else info.get("ocr", "")
    try:
        from . import profile as _profile
        it["profile"] = _profile.profile(path, ext)
    except Exception as e:  # noqa: BLE001 — the profile is a hint, never a blocker
        it["profile"] = {"error": type(e).__name__}
    info["tsig"] = text_sig(info.get("text", ""))
    info["mh"] = minhash(shingles(body)) if len(re.sub(r"\s", "", body)) > 80 else None
    it["info"] = info
    it["sha"] = util.sha256(path)
    return info


def summary(it, n=700):
    """The lines the agent sees for one file before it decides whether to look closer."""
    i = it.get("info", {})
    bits = [f"id={it['id']}", f"path={json.dumps(it['src'], ensure_ascii=False)}", f"type={it['ext']}"]
    if it.get("converted_from"):
        bits.append(f"converted_from={it['converted_from']}")
    if i.get("pages"):
        bits.append(f"pages={i['pages']}")
    if i.get("duration_s"):
        bits.append(f"duration={i['duration_s']}s")
    bits.append(f"size={it.get('size', 0) / 1e6:.1f}MB")
    for k in ("title", "author", "creator"):
        if i.get(k):
            bits.append(f"{k}={json.dumps(i[k], ensure_ascii=False)}")
    pr = it.get("profile") or {}
    if pr.get("kind"):
        from . import profile as _profile
        bits.append("profile: " + _profile.describe(pr))
    scanned = i.get("tl", 0) < 40 and it["ext"] in ("pdf", "jpg", "jpeg", "png")
    handwritten = pr.get("kind") == "scan-handwritten" or pr.get("handwritten")
    if scanned:
        bits.append("handwritten: OCR is useless, use view to read it" if handwritten else
                    "scanned" + (": text below is OCR of page 1" if i.get("ocr", "").strip() else ": use view"))
    head = "  ".join(bits)
    txt = "" if (scanned and handwritten) else re.sub(r"[ \t]+", " ", i.get("ocr", "") if scanned else i.get("text", "")).strip()
    txt = re.sub(r"\n{2,}", "\n", txt)[:n]
    return head + ("\n  text: " + txt.replace("\n", " ⏎ ") if txt else "")
