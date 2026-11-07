"""The sorter in three resumable stages, so the slow parts can run where the files are and the AI part elsewhere.

    prepare (where the files are)   collect → drop junk → unzip → convert → inspect → render page images
                                    work/items.json, work/pages/*.jpg     (budgeted: run again to continue)
    decide  (anywhere)              dedup → AI decisions (parallel batches) → work/plan.json + report.md
                                    needs only the work folder, not the files
    apply   (where the files are)   in sort_inbox.py, budgeted and resumable as well

Paths inside the work folder are stored relative to the repo ("@REPO/…") so a work folder can move between machines."""
import json
import os
import time

from . import evidence, ingest, util

PAGES = 3          # page images kept for the AI's view tool
TEXT_PAGES = 40    # page texts kept for its read tool
ROOT = "@REPO/"


def _rebase(obj, old, new):
    if isinstance(obj, dict):
        return {k: _rebase(v, old, new) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rebase(v, old, new) for v in obj]
    if isinstance(obj, str) and obj.startswith(old):
        return new + obj[len(old):]
    return obj


def save_json(path, data, repo):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(_rebase(data, repo.rstrip("/") + "/", ROOT), fh, ensure_ascii=False)
    os.replace(tmp, path)


def load_json(path, repo):
    with open(path, encoding="utf-8") as fh:
        return _rebase(json.load(fh), ROOT, repo.rstrip("/") + "/")


def page_image(work, it, page):
    return os.path.join(work, "pages", f"{it['id']}-{page}.jpg")


def prepare(items, work, rules, budget, log=print, save=None):
    """Advance every item one step at a time until done or out of time. Returns True when everything is prepared."""
    t0 = time.time()
    out_of_time = lambda: budget and time.time() - t0 > budget   # noqa: E731
    os.makedirs(os.path.join(work, "pages"), exist_ok=True)
    ingest.settle_junk(items)          # every run, so a newer junk rule also reaches a half-prepared upload
    if not any(it.get("prep_collected") for it in items):
        ingest.unpack(items, work)
        for it in items:
            it["prep_collected"] = True
    i = 0
    while i < len(items):                       # conversion (decks, docs, sheets, photos, videos)
        it = items[i]
        i += 1
        if it.get("prep_converted"):
            continue
        if out_of_time():
            return False
        sub = [it]
        ingest.convert(sub, work, rules)
        for extra in sub[1:]:                   # a workbook split into sheets
            extra["id"] = f"f{len(items) + 1:04d}"
            extra["prep_collected"] = extra["prep_converted"] = True
            items.append(extra)
        it["prep_converted"] = True
        if save and i % 20 == 0:
            save()
    n = 0
    for it in items:                            # evidence: text, OCR, profile, fingerprints, page images
        if it.get("prep_inspected") or it.get("outcome"):
            continue
        if out_of_time():
            return False
        if it.get("path") and os.path.exists(it["path"]):
            evidence.inspect(it)
            pages = (it.get("info") or {}).get("pages") or 1
            if it["ext"] in ("pdf", "jpg", "jpeg", "png"):
                for p in range(1, min(PAGES, pages) + 1):
                    im = evidence.render(it["path"], it["ext"], p, 1100)
                    if im is not None:
                        im.convert("RGB").save(page_image(work, it, p), "JPEG", quality=72)
            if it["ext"] == "pdf":
                texts = []
                for p in range(1, min(TEXT_PAGES, pages) + 1):
                    texts.append(evidence.pdf_text(it["path"], p, p)[:4000])
                it["page_texts"] = texts
        it["prep_inspected"] = True
        n += 1
        if save and n % 20 == 0:
            save()
    log(f"  prepared: {len(items)} items")
    return True


def ck_key(it):
    return f"{it.get('src')}|{it.get('sha', '')}"


def ck_dump(items, notes):
    """Checkpoint of the AI's decisions, keyed by file identity (not id), so it survives a re-collected upload."""
    by_id = {it["id"]: ck_key(it) for it in items}
    out = {}
    for it in items:
        o = it.get("outcome") or {}
        if o.get("by_agent"):
            o = dict(o)
            if o.get("of") in by_id:
                o["of_key"] = by_id[o["of"]]
            out[ck_key(it)] = o
    return {"outcomes": out, "notes": notes}


def ck_load(items, ck, ctx=None):
    """Re-apply checkpointed decisions; links to another file ('of') are re-pointed by identity."""
    by_key = {ck_key(it): it["id"] for it in items}
    n = 0
    for it in items:
        o = ck.get("outcomes", {}).get(ck_key(it))
        if not o or it.get("outcome"):
            continue
        o = dict(o)
        if "of_key" in o:
            if o["of_key"] not in by_key:
                continue                    # the file it pointed to is gone: decide again
            o["of"] = by_key[o.pop("of_key")]
        elif o.get("of") and o.get("action") == "twin":
            continue                        # old checkpoint without identities: decide again
        it["outcome"] = o
        if ctx is not None and o.get("path"):
            ctx.taken.add(o["path"])
        n += 1
    return n
