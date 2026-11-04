#!/usr/bin/env python3
"""Checks for tools/convert_slides.py apply/undo on a throwaway archive (no LibreOffice needed)."""
import json
import os
import shutil
import sys
import tempfile
import types
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import convert_slides as cs  # noqa: E402
import sej  # noqa: E402

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)
    ok += 1


root = tempfile.mkdtemp()
try:
    sej.CHANGES = os.path.join(root, "catalog", "changes")
    cs.WORK = os.path.join(root, ".sej-cache", "slides")
    cs.RESULTS = os.path.join(cs.WORK, "results.json")
    os.makedirs(cs.WORK)
    C = "BTech-and-BE/Computer-Engineering/Sem3/COC2062_DSA/Unit-1"

    def put(rel, data):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(data)
        return {"p": rel, "s": len(data), "h": cs.sha256(p), "t": 5, "meta": {"credit": "Someone"}}

    def deck_with_media(rel):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("[Content_Types].xml", "<x/>")
            z.writestr("ppt/media/media2.mp4", b"video-two")
            z.writestr("ppt/media/media1.mp4", b"video-one")
            z.writestr("ppt/media/image1.png", b"png")
        return {"p": rel, "s": os.path.getsize(p), "h": cs.sha256(p), "t": 5}

    state = {f"{C}/a.pptx": put(f"{C}/a.pptx", b"deck-a"), f"{C}/b.ppt": put(f"{C}/b.ppt", b"deck-b"),
             f"{C}/c.pptx": put(f"{C}/c.pptx", b"deck-c"), f"{C}/c.pdf": put(f"{C}/c.pdf", b"pdf-c"),
             f"{C}/d.pptx": deck_with_media(f"{C}/d.pptx"), f"{C}/e.pptx": put(f"{C}/e.pptx", b"deck-e"),
             f"{C}/e.pdf": put(f"{C}/e.pdf", b"other-e")}
    res = {}
    for name, extra in [("a", {}), ("b", {"boxes": {"OpenSymbol U+F061": 2}}), ("c", {"twin": f"{C}/c.pdf", "twin_similarity": 0.97, "twin_pages": 3}),
                        ("d", {"media": ["media1.mp4", "media2.mp4"]}), ("e", {"twin": f"{C}/e.pdf", "twin_similarity": 0.4, "twin_pages": 9})]:
        p = next(q for q in state if q.split("/")[-1].startswith(name + ".p"))
        pdf = os.path.join(cs.WORK, state[p]["h"] + ".pdf")
        open(pdf, "wb").write(b"%PDF " + name.encode())
        res[p] = {"sha": state[p]["h"], "pdf": os.path.relpath(pdf, root), "pages": 3, "slides": 3, "boxes": {}, "fallbacks": [], **extra}
    cs.save(cs.RESULTS, res)
    sej.load_state = lambda include_changes=True: {k: dict(v) for k, v in state.items()}
    import builtins
    printed = []
    real_print = builtins.print
    builtins.print = lambda *a, **k: printed.append(" ".join(map(str, a)))
    cs.cmd_apply(types.SimpleNamespace(accept=None), root)
    builtins.print = real_print
    ch_file = sorted(os.listdir(sej.CHANGES))[0]
    ch = json.load(open(os.path.join(sej.CHANGES, ch_file)))
    adds = {o["p"]: o for o in ch["ops"] if o["op"] == "add"}
    removes = {o["p"]: o for o in ch["ops"] if o["op"] == "remove"}
    check(os.path.exists(os.path.join(root, f"{C}/a.pdf")) and f"{C}/a.pdf" in adds, "deck becomes a PDF in the same folder")
    check(adds[f"{C}/a.pdf"]["meta"]["credit"] == "Someone" and "converted" in adds[f"{C}/a.pdf"]["meta"]["note"], "credit carried over, note added")
    check(not os.path.exists(os.path.join(root, f"{C}/a.pptx")) and os.path.exists(os.path.join(root, "_Originals", C, "a.pptx")), "original kept in _Originals/")
    check(f"{C}/b.ppt" not in removes and os.path.exists(os.path.join(root, f"{C}/b.ppt")), "flagged deck waits for --accept")
    check(f"{C}/c.pptx" in removes and not any(p.startswith(f"{C}/c") for p in adds), "same deck as the existing PDF: only the deck goes")
    check(open(os.path.join(root, f"{C}/c.pdf"), "rb").read() == b"pdf-c", "existing PDF untouched")
    check(f"{C}/e (slides).pdf" in adds, "different version kept beside the existing PDF")
    check(open(os.path.join(root, f"{C}/d (video 1).mp4"), "rb").read() == b"video-one" and f"{C}/d (video 2).mp4" in adds, "media published in slide order")
    check(removes[f"{C}/a.pptx"]["was"]["h"] == state[f"{C}/a.pptx"]["h"], "change set remembers the original")
    check(all(len(o.get("h", "x" * 64)) == 64 for o in ch["ops"]), "every add has a sha256")

    state2 = sej.load_state.__call__()
    cs.cmd_undo(types.SimpleNamespace(change_id=ch_file[:-5]), root)
    check(os.path.exists(os.path.join(root, f"{C}/a.pptx")) and not os.path.exists(os.path.join(root, f"{C}/a.pdf")), "undo restores the deck, sets the PDF aside")
    undo = json.load(open(os.path.join(sej.CHANGES, sorted(os.listdir(sej.CHANGES))[-1])))
    check({o["p"] for o in undo["ops"] if o["op"] == "add"} == set(removes), "undo re-adds every removed deck")
    check(f"{C}/d (video 1).mp4" in {o["p"] for o in undo["ops"] if o["op"] == "remove"}, "undo removes the extracted media too")
finally:
    shutil.rmtree(root, ignore_errors=True)
print(f"{ok} checks passed")
