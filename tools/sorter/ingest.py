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
MAX_UNZIPPED_BYTES = 8 * 1024 ** 3
MAX_UNZIPPED_FILES = 5000
REJECT_EXT = {"exe", "msi", "dmg", "apk", "app", "bat", "cmd", "com", "scr", "out", "class", "o", "obj", "dll", "so",
              "html", "htm", "css", "svg", "lnk", "url", "webloc", "download", "crdownload", "part", "torrent", "iso"}


def new_item(items, src, path, parent=None, **kw):
    it = {"id": f"f{len(items) + 1:04d}", "src": src, "name": os.path.basename(src.rstrip("/")) or src,
          "path": path, "orig_path": path, "parent": parent, **kw}
    it["ext"] = util.ext_of(it["name"])
    if path and os.path.exists(path):
        it["size"] = os.path.getsize(path)
        it["orig_sha"] = util.sha256(path)
    items.append(it)
    return it


def from_folder(root, items):
    """Every file under a local folder becomes an item; its path inside the folder is kept (the strongest clue)."""
    base = os.path.dirname(os.path.abspath(root).rstrip("/"))
    for dp, dns, fns in os.walk(root):
        dns.sort()
        for f in sorted(fns):
            fp = os.path.join(dp, f)
            if os.path.islink(fp):
                continue
            new_item(items, os.path.relpath(fp, base), fp)
    return items


def settle_junk(items):
    for it in items:
        if it.get("outcome"):
            continue
        if JUNK.search(it["src"]):
            it["outcome"] = {"action": "ignored", "reason": "system file (not part of the material)"}
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
        env = {**os.environ, "HOME": prof}
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


# Bullets drawn from symbol fonts that have no free stand-in come out as empty boxes; swap them for
# ordinary characters before converting. Plain Wingdings is left alone: fonts.conf provides it.
SLIDE_EXT = {"ppt", "pptx", "pps", "ppsx", "pot", "potx", "odp"}
SYMBOL_BULLET_FONTS = {"wingdings 2": "\u25aa", "wingdings 3": "\u25ba", "monotype sorts": "\u25aa",
                       "webdings": "\u25aa", "marlett": "\u25aa", "zapfdingbats": "\u25aa", "zapf dingbats": "\u25aa"}
BULLET_RE = re.compile(r'<a:buFont typeface="([^"]+)"[^>]*/>\s*<a:buChar char="[^"]*"/>')


def _swap_bullets(xml):
    def sub(m):
        ch = SYMBOL_BULLET_FONTS.get(m.group(1).lower())
        return f'<a:buFont typeface="Arial"/><a:buChar char="{ch}"/>' if ch else m.group(0)
    return BULLET_RE.sub(sub, xml)


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
            fixed = {n: _swap_bullets(x) for n, x in xml.items()}
            if all(fixed[n] == xml[n] for n in xml):
                return path
            out = os.path.join(out_dir, "_fixed", os.path.splitext(os.path.basename(path))[0] + ".pptx")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
                for n in names:
                    zout.writestr(zin.getinfo(n), fixed[n].encode("utf-8") if n in fixed else zin.read(n))
            return out
    except (zipfile.BadZipFile, UnicodeDecodeError, KeyError):
        return path


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
