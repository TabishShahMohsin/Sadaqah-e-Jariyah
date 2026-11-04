#!/usr/bin/env python3
"""
Fonts for converting slides and documents to PDF on Linux (this machine's LibreOffice, or the GitHub Action).

Lecture decks name Windows/Mac fonts that Linux doesn't have. tools/sorter/fonts.conf maps each one to a free font
of the same width; this script installs the free fonts that aren't Debian packages, and builds a working "Symbol"
font (old Office files address Symbol at U+F020–F0FF; the free clones answer only at 0x20–0xFF, so every α, Θ, ≤
came out as a box).

    python3 tools/sorter/install_fonts.py         # into ~/.local/share/fonts/sej (no root needed); safe to re-run

Debian packages used alongside (apt): fonts-crosextra-carlito fonts-crosextra-caladea fonts-liberation fonts-wine
fonts-adf-gillius fonts-roboto-slab fonts-urw-base35 fonts-texgyre fonts-open-sans fonts-noto-core fonts-noto-cjk
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import urllib.request

DEST = os.path.join(os.path.expanduser("~"), ".local", "share", "fonts", "sej")
# Google Fonts (SIL Open Font License), by folder name in github.com/google/fonts/tree/main/ofl
GOOGLE = ["gelasio", "comicneue", "librefranklin", "archivoblack", "anton", "montserrat", "nunito", "raleway", "roboto",
          "robotoserif", "karla", "amaticsc", "prompt", "dosis", "titilliumweb", "comfortaa", "merriweather", "lato",
          "poppins", "sourcesans3", "oswald", "ebgaramond", "firasans", "playfairdisplay", "notoemoji"]
WINE_KEEP = ("wingding.ttf", "webdings.ttf", "marlett.ttf")  # Wine's own Symbol is replaced by the one built below


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "sadaqah-e-jariyah-fonts"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def google_fonts():
    """Sparse git checkout of just the families needed (no API rate limit); falls back to the GitHub API."""
    todo = [f for f in GOOGLE if not os.path.exists(os.path.join(DEST, "google", f, ".complete"))]
    if not todo:
        return 0
    if shutil.which("git"):
        cache = os.path.join(os.path.expanduser("~"), ".cache", "sej-google-fonts")
        try:
            if not os.path.isdir(os.path.join(cache, ".git")):
                subprocess.run(["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse",
                                "https://github.com/google/fonts", cache], check=True, timeout=600)
            subprocess.run(["git", "-C", cache, "sparse-checkout", "set"] + [f"ofl/{f}" for f in GOOGLE], check=True, timeout=1200)
            n = 0
            for fam in todo:
                src = os.path.join(cache, "ofl", fam)
                if not os.path.isdir(src):
                    continue
                d = os.path.join(DEST, "google", fam)
                os.makedirs(d, exist_ok=True)
                for name in os.listdir(src):
                    if name.endswith(".ttf"):
                        shutil.copyfile(os.path.join(src, name), os.path.join(d, name))
                        n += 1
                open(os.path.join(d, ".complete"), "w").close()
            return n
        except (subprocess.SubprocessError, OSError) as e:
            print(f"  git checkout failed ({type(e).__name__}); trying the GitHub API")
    return google_fonts_api()


def google_fonts_api():
    n = 0
    for fam in GOOGLE:
        d = os.path.join(DEST, "google", fam)
        if os.path.exists(os.path.join(d, ".complete")):
            continue
        try:
            listing = json.loads(fetch(f"https://api.github.com/repos/google/fonts/contents/ofl/{fam}"))
        except Exception as e:  # noqa: BLE001
            print(f"  {fam}: not fetched ({type(e).__name__})")
            continue
        os.makedirs(d, exist_ok=True)
        for item in listing:
            if item.get("name", "").endswith(".ttf") and not os.path.exists(os.path.join(d, item["name"])):
                data = fetch(item["download_url"])
                with open(os.path.join(d, item["name"]), "wb") as fh:
                    fh.write(data)
                n += 1
        open(os.path.join(d, ".complete"), "w").close()  # an interrupted family is finished on the next run
    return n


def find(pattern):
    roots = ["/usr/share/fonts", os.path.join(os.path.expanduser("~"), ".local", "share", "fonts"), "/usr/share/wine/fonts"]
    for r in roots:
        hits = glob.glob(os.path.join(r, "**", pattern), recursive=True)
        if hits:
            return hits[0]
    return None


def wine_fonts():
    os.makedirs(os.path.join(DEST, "wine"), exist_ok=True)
    for name in WINE_KEEP:
        src = find(name)
        if src and not os.path.exists(os.path.join(DEST, "wine", name)):
            shutil.copyfile(src, os.path.join(DEST, "wine", name))


def symbol_font():
    out = os.path.join(DEST, "Symbol-URW.otf")
    if os.path.exists(out):
        return
    src = find("StandardSymbolsPS.otf")
    if not src:
        print("  Symbol: install fonts-urw-base35 first")
        return
    from fontTools.ttLib import TTFont
    from fontTools.ttLib.tables._c_m_a_p import cmap_format_4
    f = TTFont(src)
    base = f["cmap"].getBestCmap()
    m = {}
    for code, glyph in base.items():
        if code <= 0xFF:
            m[code] = glyph
            m[0xF000 + code] = glyph
    tables = []
    for pid, eid in ((3, 0), (3, 1)):
        st = cmap_format_4(4)
        st.platformID, st.platEncID, st.language, st.cmap = pid, eid, 0, dict(m)
        tables.append(st)
    f["cmap"].tables = tables
    for rec in f["name"].names:
        if rec.nameID in (1, 4, 6, 16):
            rec.string = "Symbol"
    f["OS/2"].ulCodePageRange1 |= 1 << 31
    f.save(out)


def main():
    os.makedirs(DEST, exist_ok=True)
    try:
        import fontTools  # noqa: F401,F811  (needed by symbol_font)
    except ImportError:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "fonttools"], check=False)
    print(f"Google fonts: {google_fonts()} files added")
    wine_fonts()
    symbol_font()
    subprocess.run(["fc-cache", "-f"], capture_output=True)
    print(f"fonts ready in {DEST}")


if __name__ == "__main__":
    main()
