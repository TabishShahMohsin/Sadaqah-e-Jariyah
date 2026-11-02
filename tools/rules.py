"""
Archive rules from CONVENTIONS.md, enforced in code.

Everything that decides where a file may land goes through `check_destination`. Claude's suggestions are
only suggestions: if this module says no, the file is not placed.
"""
import re

MAX_FILE_BYTES = 500 * 1024 * 1024
MAX_FILES = 1000

# What contributors may upload (CONVENTIONS.md §5) — kept in step with worker/src/index.js
CODE_EXT = {"c", "cpp", "h", "hpp", "java", "py", "ipynb", "asm", "s", "v", "sv", "vhd", "vhdl", "circ", "m", "sql",
            "js", "ts", "json", "xml", "tex", "r", "cs", "go", "rs", "kt", "sh", "pl", "lab"}
TEXT_EXT = {"txt", "md", "csv"} | CODE_EXT
OFFICE_EXT = {"pptx", "ppt", "docx", "doc", "odt", "odp", "rtf", "pages", "key"}
SHEET_EXT = {"xlsx", "xls", "ods", "numbers"}
PHOTO_EXT = {"jpg", "jpeg", "png", "webp", "heic", "heif", "gif"}
VIDEO_EXT = {"mp4", "mov", "webm", "mkv"}
ALLOWED_EXT = {"pdf", "zip"} | OFFICE_EXT | SHEET_EXT | PHOTO_EXT | VIDEO_EXT | TEXT_EXT
# What the archive publishes after conversion. .txt only as program output in lab Records.
ARCHIVE_EXT = {"pdf", "jpg", "jpeg", "png", "csv", "mp4", "txt"} | CODE_EXT
UMBRELLAS = {"BTech-and-BE"}  # degree umbrellas above the branch folders

SECTIONS = ["Syllabus", "Complete-Notes", "Unit-1", "Unit-2", "Unit-3", "Unit-4", "Unit-5",
            "PYQs-and-Assignments", "Books-and-Resources", "Manuals", "Records"]
LAB_ONLY = {"Manuals", "Records"}
BRANCH_FOLDERS = {"_Curriculum", "_General-PYQs"}

CODE = r"[A-Z]{2,4}\d{3,4}[A-Z]?"
COURSE_RE = re.compile(rf"^(?:{CODE}(?:-{CODE}){{0,2}}|(?:DE|OE)-\d{{1,2}})_[A-Z0-9][A-Za-z0-9]*(?:-[A-Za-z0-9&]+){{0,12}}$")
BRANCH_RE = re.compile(r"^[A-Z][A-Za-z]+(?:-[A-Z][A-Za-z]+){0,4}$")
TERM_RE = re.compile(r"^(?:Sem[1-8]|Phys-Sem|Chem-Sem|Electives|_Old-Syllabus-Subjects)$")
FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._()\-]{0,150}\.[a-z0-9]{1,5}$")
# Names already in the archive may contain spaces etc.; only *new* names must match FILENAME_RE.


def ext_of(name):
    i = name.rfind(".")
    return name[i + 1:].lower() if i > 0 else ""


def safe_name(name, limit=120):
    """Turn any uploaded file name into something harmless (used for holding-area keys, never for final names)."""
    name = name.replace("\\", "/").split("/")[-1]
    stem, ext = name.rsplit(".", 1) if "." in name else (name, "")
    stem = re.sub(r"[^A-Za-z0-9._()-]+", "-", stem).strip("-.") or "file"
    ext = re.sub(r"[^a-z0-9]", "", ext.lower())[:5]
    return (stem[:limit] + ("." + ext if ext else ""))


def magic_ok(ext, head: bytes):
    """Check the first bytes really match the claimed type."""
    ext = ext.lower()
    if ext == "pdf":
        return head[:1024].find(b"%PDF-") != -1
    if ext == "png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in ("jpg", "jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext == "webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if ext in ("pptx", "docx", "xlsx"):
        return head.startswith(b"PK\x03\x04")
    if ext in ("ppt", "doc", "xls"):
        return head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    if ext in ("mp4", "mov"):
        return head[4:8] in (b"ftyp", b"moov", b"wide", b"mdat", b"free")
    if ext == "webm":
        return head.startswith(b"\x1a\x45\xdf\xa3")
    if ext in TEXT_EXT:
        if b"\x00" in head:
            return False
        try:
            head.decode("utf-8")
            return True
        except UnicodeDecodeError as e:  # a multi-byte char cut at the end of the sample is fine
            return e.start >= len(head) - 3
    return False


def check_upload(name, size):
    ext = ext_of(name)
    if ext not in ALLOWED_EXT:
        return f".{ext or '?'} files aren't accepted"
    if size <= 0:
        return "file is empty"
    if size > MAX_FILE_BYTES:
        return "file is larger than 500 MB"
    return None


def is_course_name(n):
    return bool(COURSE_RE.match(n))


def check_destination(dir_path, filename, original_ext, known_courses, new_courses=()):
    """
    dir_path: folder inside the archive, e.g. 'Computer-Engineering/Sem3/COC2072-AIC2072_Digital-Logic-and-System-Design/Unit-1'
    known_courses: set of existing course folder paths; new_courses: course paths approved in the same change.
    Returns None if fine, else a reason string.
    """
    if not isinstance(dir_path, str) or not isinstance(filename, str):
        return "missing folder or file name"
    parts = dir_path.strip("/").split("/")
    if any(p in ("", ".", "..") or p.startswith(".") for p in parts):
        return "invalid folder path"
    if not FILENAME_RE.match(filename):
        return "file name has characters that aren't allowed"
    if ext_of(filename) != original_ext.lower():
        return "file extension must not change"
    pre = []
    if parts[0] in UMBRELLAS:  # 'BTech-and-BE/Branch/...': the umbrella is part of every catalog path
        pre, parts = parts[:1], parts[1:]
        if not parts:
            return "files go inside a branch"
    if not BRANCH_RE.match(parts[0]):
        return f"'{parts[0]}' is not a branch folder"

    if len(parts) == 2 and parts[1] in BRANCH_FOLDERS:
        return None
    if parts[0] == "Shared-Courses" and len(parts) in (3, 4) and not TERM_RE.match(parts[1]):
        # a course several branches take lives directly in Shared-Courses (no term folder): Shared-Courses/Course/Section
        course_path = "/".join(pre + parts[:2])
        if course_path not in known_courses and course_path not in new_courses:
            return f"course folder '{course_path}' doesn't exist and wasn't proposed as new"
        if parts[2] not in SECTIONS:
            return f"'{parts[2]}' is not a section"
        if len(parts) == 4 and not (parts[2].startswith("Unit-") and parts[3] == "Video-Lectures" and original_ext.lower() in ("mp4", "mov", "webm")):
            return "only videos go in Unit-N/Video-Lectures"
        return None
    if len(parts) not in (4, 5):
        return "files go inside a course section (Branch/Term/Course/Section)"
    branch, term, course, section = parts[:4]
    if not TERM_RE.match(term):
        return f"'{term}' is not a semester folder"
    course_path = "/".join(pre + parts[:3])
    if course_path not in known_courses and course_path not in new_courses:
        return f"course folder '{course_path}' doesn't exist and wasn't proposed as new"
    if course_path in new_courses and not is_course_name(course):
        return f"new course folder name '{course}' doesn't follow CODE_Title"
    if section not in SECTIONS:
        return f"'{section}' is not a section"
    if len(parts) == 5 and not (section.startswith("Unit-") and parts[4] == "Video-Lectures"):
        return "only Unit-N/Video-Lectures may be nested"
    if len(parts) == 5 and original_ext.lower() not in ("mp4", "mov", "webm"):
        return "only videos go in Video-Lectures"
    return None


def check_format(dir_path, filename):
    """Formats the archive publishes (CONVENTIONS.md §5). Only applied to new placements; older files are reported by conform."""
    ext = ext_of(filename)
    if ext not in ARCHIVE_EXT:
        return f".{ext} isn't a published format (convert it first)"
    if ext == "txt" and "/Records" not in "/" + dir_path.strip("/") + "/" and not dir_path.rstrip("/").endswith("Records"):
        return ".txt is only kept as program output in a lab's Records"
    return None


def unique_path(path, taken):
    if path not in taken:
        return path
    stem, ext = path.rsplit(".", 1)
    n = 2
    while f"{stem}_{n}.{ext}" in taken:
        n += 1
    return f"{stem}_{n}.{ext}"
