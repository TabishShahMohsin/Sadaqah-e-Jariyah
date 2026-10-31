#!/usr/bin/env python3
"""
Remove hidden personal data from files: photo EXIF (GPS location, owner, camera serial), PDF author fields,
Office document author fields. Content is left as it is.

The contribution pipeline runs this on every incoming file, so contributors stay anonymous.
For the existing archive (images only by default; originals are copied to _Inbox first):

    python3 tools/scrub.py --check            list files that carry hidden personal data
    python3 tools/scrub.py --fix              clean images (originals kept in _Inbox/_Originals-before-anonymising)
    python3 tools/scrub.py --fix --all-types  also PDF and Office metadata
"""
import argparse
import io
import os
import re
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BACKUP = os.path.join(REPO, "_Inbox", "_Originals-before-anonymising")

IMAGE_EXT = {"jpg", "jpeg", "png", "webp"}
OFFICE_EXT = {"docx", "pptx", "xlsx"}
PERSONAL_EXIF = {0x8825: "GPS location", 0x013B: "artist", 0x8298: "copyright", 0xA430: "owner name",
                 0xA431: "camera serial", 0x9C9D: "author", 0x9C9C: "comment", 0x010E: "description"}


def image_findings(path):
    from PIL import Image
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            found = [name for tag, name in PERSONAL_EXIF.items() if exif.get(tag)]
            xmp = im.info.get("xmp") or im.info.get("XML:com.adobe.xmp")
            if xmp:
                found.append("XMP metadata")
            return found, bool(exif) or bool(xmp)
    except Exception:  # noqa: BLE001
        return [], False


def scrub_image(path):
    """Re-save without EXIF/XMP, applying the EXIF rotation first so photos stay upright."""
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        fmt = im.format
        exif = im.getexif()
        if not exif and not im.info.get("xmp"):
            return False
        im = ImageOps.exif_transpose(im)
        icc = im.info.get("icc_profile")
        buf = io.BytesIO()
        kw = {"icc_profile": icc} if icc else {}
        if fmt == "JPEG":
            if im.mode not in ("RGB", "L", "CMYK"):
                im = im.convert("RGB")
            im.save(buf, "JPEG", quality=95, optimize=True, **kw)
        elif fmt == "PNG":
            im.save(buf, "PNG", optimize=True, **kw)
        elif fmt == "WEBP":
            im.save(buf, "WEBP", quality=95, **kw)
        else:
            return False
    with open(path, "wb") as fh:
        fh.write(buf.getvalue())
    return True


PDF_FIELDS = ("/Author", "/Creator", "/Producer", "/Title", "/Subject", "/Keywords")


def pdf_findings(path):
    try:
        from pypdf import PdfReader
        r = PdfReader(path)
        info = r.metadata or {}
        found = [k[1:] for k in ("/Author",) if info.get(k)]
        if "/Metadata" in r.trailer["/Root"]:
            found.append("XMP metadata")
        return found
    except Exception:  # noqa: BLE001
        return []


def scrub_pdf(path):
    from pypdf import PdfReader, PdfWriter
    r = PdfReader(path)
    if r.is_encrypted:
        return False
    w = PdfWriter(clone_from=r)
    w.add_metadata({"/Author": "", "/Creator": "", "/Producer": "", "/Subject": "", "/Keywords": ""})
    if "/Metadata" in w._root_object:
        del w._root_object["/Metadata"]
    buf = io.BytesIO()
    w.write(buf)
    with open(path, "wb") as fh:
        fh.write(buf.getvalue())
    return True


def office_findings(path):
    try:
        with zipfile.ZipFile(path) as z:
            core = z.read("docProps/core.xml").decode("utf-8", "ignore") if "docProps/core.xml" in z.namelist() else ""
        return ["author"] if re.search(r"<dc:creator>[^<]+|<cp:lastModifiedBy>[^<]+", core) else []
    except Exception:  # noqa: BLE001
        return []


def scrub_office(path):
    with zipfile.ZipFile(path) as zin:
        items = [(i, zin.read(i.filename)) for i in zin.infolist()]
    changed = False
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info, data in items:
            if info.filename == "docProps/core.xml":
                s = data.decode("utf-8", "ignore")
                s2 = re.sub(r"(<dc:creator>)[^<]*(</dc:creator>)", r"\1\2", s)
                s2 = re.sub(r"(<cp:lastModifiedBy>)[^<]*(</cp:lastModifiedBy>)", r"\1\2", s2)
                changed |= s2 != s
                data = s2.encode("utf-8")
            elif info.filename == "docProps/app.xml":
                s = data.decode("utf-8", "ignore")
                s2 = re.sub(r"(<Company>)[^<]*(</Company>)", r"\1\2", s)
                s2 = re.sub(r"(<Manager>)[^<]*(</Manager>)", r"\1\2", s2)
                changed |= s2 != s
                data = s2.encode("utf-8")
            zout.writestr(info, data)
    if changed:
        with open(path, "wb") as fh:
            fh.write(out.getvalue())
    return changed


def clean(path, ext, all_types=True):
    """Clean one file in place. Returns True if it changed. Never raises: a file that can't be cleaned is left as is."""
    ext = ext.lower()
    try:
        if ext in IMAGE_EXT:
            return scrub_image(path)
        if all_types and ext == "pdf":
            return bool(pdf_findings(path)) and scrub_pdf(path)
        if all_types and ext in OFFICE_EXT:
            return scrub_office(path)
    except Exception as e:  # noqa: BLE001
        print(f"warning: could not clean {os.path.basename(path)}: {type(e).__name__}")
    return False


def findings(path, ext, all_types):
    if ext in IMAGE_EXT:
        return image_findings(path)[0]
    if all_types and ext == "pdf":
        return pdf_findings(path)
    if all_types and ext in OFFICE_EXT:
        return office_findings(path)
    return []


def main():
    sys.path.insert(0, HERE)
    import sej
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--all-types", action="store_true", help="PDF and Office files too (images only by default)")
    a = ap.parse_args()
    todo = []
    for rel, fp in sej.iter_local():
        ext = rel.rsplit(".", 1)[-1].lower()
        f = findings(fp, ext, a.all_types)
        if f:
            todo.append((rel, fp, ext, f))
    for rel, _, _, f in todo:
        print(f"  {', '.join(f):32s} {rel}")
    print(f"{len(todo)} files carry hidden personal data")
    if a.fix and todo:
        for rel, fp, ext, _ in todo:
            dst = os.path.join(BACKUP, rel)
            if not os.path.exists(dst):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(fp, dst)
            if clean(fp, ext, a.all_types):
                print("cleaned", rel)
        print(f"Originals kept in {os.path.relpath(BACKUP, REPO)}/. Run `python3 tools/sej.py import --force` before the first upload.")


if __name__ == "__main__":
    main()
