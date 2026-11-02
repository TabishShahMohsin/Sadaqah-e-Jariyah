"""Duplicates. Exact copies are settled in code; look-alikes become hints the agent checks by eye."""
import re

from . import evidence


COPY_MARK = re.compile(r"(?i)\(\d+\)|\bcopy\b|^\.trashed-|\s-\s?copy|_\d{6,}(?=\.)")


def keeper_rank(it):
    """Which of several identical files to keep: the one whose name isn't a download/trash copy, then the shortest path."""
    name = it["src"].rsplit("/", 1)[-1]
    return (bool(COPY_MARK.search(name)), len(it["src"]), it["src"])


def exact(items, archive):
    """Same bytes as an archive file → credit that file; same bytes as another file in this upload → twin of the best-named copy."""
    groups = {}
    for it in items:
        if it.get("outcome") or not it.get("path"):
            continue
        hit = archive.sha_index.get(it.get("orig_sha")) or archive.sha_index.get(it.get("sha"))
        if hit:
            it["outcome"] = {"action": "credit", "target": hit, "reason": "identical to a file already in the archive", "auto": True}
            continue
        groups.setdefault(it.get("sha") or it.get("orig_sha"), []).append(it)
    for g in groups.values():
        if len(g) < 2:
            continue
        g.sort(key=keeper_rank)
        for it in g[1:]:
            it["outcome"] = {"action": "twin", "of": g[0]["id"], "reason": "identical copy of another file in this upload", "auto": True}


def batch_lookalikes(items):
    """Pairs inside the upload with the same text, or a near-identical first page, for the agent to confirm."""
    open_items = [it for it in items if not it.get("outcome") and it.get("info")]
    by_tsig = {}
    for it in open_items:
        t = it["info"].get("tsig")
        if t:
            by_tsig.setdefault((t, it["info"].get("pages")), []).append(it["id"])
    hints = {}
    for ids in by_tsig.values():
        for i in ids[1:]:
            hints.setdefault(i, []).append({"id": ids[0], "why": "same text and page count"})
    vs = [(it["id"], it["info"].get("vsig"), it["info"].get("pages")) for it in open_items if it["info"].get("vsig")]
    for a in range(len(vs)):
        for b in range(a):
            s = evidence.sig_sim(vs[a][1], vs[b][1])
            if s > 0.9 and vs[a][2] == vs[b][2]:
                hints.setdefault(vs[a][0], []).append({"id": vs[b][0], "why": f"first pages look the same (similarity {s:.2f})"})
    for it in open_items:
        if it["id"] in hints:
            it["lookalikes"] = hints[it["id"]][:3]


def against_course(it, archive, course_path, limit=5):
    """Archive files in one course that resemble this file: same text, overlapping text, or a matching first page."""
    files = [p for p in archive.by_course.get(course_path, []) if p.rsplit(".", 1)[-1].lower() in ("pdf", "jpg", "jpeg", "png")]
    info = it.get("info", {})
    out = []
    for p in files[:400]:
        fp = archive.fingerprint(p)
        if not fp:
            continue
        score, why = 0.0, []
        if info.get("tsig") and fp.get("tsig") == info["tsig"]:
            score, why = 1.0, ["same text"]
        else:
            if info.get("mh") and fp.get("mh"):
                j = evidence.mh_sim(info["mh"], fp["mh"])
                if j > 0.25:
                    score, why = max(score, min(1.0, j * 1.6)), why + [f"text overlap {j:.2f}"]
            v = evidence.sig_sim(info.get("vsig"), fp.get("vsig"))
            if v > 0.55:  # a look-alike first page alone (cover sheets, letterheads) is weaker evidence than shared text
                score, why = max(score, v * (1.0 if score else 0.8)), why + [f"first page similarity {v:.2f}"]
        if score:
            out.append({"path": p, "score": round(score, 2), "pages": fp.get("n"), "why": ", ".join(why)})
    out.sort(key=lambda r: -r["score"])
    return out[:limit], len(files)
