"""Gather inputs, unpack archives, set aside system junk and convert everything to a published format."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

from . import util

JUNK = re.compile(r"(?i)(^|/)(\.ds_store|thumbs\.db|desktop\.ini|icon\r|~\$[^/]*|\._[^/]*)$|(^|/)__macosx/")
# code projects' machinery: installed packages, environments, caches, build output (not study material)
CODE_JUNK = re.compile(r"(?i)(^|/)(node_modules|__pycache__|site-packages|\.?venv|\.?env|\.git|\.ipynb_checkpoints|\.idea|"
                       r"\.vscode|\.gradle|\.pytest_cache|\.mypy_cache|\.next|\.cache|xsim\.dir|\.Xil|webtalk|"
                       r"[^/]+\.(runs|sim|cache|hw|ip_user_files|gen))/|"   # Vivado/Xilinx project output
                       r"\.(pyc|pyo|pyi|so|dylib|dll|o|a|class|jar|whl|egg|lock|map)$|(^|/)pyvenv\.cfg$")
DATASET_IMAGES = 150   # a folder holding this many images is a dataset (e.g. an ML project's training images)
MAX_UNZIPPED_BYTES = 8 * 1024 ** 3
MAX_UNZIPPED_FILES = 5000
REJECT_EXT = {"exe", "msi", "dmg", "apk", "app", "bat", "cmd", "com", "scr", "out", "class", "o", "obj", "dll", "so",
              "html", "htm", "css", "svg", "lnk", "url", "webloc", "download", "crdownload", "part", "torrent", "iso"}


def new_item(items, src, path, parent=None, fingerprint=True, **kw):
    it = {"id": f"f{len(items) + 1:04d}", "src": src, "name": os.path.basename(src.rstrip("/")) or src,
          "path": path, "orig_path": path, "parent": parent, **kw}
    it["ext"] = util.ext_of(it["name"])
    if path and os.path.exists(path):
        it["size"] = os.path.getsize(path)
        if fingerprint:
            it["orig_sha"] = util.sha256(path)
    items.append(it)
    return it


IMAGE_EXT = ("png", "jpg", "jpeg", "bmp", "gif", "tif", "tiff", "webp")


def from_folder(root, items):
    """Every file under a local folder becomes an item; its path inside the folder is kept (the strongest clue).
    System junk, code-project machinery and image datasets are recognised here and not fingerprinted (it's slow)."""
    base = os.path.dirname(os.path.abspath(root).rstrip("/"))
    found = []
    for dp, dns, fns in os.walk(root):
        dns.sort()
        for f in sorted(fns):
            fp = os.path.join(dp, f)
            if not os.path.islink(fp):
                found.append((os.path.relpath(fp, base), fp))
    per_dir = {}
    for rel, _ in found:
        if util.ext_of(os.path.basename(rel)) in IMAGE_EXT:
            per_dir[os.path.dirname(rel)] = per_dir.get(os.path.dirname(rel), 0) + 1
    for rel, fp in found:
        junk = bool(JUNK.search(rel) or CODE_JUNK.search(rel) or
                    (util.ext_of(os.path.basename(rel)) in IMAGE_EXT and per_dir.get(os.path.dirname(rel), 0) >= DATASET_IMAGES))
        new_item(items, rel, fp, fingerprint=not junk)
    return items


def settle_junk(items):
    per_dir = {}
    for it in items:
        if not it.get("outcome") and it.get("ext") in ("png", "jpg", "jpeg", "bmp", "gif", "tif", "tiff", "webp"):
            per_dir[os.path.dirname(it["src"])] = per_dir.get(os.path.dirname(it["src"]), 0) + 1
    for it in items:
        if it.get("outcome"):
            continue
        if JUNK.search(it["src"]):
            it["outcome"] = {"action": "ignored", "reason": "system file (not part of the material)"}
        elif CODE_JUNK.search(it["src"]):
            it["outcome"] = {"action": "ignored", "reason": "code project machinery (packages, environment, cache or build output)"}
        elif per_dir.get(os.path.dirname(it["src"]), 0) >= DATASET_IMAGES and it.get("ext") in (
                "png", "jpg", "jpeg", "bmp", "gif", "tif", "tiff", "webp"):
            it["outcome"] = {"action": "ignored", "reason": f"part of an image dataset ({per_dir[os.path.dirname(it['src'])]} images in one folder)"}
        elif it.get("size", 1) == 0 and it.get("path"):
            it["outcome"] = {"action": "reject", "category": "unreadable", "reason": "empty file"}


def unpack(items, workdir, depth=0):
    """Zips are opened and their files become items of their own; the zip itself is recorded as 'unpacked'."""
    added = 0
    for it in list(items):
        if it.get("outcome") or it["ext"] != "zip" or it.get("unpacked"):
            continue
        it["unpacked"] = True
        dest = os.path.join(workdir, f"zip-{it['id']}")
        try:
            with zipfile.ZipFile(it["path"]) as zf:
                infos = [i for i in zf.infolist() if not i.is_dir()]
                if len(infos) > MAX_UNZIPPED_FILES or sum(i.file_size for i in infos) > MAX_UNZIPPED_BYTES:
                    it["outcome"] = {"action": "reject", "category": "unreadable", "reason": "zip is too large to unpack"}
                    continue
                for info in infos:
                    rel = util.safe_rel(info.filename)
                    if not rel:
                        continue
                    target = os.path.join(dest, rel)
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(info) as src, open(target, "wb") as out:
                        shutil.copyfileobj(src, out)
                    stem = it["src"][:-4] if it["src"].lower().endswith(".zip") else it["src"]
                    new_item(items, f"{stem}/{rel}", target, parent=it["id"])
                    added += 1
            it["outcome"] = {"action": "unpacked", "reason": f"zip opened: {len(infos)} files inside are listed separately"}
        except (zipfile.BadZipFile, OSError, RuntimeError) as e:  # encrypted or damaged
            it["outcome"] = {"action": "reject", "category": "unreadable", "reason": f"zip could not be opened ({type(e).__name__})"}
    if added and depth < 2:
        settle_junk(items)
        unpack(items, workdir, depth + 1)


# ── conversion to published formats (CONVENTIONS.md §5) ─────────────────────
FONTS_CONF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts.conf")


def soffice(src, fmt, outdir, timeout=240):
    """LibreOffice headless conversion with a throwaway profile (safe to run in parallel)."""
    with tempfile.TemporaryDirectory() as prof:
        cmd = ["soffice", f"-env:UserInstallation=file://{prof}", "--headless", "--norestore", "--convert-to", fmt, "--outdir", outdir, src]
        # throwaway HOME, but fonts installed in the user's own folder (~/.local/share/fonts) stay visible
        env = {**os.environ, "HOME": prof,
               "XDG_DATA_HOME": os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")}
        if sys.platform.startswith("linux") and os.path.exists(FONTS_CONF):
            env["FONTCONFIG_FILE"] = FONTS_CONF  # stand-ins for Windows fonts (macOS has them or uses its own lookup)
        subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)


def quicklook_pdf(path, out):
    """Apple .pages/.key/.numbers keep a PDF preview inside the package."""
    with zipfile.ZipFile(path) as zf:
        for n in zf.namelist():
            if n.lower().endswith("preview.pdf"):
                with zf.open(n) as s, open(out, "wb") as o:
                    shutil.copyfileobj(s, o)
                return True
    return False


# Bullets drawn from symbol fonts (Symbol, Wingdings 1-3, Zapf Dingbats …) use private code points that only those exact
# fonts can draw, so on a machine without them they print as empty boxes. Before converting, each such bullet is
# replaced by the real Unicode character it shows, in a font every machine has.
SLIDE_EXT = {"ppt", "pptx", "pps", "ppsx", "pot", "potx", "odp"}
SYMBOL_TO_UNICODE = dict(zip(range(0x20, 0x7F), " !\u2200#\u2203%&\u220b()\u2217+,\u2212./0123456789:;<=>?\u2245\u0391\u0392\u03a7\u0394\u0395\u03a6"
    "\u0393\u0397\u0399\u03d1\u039a\u039b\u039c\u039d\u039f\u03a0\u0398\u03a1\u03a3\u03a4\u03a5\u03c2\u03a9\u039e\u03a8\u0396[\u2234]\u22a5_\u203e\u03b1\u03b2"
    "\u03c7\u03b4\u03b5\u03c6\u03b3\u03b7\u03b9\u03d5\u03ba\u03bb\u03bc\u03bd\u03bf\u03c0\u03b8\u03c1\u03c3\u03c4\u03c5\u03d6\u03c9\u03be\u03c8\u03b6{|}\u223c"))
SYMBOL_TO_UNICODE.update(zip(range(0xA0, 0xFF), "\u20ac\u03d2\u2032\u2264\u2044\u221e\u0192\u2663\u2666\u2665\u2660\u2194\u2190\u2191\u2192\u2193\u00b0\u00b1\u2033"
    "\u2265\u00d7\u221d\u2202\u2022\u00f7\u2260\u2261\u2248\u2026\u23d0\u23af\u21b5\u2135\u2111\u211c\u2118\u2297\u2295\u2205\u2229\u222a\u2283\u2287"
    "\u2284\u2282\u2286\u2208\u2209\u2220\u2207\u00ae\u00a9\u2122\u220f\u221a\u22c5\u00ac\u2227\u2228\u21d4\u21d0\u21d1\u21d2\u21d3\u25ca\u2329\u00ae"
    "\u00a9\u2122\u2211\u239b\u239c\u239d\u23a1\u23a2\u23a3\u23a7\u23a8\u23a9\u23aa\u20ac\u232a\u222b\u2320\u23ae\u2321\u239e\u239f\u23a0\u23a4\u23a5"
    "\u23a6\u23ab\u23ac\u23ad"))
WINGDINGS_TO_UNICODE = {0x6C: "\u25cf", 0x6D: "\u274d", 0x6E: "\u25a0", 0x6F: "\u25a1", 0x70: "\u25fb", 0x71: "\u2751", 0x72: "\u2752",
                        0x73: "\u2b27", 0x74: "\u29eb", 0x75: "\u25c6", 0x76: "\u2756", 0x77: "\u2b25", 0x9F: "\u2022", 0xA7: "\u25aa",
                        0xA8: "\u25fb", 0xD8: "\u27a2", 0xE0: "\u2192", 0xE8: "\u2794", 0xF0: "\u21e8", 0xFB: "\u2717", 0xFC: "\u2713",
                        0xFD: "\u2612", 0xFE: "\u2611", 0x46: "\u261e", 0x4A: "\u263a", 0xA1: "\u25cb",
                        0xA4: "\u25c9", 0xA5: "\u25ce", 0xAB: "\u2605", 0x43: "\U0001f44d", 0x44: "\U0001f44e"}
DINGBATS_TO_UNICODE = {0x6C: "\u25cf", 0x6E: "\u25a0", 0x6F: "\u274f", 0x75: "\u25c6", 0x76: "\u2756", 0x5B: "\u275b", 0x5D: "\u275d"}
BULLET_FONTS = {"symbol": (SYMBOL_TO_UNICODE, "\u2022"), "wingdings": (WINGDINGS_TO_UNICODE, "\u2022"),
                "wingdings 2": ({}, "\u25aa"), "wingdings 3": ({}, "\u25ba"), "monotype sorts": (DINGBATS_TO_UNICODE, "\u25aa"),
                "zapf dingbats": (DINGBATS_TO_UNICODE, "\u25aa"), "zapfdingbats": (DINGBATS_TO_UNICODE, "\u25aa"),
                "webdings": ({}, "\u25aa"), "marlett": ({}, "\u25aa")}
BULLET_RE = re.compile(r'<a:buFont\b([^>]*?)\btypeface="([^"]+)"([^>]*)/>(\s*)<a:buChar\b([^>]*?)\bchar="([^"]*)"([^>]*)/>')


def _bullet_char(font, ch):
    table, default = BULLET_FONTS[font]
    if not ch:
        return default
    code = ord(ch[0])
    code = code - 0xF000 if 0xF000 <= code <= 0xF0FF else code
    return table.get(code, default)


def _swap_bullets(xml):
    import html
    from xml.sax.saxutils import escape

    def sub(m):
        font = m.group(2).lower()
        if font not in BULLET_FONTS:
            return m.group(0)
        ch = _bullet_char(font, html.unescape(m.group(6)))
        return (f'<a:buFont typeface="DejaVu Sans"/>{m.group(4)}'
                f'<a:buChar{m.group(5)}char="{escape(ch, {chr(34): "&quot;"})}"{m.group(7)}/>')
    return BULLET_RE.sub(sub, xml)


RUN_RE = re.compile(r'<a:r>(<a:rPr\b(?:[^>]*/>|.*?</a:rPr>))(\s*<a:t>)(.*?)(</a:t>\s*</a:r>)', re.S)
TYPEFACE_RE = re.compile(r'<a:(latin|ea|cs|sym)\b[^>]*\btypeface="([^"]+)"[^>]*/>')


def _swap_symbol_runs(xml):
    """Text typed in a symbol font (α as 'a' in Symbol, ☺ as 'J' in Wingdings) becomes the real Unicode character in
    an ordinary font. Runs with any character that can't be mapped are left as they are."""
    import html
    from xml.sax.saxutils import escape

    def sub(m):
        rpr, text = m.group(1), html.unescape(m.group(3))
        fonts = {k: v.lower() for k, v in TYPEFACE_RE.findall(rpr)}
        latin, sym = fonts.get("latin"), fonts.get("sym")
        whole = latin in BULLET_FONTS and latin in ("symbol", "wingdings")      # every character is in the symbol font
        part = sym in ("symbol", "wingdings") and not whole                     # only private-use characters are
        orphan = not (whole or part) and any(0xF020 <= ord(ch) <= 0xF0FF for ch in text)
        if not (whole or part or orphan):
            return m.group(0)
        # orphan: symbol-font codes left in an ordinary font (the font was lost on copy-paste); Office shows these
        # as Wingdings, e.g. U+F076 ❖ and U+F0A1 ○ before a line
        table = SYMBOL_TO_UNICODE if (latin if whole else sym) == "symbol" else WINGDINGS_TO_UNICODE
        out = []
        for ch in text:
            c = ord(ch)
            if 0xF000 <= c <= 0xF0FF:
                c -= 0xF000
            elif not whole:
                out.append(ch)
                continue
            if c == 0x20:
                out.append(" ")
                continue
            if c not in table:
                return m.group(0)
            out.append(table[c])
        rpr = TYPEFACE_RE.sub(lambda f: f'<a:{f.group(1)} typeface="DejaVu Serif"/>' if f.group(2).lower() in ("symbol", "wingdings") else f.group(0), rpr)
        return f"<a:r>{rpr}{m.group(2)}{escape(''.join(out))}{m.group(4)}"
    return RUN_RE.sub(sub, xml)


def fixed_slides(path, out_dir):
    """A copy of the deck with symbol-font bullets replaced, or the original when nothing needs fixing."""
    src = path
    if not path.lower().endswith((".pptx", ".ppsx", ".potx")):
        tmp = os.path.join(out_dir, "_pptx")
        os.makedirs(tmp, exist_ok=True)
        soffice(path, "pptx", tmp)  # old binary .ppt: read it through LibreOffice's own PowerPoint export
        conv = [os.path.join(tmp, f) for f in os.listdir(tmp) if f.endswith(".pptx")]
        if not conv:
            return path
        src = conv[0]
    try:
        with zipfile.ZipFile(src) as zin:
            names = zin.namelist()
            xml = {n: zin.read(n).decode("utf-8") for n in names if n.startswith("ppt/") and n.endswith(".xml")}
            fixed = {n: _swap_symbol_runs(_swap_bullets(x)) for n, x in xml.items()}
            if all(fixed[n] == xml[n] for n in xml):
                return path
            out = os.path.join(out_dir, "_fixed", os.path.splitext(os.path.basename(path))[0] + ".pptx")
            write_package(out, ((n, fixed[n].encode("utf-8") if n in fixed else zin.read(n)) for n in names))
            return out
    except (zipfile.BadZipFile, UnicodeDecodeError, KeyError):
        return path


def write_package(out, entries):
    """Write an Office (OOXML) package the way Office does: [Content_Types].xml first, plain deflated entries.
    Some files in the archive were rewritten by tools that put it last; LibreOffice then refuses to open them."""
    entries = list(entries)
    entries.sort(key=lambda e: e[0] != "[Content_Types].xml")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
    return out


def repacked(path, out_dir):
    try:
        with zipfile.ZipFile(path) as z:
            return write_package(os.path.join(out_dir, "_repacked", os.path.basename(path)), [(n, z.read(n)) for n in z.namelist()])
    except (zipfile.BadZipFile, OSError):
        return None


def to_pdf(it, workdir):
    out_dir = os.path.join(workdir, f"conv-{it['id']}")
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(it["path"]))[0]
    out = os.path.join(out_dir, stem + ".pdf")
    if it["ext"] in ("pages", "key", "numbers"):
        ok = quicklook_pdf(it["path"], out)
        if not ok and it["ext"] != "numbers":
            soffice(it["path"], "pdf", out_dir)
    elif it["ext"] in SLIDE_EXT:
        soffice(fixed_slides(it["path"], out_dir), "pdf", out_dir)
    else:
        soffice(it["path"], "pdf", out_dir)
    if not (os.path.exists(out) and os.path.getsize(out)) and zipfile.is_zipfile(it["path"]):
        src = repacked(it["path"], out_dir)  # unusual zip layout: repack and try once more
        if src:
            soffice(fixed_slides(src, out_dir) if it["ext"] in SLIDE_EXT else src, "pdf", out_dir)
    return out if os.path.exists(out) and os.path.getsize(out) > 0 else None


def sheets_to_csv(it, workdir):
    out_dir = os.path.join(workdir, f"conv-{it['id']}")
    os.makedirs(out_dir, exist_ok=True)
    # last token -1: every sheet to its own file (LibreOffice 7.2+); UTF-8, comma separated, quoted text
    soffice(it["path"], 'csv:Text - txt - csv (StarCalc):44,34,76,1,,0,false,true,false,false,false,-1', out_dir)
    return sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.lower().endswith(".csv"))


def photo_to_jpeg(it, workdir):
    from PIL import Image
    if it["ext"] in ("heic", "heif"):
        try:
            import pillow_heif  # noqa: F401
            pillow_heif.register_heif_opener()
        except ImportError:
            out = os.path.join(workdir, f"conv-{it['id']}.jpg")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", it["path"], "-frames:v", "1", "-q:v", "3", out], capture_output=True, timeout=120)
            return out if os.path.exists(out) and os.path.getsize(out) else None
    im = Image.open(it["path"])
    if getattr(im, "is_animated", False):
        im.seek(0)
    if im.mode not in ("RGB", "L"):
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[-1])
        im = bg
    out = os.path.join(workdir, f"conv-{it['id']}.jpg")
    im.save(out, "JPEG", quality=88, optimize=True)
    return out


def png_is_photo(path):
    """PNG stays PNG for screenshots and diagrams (few colours, sharp text); camera photos become JPEG."""
    from PIL import Image
    try:
        im = Image.open(path)
        if im.mode in ("P", "1", "L", "LA"):
            return False
        small = im.convert("RGB").resize((min(im.width, 256), min(im.height, 256)))
        colours = small.getcolors(256 * 256)
        return colours is None or len(colours) > 6000
    except OSError:
        return False


def video_to_mp4(it, workdir):
    out = os.path.join(workdir, f"conv-{it['id']}.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", it["path"], "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                    "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", out], capture_output=True, timeout=3600)
    return out if os.path.exists(out) and os.path.getsize(out) else None


def switch(it, new_path, new_ext):
    it["converted_from"] = it["ext"]
    it["path"], it["ext"] = new_path, new_ext
    stem = it["name"].rsplit(".", 1)[0] if "." in it["name"] else it["name"]
    it["name"] = f"{stem}.{new_ext}"
    it["size"] = os.path.getsize(new_path)


def convert(items, workdir, rules):
    """Bring every file to a format the archive publishes. Files that can't be converted are sent for review."""
    for it in list(items):
        if it.get("outcome") or not it.get("path"):
            continue
        ext = it["ext"]
        try:
            if ext in REJECT_EXT:
                it["outcome"] = {"action": "reject", "category": "format", "reason": f".{ext} files aren't study material"}
            elif ext not in rules.ALLOWED_EXT:
                it["outcome"] = {"action": "reject", "category": "format", "reason": f".{ext or '?'} isn't an accepted format"}
            elif ext in rules.OFFICE_EXT or (ext == "numbers"):
                out = to_pdf(it, workdir)
                if out:
                    switch(it, out, "pdf")
                else:
                    it["outcome"] = {"action": "review", "reason": f"couldn't convert .{ext} to PDF; convert it by hand"}
            elif ext in rules.SHEET_EXT:
                outs = sheets_to_csv(it, workdir)
                if len(outs) == 1:
                    switch(it, outs[0], "csv")
                elif outs:
                    for o in outs:
                        sheet = os.path.splitext(os.path.basename(o))[0]
                        new_item(items, f"{it['src']}#{sheet}.csv", o, parent=it["id"], converted_from=ext)
                    it["outcome"] = {"action": "unpacked", "reason": f"spreadsheet split into {len(outs)} CSV files (one per sheet)"}
                else:
                    it["outcome"] = {"action": "review", "reason": "couldn't convert the spreadsheet to CSV"}
            elif ext in ("heic", "heif", "webp", "gif") or (ext == "png" and png_is_photo(it["path"])):
                out = photo_to_jpeg(it, workdir)
                if out:
                    switch(it, out, "jpg")
                else:
                    it["outcome"] = {"action": "review", "reason": f"couldn't convert .{ext} to JPEG"}
            elif ext in ("mov", "webm", "mkv"):
                out = video_to_mp4(it, workdir)
                if out:
                    switch(it, out, "mp4")
                else:
                    it["outcome"] = {"action": "review", "reason": f"couldn't convert .{ext} to MP4"}
            elif ext == "jpeg":
                it["ext"] = "jpg"
                it["name"] = it["name"][:-5] + ".jpg"
        except (subprocess.TimeoutExpired, OSError, ValueError) as e:
            it["outcome"] = {"action": "review", "reason": f"conversion failed ({type(e).__name__})"}


def md_or_txt_to_pdf(it, workdir):
    """.md/.txt that end up outside a lab's Records are published as PDF."""
    out = to_pdf(it, workdir)
    if out:
        switch(it, out, "pdf")
        return True
    return False
