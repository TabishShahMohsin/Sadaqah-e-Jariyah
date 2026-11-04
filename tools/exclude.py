"""Files that stay out of the public archive (copyrighted books and anything else listed in catalog/exclude.txt).

catalog/exclude.txt, one rule per line:
    # comment
    *Books-and-Resources/*          a pattern (fnmatch, * also matches '/')
    =BTech-and-BE/.../some book.pdf  one exact path
"""
import fnmatch
import os

FILE = os.path.join("catalog", "exclude.txt")


def make(root):
    exact, pats = set(), []
    p = os.path.join(root, FILE)
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if line.startswith("="):
                exact.add(line[1:])
            else:
                pats.append(line.strip())

    def is_excluded(rel):
        return rel in exact or any(fnmatch.fnmatchcase(rel, g) for g in pats)
    return is_excluded
