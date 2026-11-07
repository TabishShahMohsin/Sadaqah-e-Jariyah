"""Every decision the agent makes is checked here before it counts. Claude proposes; code decides."""
import re

from . import util

ACTIONS = ("place", "duplicate", "twin", "link", "reject", "review")
REJECT_CATEGORIES = ("copyright", "personal_data", "not_study_material", "out_of_scope", "unreadable", "format", "still_due")
YEAR_RE = re.compile(r"^(19|20)\d\d-\d\d$")
BRANCH_LEVEL = ("_General-PYQs", "_Curriculum")


def describe_meta(d, it, course_path):
    """professor (canonical from the teacher registry), author, source, title, summary, kind. Returns (meta, flags)."""
    from . import people
    meta, flags = {}, []
    for k in ("author", "source"):
        v = util.clean_text(d.get(k), 60)
        if v:
            meta[k] = v
    prof = util.clean_text(d.get("professor"), 60)
    if prof:
        course = "/".join(course_path.split("/")[:4])
        hit = people.Registry().match(prof, course=course)
        if hit:
            meta["professor"] = hit["name"]
        else:
            meta["professor"] = prof
            flags.append(f"new teacher: {prof}")
    title = util.clean_text(d.get("title"), 90)
    if title:
        meta["title"] = title
    summ = util.clean_text(d.get("summary"), 220)
    if summ:
        meta["summary"] = summ
    kind = (it.get("profile") or {}).get("kind")
    if kind and kind not in ("other",):
        meta["kind"] = kind
    return meta, flags


class Context:
    def __init__(self, archive, rules, items, permission=None):
        self.archive, self.rules, self.permission = archive, rules, permission
        self.items = {it["id"]: it for it in items}
        self.taken = set(archive.state)

    def check(self, d):
        """Return (outcome, None) or (None, error message the agent can act on)."""
        if not isinstance(d, dict):
            return None, "each decision must be an object"
        it = self.items.get(d.get("id"))
        if not it:
            return None, f"unknown id {d.get('id')!r}"
        if it.get("outcome") and not it["outcome"].get("by_agent"):
            return None, f"{it['id']} was already settled ({it['outcome']['action']}); leave it"
        action = d.get("action")
        if action not in ACTIONS:
            return None, f"action must be one of {', '.join(ACTIONS)}"
        reason = util.clean_text(d.get("reason"), 300)
        conf = d.get("confidence")
        conf = float(conf) if isinstance(conf, (int, float)) else None
        base = {"action": action, "reason": reason, "by_agent": True}
        if conf is not None:
            base["confidence"] = max(0.0, min(1.0, conf))
        flags = [util.clean_text(f, 60) for f in (d.get("flags") or []) if isinstance(f, str)][:4]
        if flags:
            base["flags"] = flags

        if action == "place":
            return self._place(it, d, base)
        if action == "duplicate":
            t = str(d.get("of") or "").strip()
            if t not in self.archive.state:
                return None, f"'{t}' is not a file in the archive (use the exact path from list_course or similar)"
            return {**base, "target": t}, None
        if action == "twin":
            t = str(d.get("of") or "").strip()
            if t not in self.items or t == it["id"]:
                return None, "twin needs 'of': the id of the other file in this upload that should be kept"
            return {**base, "of": t}, None
        if action == "reject":
            cat = d.get("category")
            if cat not in REJECT_CATEGORIES:
                return None, f"reject needs a category: {', '.join(REJECT_CATEGORIES)}"
            if cat == "copyright" and self.permission:
                return None, (f"this upload carries permission ({self.permission}); place books under Books-and-Resources "
                              "instead of rejecting them for copyright")
            if not reason:
                return None, "say why in 'reason' (the contributor sees it)"
            return {**base, "category": cat}, None
        if action == "review":
            if not reason:
                return None, "say what a person needs to check in 'reason'"
            sugg = {}
            if d.get("folder") and d.get("name"):
                sugg = {"suggested": f"{str(d['folder']).strip('/')}/{d['name']}"}
            return {**base, **sugg}, None
        if action == "link":
            if it["ext"] != "url":
                return None, "only link inputs can be recorded as links"
            folder = str(d.get("folder") or "").strip().strip("/")
            title = util.clean_text(d.get("name") or d.get("title"), 120)
            if not title:
                return None, "a link needs a short title in 'name'"
            err = self._folder_ok(folder)
            if err:
                return None, err
            return {**base, "folder": folder, "title": title, "url": it["url"]}, None
        return None, "unhandled action"

    def _folder_ok(self, folder):
        parts = folder.split("/")
        k = 1 if parts and parts[0] in self.rules.UMBRELLAS else 0
        if len(parts) == k + 2 and parts[k + 1] in BRANCH_LEVEL:
            return None
        err = self.rules.check_destination(folder, "x.pdf", "pdf", self.archive.known)
        return err

    def _place(self, it, d, base):
        folder = str(d.get("folder") or "").strip().strip("/")
        name = str(d.get("name") or "").strip()
        if "." not in name:
            name = f"{name}.{it['ext']}"
        ext = util.ext_of(name)
        target_ext = it["ext"]
        # .md/.txt outside a lab's Records are published as PDF (converted when applied)
        if it["ext"] in ("md", "txt") and not folder.endswith("/Records"):
            target_ext = "pdf"
        if ext != target_ext:
            return None, f"the name must end in .{target_ext}"
        err = self.rules.check_destination(folder, name, target_ext, self.archive.known)
        if err:
            parts = folder.split("/")
            k = 1 if parts and parts[0] in self.rules.UMBRELLAS else 0
            if not (len(parts) == k + 2 and parts[k + 1] in BRANCH_LEVEL and self.rules.FILENAME_RE.match(name)):
                hint = ""
                if "doesn't exist" in err:
                    hint = " — use find_courses to get the exact course path; a course missing from the curriculum goes to review"
                return None, err + hint
        err = self.rules.check_format(folder, name)
        if err:
            return None, err
        meta, flags = describe_meta(d, it, folder)
        if flags:
            base["flags"] = (base.get("flags", []) + flags)[:6]
        y = str(d.get("year") or "").strip()
        if y:
            if not YEAR_RE.match(y):
                return None, "year must look like 2024-25"
            meta["year"] = y
        path = self.rules.unique_path(f"{folder}/{name}", self.taken)
        prev = (it.get("outcome") or {}).get("path")
        if prev and prev != path:
            self.taken.discard(prev)
        self.taken.add(path)
        out = {**base, "path": path, "meta": meta}
        if target_ext != it["ext"]:
            out["convert_to"] = target_ext
        return out, None


def resolve_twins(items):
    """A twin of a twin points at the file that is actually kept; a twin of a rejected file is rejected the same way."""
    by = {it["id"]: it for it in items}
    for it in items:
        o = it.get("outcome") or {}
        seen = set()
        while o.get("action") == "twin" and o.get("of") in by and o["of"] not in seen:
            seen.add(o["of"])
            other = by[o["of"]].get("outcome") or {}
            if other.get("action") == "twin":
                o["of"] = other["of"]
                continue
            if other.get("action") in ("reject", "ignored"):
                it["outcome"] = {**other, "reason": f"same as {o['of']}: {other.get('reason', '')}", "auto": True}
            break
