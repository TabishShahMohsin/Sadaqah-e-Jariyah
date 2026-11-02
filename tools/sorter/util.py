import hashlib
import re
import unicodedata


def ext_of(name):
    i = name.rfind(".")
    return name[i + 1:].lower() if i > 0 else ""


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(4 << 20), b""):
            h.update(b)
    return h.hexdigest()


def safe_rel(name):
    """A path inside an archive, made safe to extract (no absolute paths, no '..', no control characters)."""
    name = unicodedata.normalize("NFC", name.replace("\\", "/"))
    parts = [p for p in name.split("/") if p not in ("", ".", "..")]
    parts = [re.sub(r"[\x00-\x1f]", "", p)[:180] for p in parts]
    return "/".join(p for p in parts if p)


def clean_text(s, n=60):
    s = re.sub(r"[\x00-\x1f<>`@\[\]{}|]", "", str(s or "")).strip()
    return re.sub(r"\s+", " ", s)[:n]


def norm_space(s):
    return re.sub(r"[   ]", " ", s)
