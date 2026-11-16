"""Teacher registry (catalog/people.json): one canonical name per teacher, the spellings seen in files, and courses.

    {"people": [{"name": "Prof. M. M. Sufyan Beg", "aliases": ["Sufyan Sir", "M. M. Sufyan Beg"],
                 "courses": ["Faculty-of-Engineering-and-Technology/BTech-and-BE/Computer-Engineering/Sem3/COC2072-AIC2072_Digital-Logic-and-System-Design"]}]}

match() maps any spelling ("Sufyan sir", "Prof. Sufyan Beg", "Dr M M Sufyan Beg") to the canonical name.
New names are never added automatically: the sorter proposes them and a person approves (people.py add)."""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
FILE = os.path.join(REPO, "catalog", "people.json")
HONORIFIC = re.compile(r"(?i)\b(prof(essor)?|dr|mr|mrs|ms|er|sir|sahab|ma'?am|madam|mam)\b\.?")


def key_tokens(name):
    s = HONORIFIC.sub(" ", name or "")
    s = re.sub(r"[^A-Za-z ]", " ", s).lower()
    return [t for t in s.split() if len(t) > 1 and t not in ("md", "mohd", "mohammad", "muhammad", "mohammed", "syed", "sheikh")]


class Registry:
    def __init__(self, path=FILE):
        self.path = path
        self.people = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                self.people = json.load(fh).get("people", [])

    def save(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"people": sorted(self.people, key=lambda p: p["name"])}, fh, indent=1, ensure_ascii=False)
            fh.write("\n")

    def match(self, name, course=None):
        """Best registry entry for a spelling, or None. Prefers teachers of the given course."""
        toks = set(key_tokens(name))
        if not toks:
            return None
        best, score = None, 0.0
        for p in self.people:
            for spelling in [p["name"], *p.get("aliases", [])]:
                st = set(key_tokens(spelling))
                if not st:
                    continue
                common = toks & st
                if not common:
                    continue
                same_course = bool(course and course in p.get("courses", []))
                if len(common) == 1 and not same_course and (len(toks) == 1 or len(st) == 1):
                    continue                   # one shared word ("Khan", "Talib") is a different person unless same course
                s = len(common) / len(toks | st)
                if toks <= st or st <= toks:   # "Sufyan Beg" inside "M M Sufyan Beg"
                    s = max(s, 0.6)
                if same_course:
                    s += 0.3
                if s > score:
                    best, score = p, s
        return best if score >= 0.6 else None

    def of_course(self, course):
        return [p["name"] for p in self.people if course in p.get("courses", [])]

    def add(self, name, alias=None, course=None):
        p = next((x for x in self.people if x["name"] == name), None)
        if not p:
            p = {"name": name, "aliases": [], "courses": []}
            self.people.append(p)
        if alias and alias != name and alias not in p["aliases"]:
            p["aliases"].append(alias)
        if course and course not in p["courses"]:
            p["courses"].append(course)
        return p


def seed(state, course_of):
    """First registry from the professors already recorded in the catalog; spellings that clearly share a person merge."""
    reg = Registry(path=FILE + ".new")
    reg.people = []
    seen = {}
    for p, e in state.items():
        prof = ((e.get("meta") or {}).get("professor") or "").strip()
        if prof:
            seen.setdefault(prof, set()).add(course_of(p))
    for name in sorted(seen, key=lambda n: (-len(key_tokens(n)), n)):   # fullest spelling first becomes canonical
        for c in seen[name]:
            hit = reg.match(name, course=c)
            reg.add(hit["name"] if hit else name, alias=name if hit and hit["name"] != name else None, course=c)
    return reg


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import sej  # noqa: E402
    if sys.argv[1:2] == ["seed"]:
        st = sej.load_state()
        import rules  # noqa: E402
        r = seed(st, rules.course_prefix)
        r.save()
        print(f"{len(r.people)} teachers → {r.path}")
        for x in r.people:
            print(f"  {x['name']:35} aliases={x['aliases']} courses={len(x['courses'])}")
    elif sys.argv[1:2] == ["match"]:
        r = Registry()
        for n in sys.argv[2:]:
            m = r.match(n)
            print(n, "→", m["name"] if m else None)
