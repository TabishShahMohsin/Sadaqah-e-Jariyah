"""The review page: every file of the upload and what happened to it, the things that need a person first."""
import json
from collections import Counter, defaultdict


LABEL = {"place": "Placed", "credit": "Already in the archive (credited)", "duplicate": "Already in the archive (credited)",
         "twin": "Copy of another file in this upload", "link": "Link entries", "review": "Needs a person",
         "reject": "Not added", "ignored": "System files ignored", "unpacked": "Opened (zip or multi-sheet file)"}
ORDER = ["review", "place", "link", "duplicate", "credit", "twin", "reject", "unpacked", "ignored"]


def md(s):
    return str(s).replace("|", "\\|").replace("\n", " ").replace("@", "@\u200b")


def counts(items):
    return Counter((it.get("outcome") or {}).get("action", "none") for it in items)


def coverage_ok(items):
    missing = [it["id"] for it in items if not it.get("outcome")]
    return not missing, missing


def course_of(path):
    parts = path.split("/")
    import rules  # tools/ is on sys.path when the sorter runs
    k = rules.umbrella_depth(parts)
    return "/".join(parts[:k + 3]) if len(parts) > k + 3 else "/".join(parts[:-1])


def markdown(items, title, link=None, extra=None):
    c = counts(items)
    ok, missing = coverage_ok(items)
    head = [f"## {title}", "",
            f"**{len(items)} files in → " + ", ".join(f"{c[a]} {LABEL[a].lower()}" for a in ORDER if c.get(a)) + "**",
            "", "✅ every file has an outcome" if ok else f"❌ no outcome for {len(missing)} files: {', '.join(missing[:20])}", ""]
    if extra:
        head += extra + [""]
    out = head
    by = defaultdict(list)
    for it in items:
        by[(it.get("outcome") or {}).get("action", "none")].append(it)

    def src(it):
        s = md(it["src"])
        return f"[{s}]({link(it)})" if link and link(it) else s

    if by.get("review"):
        out += ["### ⚠️ Needs a person", "", "| File | What to check | Suggested place |", "|---|---|---|"]
        out += [f"| {src(it)} | {md(it['outcome'].get('reason', ''))} | {md(it['outcome'].get('suggested', ''))} |" for it in by["review"]]
        out.append("")
    if by.get("place"):
        out += ["### Placed", ""]
        groups = defaultdict(list)
        for it in by["place"]:
            groups[course_of(it["outcome"]["path"])].append(it)
        for course in sorted(groups):
            out += [f"**{md(course)}**", "", "| From | Saved as | Details | Sure |", "|---|---|---|---|"]
            for it in sorted(groups[course], key=lambda x: x["outcome"]["path"]):
                o = it["outcome"]
                rel = o["path"][len(course) + 1:]
                det = ", ".join(f"{k}: {v}" for k, v in (o.get("meta") or {}).items() if k != "credit")
                conf = o.get("confidence")
                sure = "" if conf is None else (f"{conf:.0%}" + (" ⚠️" if conf < 0.6 else ""))
                fl = " ".join(f"⚠️ {f}" for f in o.get("flags", []))
                out.append(f"| {src(it)} | `{md(rel)}` | {md(det)} {fl} | {sure} |")
            out.append("")
    if by.get("link"):
        out += ["### Link entries", "", "| Link | Shown as | In |", "|---|---|---|"]
        out += [f"| {md(it['outcome']['url'])} | {md(it['outcome']['title'])} | `{md(it['outcome']['folder'])}` |" for it in by["link"]]
        out.append("")
    dups = by.get("duplicate", []) + by.get("credit", [])
    if dups:
        out += ["### Already in the archive (the existing copy gets the credit)", "", "| From | Same as | How we know |", "|---|---|---|"]
        out += [f"| {src(it)} | `{md(it['outcome']['target'])}` | {md(it['outcome'].get('reason', ''))} |" for it in dups]
        out.append("")
    if by.get("twin"):
        ids = {it["id"]: it for it in items}
        out += ["### Copies inside this upload (one copy kept)", "", "| File | Same as |", "|---|---|"]
        out += [f"| {src(it)} | {md(ids[it['outcome']['of']]['src'])} |" for it in by["twin"]]
        out.append("")
    if by.get("reject"):
        out += ["### Not added", "", "| File | Why |", "|---|---|"]
        out += [f"| {src(it)} | {md(it['outcome'].get('category', ''))}: {md(it['outcome'].get('reason', ''))} |" for it in by["reject"]]
        out.append("")
    small = by.get("ignored", []) + by.get("unpacked", [])
    if small:
        out += ["<details><summary>Opened or ignored (" + str(len(small)) + ")</summary>", ""]
        out += [f"- {md(it['src'])}: {md(it['outcome'].get('reason', ''))}" for it in small]
        out += ["", "</details>", ""]
    return "\n".join(out)


def manifest(items):
    """Machine-readable record of every input and its outcome (kept with the change set)."""
    keep = ("id", "src", "ext", "converted_from", "size", "orig_sha", "parent", "url")
    return [{**{k: it[k] for k in keep if it.get(k) is not None}, "outcome": it.get("outcome")} for it in items]


def dump(items, path):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest(items), fh, ensure_ascii=False, indent=1)
