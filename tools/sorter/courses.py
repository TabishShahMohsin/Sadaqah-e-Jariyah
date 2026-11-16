"""What the agent can look up about the archive: courses from the official curricula, what each course already holds,
and fingerprints of archive files to compare against."""
import json
import os
import re

from . import evidence, util

ALIASES_FILE = "catalog/aliases.json"   # short names students use → course code, grown from reviews
FP_FILE = "catalog/fingerprints.jsonl.gz"  # {p, h, n, tsig, mh, vsig} per archive file (tools/fingerprint.py)


def record(p, info, sha=None):
    return {"p": p, "h": sha, "n": info.get("pages", 0), "tsig": info.get("tsig"), "mh": info.get("mh"), "vsig": info.get("vsig")}


def parse_codes(folder):
    head = folder.split("_", 1)[0]
    return [c for c in head.split("-") if re.match(r"^[A-Z]{2,5}\d[0-9A-Z]\d{1,2}[A-Z]?$", c)]


def norm_code(s):
    return re.sub(r"[^A-Z0-9]", "", s.upper())


class Archive:
    def __init__(self, repo, state, sej, rules, local_root=None):
        self.repo, self.state, self.rules = repo, state, rules
        self.local_root = local_root  # set when the archive files themselves are on this machine
        pg_path = os.path.join(repo, "catalog", "programmes.json")
        self.pg = json.load(open(pg_path, encoding="utf-8")) if os.path.exists(pg_path) else {"programmes": [], "courses": {}}
        ex_path = os.path.join(repo, "catalog", "extra-courses.json")   # courses proven by material but missing from the curricula
        if os.path.exists(ex_path):
            for k, v in json.load(open(ex_path, encoding="utf-8")).get("courses", {}).items():
                self.pg.setdefault("courses", {}).setdefault(k, v)
        self.folders = sej.courses(state)  # course path → {sections}
        self.known = set(self.folders) | set(self.pg.get("courses", {}))  # the curriculum counts even before any file exists
        self.by_course = {}
        for p in state:
            for c in self.known_prefixes(p):
                self.by_course.setdefault(c, []).append(p)
        self.progs = {p["id"]: p for p in self.pg.get("programmes", [])}
        self.aliases = {}
        ap = os.path.join(repo, ALIASES_FILE)
        if os.path.exists(ap):
            self.aliases = {k.lower(): v for k, v in json.load(open(ap, encoding="utf-8")).items()}
        self.fp = {}
        fp = os.path.join(repo, FP_FILE)
        if os.path.exists(fp):
            import gzip
            with gzip.open(fp, "rt", encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        r = json.loads(line)
                        if r["p"] in state and (not r.get("h") or r["h"] == state[r["p"]].get("h")):
                            self.fp[r["p"]] = r
        self.sha_index = {}
        for p, e in state.items():
            if e.get("h"):
                self.sha_index.setdefault(e["h"], p)
        self._index = None

    def known_prefixes(self, p):
        parts = p.split("/")
        for n in (3, 4):
            cp = "/".join(parts[:n])
            if cp in self.folders:
                return [cp]
        return []

    # ── search ────────────────────────────────────────────────────────────
    def entries(self):
        if self._index is None:
            idx = []
            meta = self.pg.get("courses", {})
            for cp in sorted(self.known):
                m = meta.get(cp, {})
                folder = cp.split("/")[-1]
                codes = list(dict.fromkeys(parse_codes(folder) + m.get("codes", []) + m.get("be", []) + m.get("old", [])))
                title = m.get("title") or folder.split("_", 1)[-1].replace("-", " ")
                progs = []
                for pid, sems in (m.get("progs") or {}).items():
                    pr = self.progs.get(pid)
                    if pr:
                        progs.append(f"{pr['degree']} {pr['name']} sem {','.join(map(str, sems))}")
                idx.append({"path": cp, "title": title, "codes": codes, "programmes": progs,
                            "kind": m.get("kind", ""), "sections": self.folders.get(cp, {}).get("sections", {}),
                            "norm": {norm_code(c) for c in codes}, "words": set(re.findall(r"[a-z0-9]+", (title + " " + cp).lower()))})
            self._index = idx
        return self._index

    def find(self, query, limit=8):
        q = (query or "").strip()
        ql = q.lower()
        if ql in self.aliases:
            q = self.aliases[ql]
            ql = q.lower()
        qcode = norm_code(q)
        qwords = set(re.findall(r"[a-z0-9]+", ql)) - {"and", "of", "the", "i", "ii", "lab", "1", "2"}
        roman = {"1": "i", "2": "ii", "3": "iii", "4": "iv"}
        scored = []
        for e in self.entries():
            s = 0.0
            if qcode and any(qcode == c or (len(qcode) >= 6 and (c.startswith(qcode) or qcode.startswith(c[:-1]))) for c in e["norm"]):
                s += 10
            elif qcode and len(qcode) >= 5 and any(c.endswith(qcode) or qcode.endswith(c) for c in e["norm"]):
                s += 6  # E-prefixed B.E. codes, 'ME221' vs 'EME221'
            if qwords:
                hit = len(qwords & e["words"]) / len(qwords)
                s += 5 * hit
            if "lab" in ql and "lab" in e["words"]:
                s += 1
            for d, r in roman.items():
                if re.search(rf"\b{d}\b", ql) and (r in e["words"] or d in e["words"]):
                    s += 0.5
            if s > 0:
                scored.append((s, e))
        scored.sort(key=lambda x: (-x[0], x[1]["path"]))
        return [{k: v for k, v in e.items() if k not in ("norm", "words")} | {"score": round(s, 1)} for s, e in scored[:limit]]

    def list_course(self, cp, section=None, limit=250):
        files = sorted(p for p in self.by_course.get(cp, []) if section is None or p[len(cp) + 1:].startswith(section + "/"))
        out = []
        for p in files[:limit]:
            m = self.state[p].get("meta") or {}
            out.append(p[len(cp) + 1:] + (f"  [{', '.join(f'{k}={v}' for k, v in m.items() if k in ('year', 'professor'))}]" if m else ""))
        return out, len(files)

    # ── fingerprints of archive files ─────────────────────────────────────
    def fingerprint(self, p):
        if p in self.fp:
            return self.fp[p]
        if not self.local_root:
            return None
        fp = os.path.join(self.local_root, p)
        ext = util.ext_of(p)
        if not os.path.exists(fp) or ext not in ("pdf", "jpg", "jpeg", "png"):
            return None
        it = {"path": fp, "ext": ext}
        try:
            info = evidence.inspect(it)
        except Exception:  # noqa: BLE001
            return None
        r = record(p, info)
        self.fp[p] = r
        return r

    def local_path(self, p):
        if self.local_root:
            fp = os.path.join(self.local_root, p)
            if os.path.exists(fp):
                return fp
        return None
