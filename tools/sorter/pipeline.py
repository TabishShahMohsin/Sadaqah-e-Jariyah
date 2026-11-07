"""Run the sorter on a set of items and turn its outcomes into catalog operations."""
import json
import os
import shutil
import time

from . import agent, decide, dedup, evidence, ingest, report, util


def sort(items, *, archive, rules, workdir, upload_text, call, conventions, permission=None, log=print):
    """Give every item exactly one outcome. Returns the agent's notes."""
    ingest.settle_junk(items)
    ingest.unpack(items, workdir)
    ingest.convert(items, workdir, rules)
    n_open = 0
    for it in items:
        if not it.get("outcome") and it.get("path"):
            evidence.inspect(it)
            n_open += 1
        elif not it.get("outcome") and it.get("ext") == "url":
            it["info"] = {"text": it.get("page_title", ""), "tl": 0}
            n_open += 1
    log(f"  {len(items)} files in, {n_open} to look at after unpacking, conversion and junk removal")
    dedup.exact(items, archive)
    dedup.batch_lookalikes(items)
    open_items = [it for it in items if not it.get("outcome")]
    log(f"  {len(open_items)} need a decision ({sum(1 for it in items if (it.get('outcome') or {}).get('auto'))} exact copies settled in code)")
    notes = []
    if open_items:
        ctx = decide.Context(archive, rules, items, permission)
        ck_path = os.path.join(workdir, "checkpoint.json")
        key = lambda it: f"{it.get('src')}|{it.get('sha', '')}"   # noqa: E731
        if os.path.exists(ck_path):   # resume: decisions from an earlier, interrupted run of this plan
            with open(ck_path, encoding="utf-8") as fh:
                ck = json.load(fh)
            n = 0
            for it in open_items:
                o = ck.get("outcomes", {}).get(key(it))
                if o and not it.get("outcome"):
                    it["outcome"] = o
                    if o.get("path"):
                        ctx.taken.add(o["path"])
                    n += 1
            notes = ck.get("notes", [])
            log(f"  resumed {n} decisions from an earlier run")

        def save(_notes):   # agent.run appends to `notes` itself
            out = {key(it): it["outcome"] for it in items if (it.get("outcome") or {}).get("by_agent")}
            tmp = ck_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"outcomes": out, "notes": notes}, fh, ensure_ascii=False)
            os.replace(tmp, ck_path)

        agent.run(items, archive, ctx, call, conventions, upload_text, log, on_batch=save, notes=notes, work=workdir)
    decide.resolve_twins(items)
    for it in items:
        if not it.get("outcome"):
            it["outcome"] = {"action": "review", "reason": "no decision was made", "auto": True}
    return notes


def merge_credit(old, who):
    names = [x.strip() for x in (old or "").split(",") if x.strip()]
    for w in [x.strip() for x in (who or "").split(",") if x.strip()]:
        if w not in names:
            names.append(w)
    return ", ".join(names)


def finalize(it, workdir, scrub):
    """The bytes that get published for a placed item: converted if needed, hidden metadata stripped.
    Works on a copy, so the contributor's original is never modified."""
    o = it["outcome"]
    if o.get("convert_to") == "pdf" and it["ext"] != "pdf":
        if not ingest.md_or_txt_to_pdf(it, workdir):
            it["outcome"] = {"action": "review", "reason": "couldn't convert to PDF", "auto": True}
            return None
    os.makedirs(os.path.join(workdir, "final"), exist_ok=True)
    final = os.path.join(workdir, "final", f"{it['id']}.{it['ext']}")
    shutil.copyfile(it["path"], final)
    scrub.clean(final, it["ext"])
    return {"path": final, "size": os.path.getsize(final), "sha": util.sha256(final)}


def ops_for(items, state, finals, credit, permission=None, staged=None):
    """Catalog operations for the outcomes. finals: id → {path,size,sha}; staged: id → holding-area key (website flow)."""
    t = int(time.time())
    ops, credited = [], {}
    for it in items:
        o = it.get("outcome") or {}
        a = o.get("action")
        if a == "place" and it["id"] in finals:
            f = finals[it["id"]]
            meta = {k: v for k, v in (o.get("meta") or {}).items() if v}
            if credit:
                meta["credit"] = credit
            if permission:
                meta["rights"] = f"permission: {permission}"
            op = {"op": "add", "p": o["path"], "s": f["size"], "h": f["sha"], "t": t}
            if staged and it["id"] in staged:
                op["staged"] = staged[it["id"]]
            if meta:
                op["meta"] = meta
            ops.append(op)
        elif a in ("credit", "duplicate") and credit:
            tgt = o["target"]
            cur = credited.get(tgt, ((state.get(tgt) or {}).get("meta") or {}).get("credit"))
            credited[tgt] = merge_credit(cur, credit)
        elif a == "link":
            name = util.clean_text(o["title"], 100)
            safe = "".join(ch if ch.isalnum() or ch in "-_()" else "-" for ch in name).strip("-")[:90] or "Link"
            meta = {"url": o["url"], "title": name}
            if credit:
                meta["credit"] = credit
            ops.append({"op": "link", "p": f"{o['folder']}/{safe}.link", "url": o["url"], "t": t, "meta": meta})
    for tgt, c in credited.items():
        if c != (((state.get(tgt) or {}).get("meta") or {}).get("credit")):
            ops.append({"op": "meta", "p": tgt, "meta": {"credit": c}})
    return ops


__all__ = ["sort", "finalize", "ops_for", "report"]
