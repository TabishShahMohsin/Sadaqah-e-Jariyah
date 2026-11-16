#!/usr/bin/env python3
"""
Offline test of the sorter (tools/sorter) and the local command (tools/sort_inbox.py). Claude is replaced by a script
that uses the real tools; everything else is real: unpacking, conversion, fingerprints, rules, catalog, apply.
    python3 tools/test_sorter.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PASS = 0


def ok(cond, msg):
    global PASS
    assert cond, msg
    PASS += 1
    print("  ✓", msg)


def pdf_with_text(text, pages=1):
    """A real text PDF (so pdftotext and fingerprints work) made with LibreOffice-free tooling."""
    from PIL import Image, ImageDraw
    ims = []
    for i in range(pages):
        im = Image.new("RGB", (620, 877), "white")
        d = ImageDraw.Draw(im)
        for j, line in enumerate((text + f" page {i + 1}").split("\n")):
            d.text((40, 40 + 18 * j), line, fill="black")
        ims.append(im)
    buf = io.BytesIO()
    ims[0].save(buf, "PDF", save_all=True, append_images=ims[1:])
    return buf.getvalue()


def rescanned(pdf_bytes):
    """The same page as a phone would capture it: shifted, slightly scaled, greyer paper, sensor noise."""
    import subprocess
    import numpy as np
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        open(os.path.join(td, "a.pdf"), "wb").write(pdf_bytes)
        subprocess.run(["pdftoppm", "-r", "72", "-png", "-singlefile", os.path.join(td, "a.pdf"), os.path.join(td, "p")], check=True)
        im = Image.open(os.path.join(td, "p.png")).convert("L")
    w, h = im.size
    im = im.resize((int(w * 1.06), int(h * 1.06)))
    canvas = Image.new("L", (w, h), 235)
    canvas.paste(im, (-18, -11))
    a = np.asarray(canvas, dtype=float) + np.random.default_rng(7).normal(0, 10, (h, w))
    out = Image.fromarray(np.clip(a, 0, 255).astype("uint8")).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, "PDF")
    return buf.getvalue()


def make_repo():
    root = tempfile.mkdtemp(prefix="sej-sort-")
    shutil.copytree(HERE, os.path.join(root, "tools"), ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(os.path.join(os.path.dirname(HERE), "CONVENTIONS.md"), root)
    os.makedirs(os.path.join(root, "catalog", "changes"))
    md = "Faculty-of-Engineering-and-Technology/BTech-and-BE/Mechanical-Engineering/Sem4/MEC2132_Machine-Design-I"
    paper = pdf_with_text("B.TECH EXAMINATION 2023-24\nMACHINE DESIGN-I MEC2130\nQ1 Define factor of safety")
    files = {f"{md}/PYQs-and-Assignments/Sem-End_2023-24_MEC2130.pdf": paper,
             f"{md}/Unit-1/Lecture-Slides_Intro.pdf": pdf_with_text("Machine design introduction slides\nDesign process")}
    rows = []
    import hashlib
    for p, b in files.items():
        fp = os.path.join(root, p)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        open(fp, "wb").write(b)
        rows.append({"p": p, "s": len(b), "h": hashlib.sha256(b).hexdigest(), "t": 1})
    with open(os.path.join(root, "catalog", "base.jsonl"), "w") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in rows)
    pg = {"umbrella": "Faculty-of-Engineering-and-Technology/BTech-and-BE", "labels": {}, "programmes": [
        {"id": "btech-me", "degree": "B.Tech", "name": "Mechanical Engineering", "branch": "Faculty-of-Engineering-and-Technology/BTech-and-BE/Mechanical-Engineering",
         "semesters": {"4": [{"p": md, "c": "MEC2132"}]}, "official": [], "published": True}],
        "courses": {md: {"title": "Machine Design-I", "codes": ["MEC2132"], "be": ["EMEC2132"], "old": ["MEC2130"], "progs": {"btech-me": [4]}, "kind": "core"},
                    "Faculty-of-Engineering-and-Technology/BTech-and-BE/Mechanical-Engineering/Sem4/MEC2212_Applied-Thermodynamics":
                        {"title": "Applied Thermodynamics", "codes": ["MEC2212"], "be": [], "old": ["MEC2210"], "progs": {"btech-me": [4]}, "kind": "core"}}}
    json.dump(pg, open(os.path.join(root, "catalog", "programmes.json"), "w"))
    return root, md, paper


def build_pptx(path):
    """A minimal real .pptx via LibreOffice: write an .odp-free text deck through soffice from a .txt."""
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, "deck.txt")
    open(src, "w").write("Applied Thermodynamics\nUnit 3 Compressors\nDr. Mohammad Asif")
    import subprocess
    with tempfile.TemporaryDirectory() as prof:
        subprocess.run(["soffice", f"-env:UserInstallation=file://{prof}", "--headless", "--convert-to", "docx", "--outdir", tmp, src],
                       capture_output=True, timeout=180, env={**os.environ, "HOME": prof})
    shutil.copy(os.path.join(tmp, "deck.docx"), path)


class ScriptedClaude:
    """Plays Claude's part: uses the tools the way the system prompt asks, makes one mistake to see it corrected."""

    def __init__(self):
        self.calls, self.seen_tools = 0, []

    def __call__(self, body):
        self.calls += 1
        msgs = body["messages"]
        intro = msgs[0]["content"][0]["text"]
        files = [ln for ln in intro.split("\n") if ln.startswith("id=")]
        ids = {ln.split()[0][3:]: json.loads(ln.split("path=")[1].split("  type=")[0]) for ln in files}
        turn = (len(msgs) - 1) // 2
        last = msgs[-1]["content"]
        if turn == 0:
            first = sorted(ids)[0]
            return {"content": [{"type": "tool_use", "id": "t1", "name": "find_courses", "input": {"query": "MEC2130"}},
                                {"type": "tool_use", "id": "t2", "name": "view", "input": {"id": first}},
                                {"type": "tool_use", "id": "t3", "name": "find_courses", "input": {"query": "applied thermodynamics"}}]}
        if turn == 1:
            res = {b["tool_use_id"]: json.loads(b["content"][0]["text"]) for b in last if b.get("type") == "tool_result"}
            self.seen_tools.append(res)
            md = res["t1"]["results"][0]["path"]
            th = res["t3"]["results"][0]["path"]
            self.md, self.th = md, th
            ds = []
            for i, src in ids.items():
                if src.endswith("Unit 1 notes.pdf"):
                    ds.append({"id": i, "action": "place", "folder": f"{md}/Unit-1", "name": "Class-Notes_Unit-1_2025-26.pdf",
                               "author": "Syed Mohammad Talha", "year": "2025-26", "reason": "signed SMT", "confidence": 0.9})
                elif src.endswith(".docx") or src.endswith(".pdf") and "Thermo" in src:
                    ds.append({"id": i, "action": "place", "folder": f"{th}/Unit-3", "name": "Lecture Slides Compressors.pdf",
                               "professor": "Dr. Mohammad Asif", "reason": "title slide", "confidence": 0.8})  # bad name on purpose
                elif src.endswith(".jpg") or src.endswith(".png"):
                    ds.append({"id": i, "action": "place", "folder": f"{md}/Unit-1", "name": "Class-Notes_Board-Photo.jpg", "reason": "board photo"})
                elif src.endswith("output.txt"):
                    ds.append({"id": i, "action": "place", "folder": f"{md}/Unit-1", "name": "Program-Output.txt", "reason": "program output"})
                elif src.startswith("https://"):
                    ds.append({"id": i, "action": "link", "folder": f"{md}/Unit-1", "name": "NPTEL Machine Design lectures", "reason": "video course"})
                elif src.endswith("Kurmi.pdf"):
                    ds.append({"id": i, "action": "reject", "category": "copyright", "reason": "published textbook"})
                else:
                    ds.append({"id": i, "action": "review", "reason": "unclear"})
            return {"content": [{"type": "tool_use", "id": "d1", "name": "decide", "input": {"decisions": ds}}]}
        if turn == 2:
            res = json.loads([b for b in last if b.get("type") == "tool_result"][0]["content"][0]["text"])
            self.first_results = res["results"]
            fixes = []
            for r in res["results"]:
                if "error" in r:
                    if "characters" in r["error"]:
                        fixes.append({"id": r["id"], "action": "place", "folder": f"{self.th}/Unit-3", "name": "Lecture-Slides_Compressors.pdf",
                                      "professor": "Dr. Mohammad Asif", "reason": "title slide", "confidence": 0.8})
                    else:
                        fixes.append({"id": r["id"], "action": "review", "reason": r["error"][:80]})
            calls = [{"type": "tool_use", "id": "d2", "name": "decide", "input": {"decisions": fixes}}] if fixes else []
            return {"content": calls + [{"type": "tool_use", "id": "f1", "name": "finish", "input": {"notes": ["Mech Drive = ME sem 4"]}}]}
        return {"content": [{"type": "tool_use", "id": "f2", "name": "finish", "input": {}}]}


def main():
    root, md, paper = make_repo()
    os.chdir(root)
    sys.path.insert(0, os.path.join(root, "tools"))
    for m in list(sys.modules):
        if m in ("rules", "sej", "scrub", "triage", "sort_inbox") or m.startswith("sorter"):
            sys.modules.pop(m)
    import sej
    import rules
    import sort_inbox
    from sorter import agent, courses

    # inbox
    ib = os.path.join(root, "_Inbox", "Mech Drive")
    os.makedirs(os.path.join(ib, "Machine Design I", "UNIT 1"))
    os.makedirs(os.path.join(ib, "Machine Design I", "PYQ"))
    os.makedirs(os.path.join(ib, "Applied Thermo"))
    open(os.path.join(ib, "Machine Design I", "PYQ", "M 23-24.pdf"), "wb").write(paper)
    notes = pdf_with_text("Unit 1 notes by SMT\nStress concentration\nFactor of safety", pages=3)
    open(os.path.join(ib, "Machine Design I", "UNIT 1", "Unit 1 notes.pdf"), "wb").write(notes)
    open(os.path.join(ib, "Machine Design I", "UNIT 1", "Unit 1 notes (1).pdf"), "wb").write(notes)
    open(os.path.join(ib, ".DS_Store"), "wb").write(b"junk")
    open(os.path.join(ib, "setup.exe"), "wb").write(b"MZ junk")
    open(os.path.join(ib, "Machine Design I", "output.txt"), "w").write("Enter n: 5\n120\n")
    build_pptx(os.path.join(ib, "Applied Thermo", "U3 compressor.docx"))
    from PIL import Image
    import numpy as np
    Image.fromarray((np.random.default_rng(1).random((400, 600, 3)) * 255).astype("uint8")).save(os.path.join(ib, "Machine Design I", "board.png"))
    zbuf = os.path.join(ib, "extra.zip")
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr("Kurmi.pdf", pdf_with_text("A TEXTBOOK OF MACHINE DESIGN\nR.S. Khurmi"))
        z.writestr("../../escape.pdf", pdf_with_text("zip slip attempt"))
        z.writestr("__MACOSX/._Kurmi.pdf", b"x")

    # ── archive lookups
    state = sej.load_state()
    arch = courses.Archive(root, state, sej, rules, local_root=root)
    ok(arch.find("MEC2130")[0]["path"] == md, "finds a course by its old code")
    ok(arch.find("EMEC2132")[0]["path"] == md, "finds a course by its B.E. code")
    ok(arch.find("machine design 1")[0]["path"] == md, "finds a course by title words")
    ok("Faculty-of-Engineering-and-Technology/BTech-and-BE/Mechanical-Engineering/Sem4/MEC2212_Applied-Thermodynamics" in arch.known,
       "curriculum courses count as known even with no files yet")
    # a different scan of a paper the archive has: different bytes, same exam
    from sorter import dedup, evidence
    rescan = os.path.join(root, "rescan.pdf")
    open(rescan, "wb").write(rescanned(paper))
    it = {"id": "x", "src": "rescan.pdf", "path": rescan, "ext": "pdf"}
    evidence.inspect(it)
    found, _ = dedup.against_course(it, arch, md)
    ok(found and found[0]["path"].endswith("Sem-End_2023-24_MEC2130.pdf"), "a re-scan of an archive paper is found as a likely duplicate")
    rec = courses.record("x", it["info"], "h")
    ok(len(json.dumps(rec)) < 2500, "a fingerprint stays small (for the catalog in git)")
    os.remove(rescan)

    # ── plan with the scripted Claude
    fake = ScriptedClaude()
    agent.ApiClient = lambda *a, **k: fake  # noqa: E731
    fake.usage = {"calls": 0, "input": 0, "output": 0, "cache_read": 0}
    sys.argv = ["sort_inbox.py", "plan", ib, "--credit", "Farhan Baig", "--note", "Mech sem 4 drive",
                "--link", "https://nptel.ac.in/courses/112105124"]
    sort_inbox.main()
    work = os.path.join(root, ".sej-cache", "sort", "Mech-Drive")
    plan = json.load(open(os.path.join(work, "plan.json")))
    items = plan["items"]
    by = {it["src"].split("Mech Drive/")[-1]: it for it in items}
    out = lambda k: by[k]["outcome"]  # noqa: E731
    ok(all(it.get("outcome") for it in items), "every input has an outcome")
    ok(out(".DS_Store")["action"] == "ignored", "system junk is ignored, not lost")
    ok(out("setup.exe")["action"] == "reject" and out("setup.exe")["category"] == "format", "executables are rejected")
    ok(out("extra.zip")["action"] == "unpacked", "zips are opened")
    ok(any(k.endswith("escape.pdf") and "extra/escape.pdf" in k for k in by), "zip paths can't escape the work folder")
    ok(out("extra/__MACOSX/._Kurmi.pdf")["action"] == "ignored", "zip junk is ignored")
    ok(out("Machine Design I/PYQ/M 23-24.pdf") == {**out("Machine Design I/PYQ/M 23-24.pdf"), "action": "credit",
                                                   "target": f"{md}/PYQs-and-Assignments/Sem-End_2023-24_MEC2130.pdf"},
       "an exact copy of an archive file is credited in code, never sent to Claude")
    tw = out("Machine Design I/UNIT 1/Unit 1 notes (1).pdf")
    ok(tw["action"] == "twin" and by["Machine Design I/UNIT 1/Unit 1 notes.pdf"]["id"] == tw["of"], "an identical copy in the upload is a twin")
    ok(by["Applied Thermo/U3 compressor.docx"]["converted_from"] == "docx", "Word/PowerPoint files are converted to PDF")
    ok(by["Machine Design I/board.png"]["converted_from"] == "png", "photo PNGs become JPEG")
    ok(any("error" in r and "characters" in r["error"] for r in fake.first_results), "a bad file name is sent back to Claude")
    ok(out("Applied Thermo/U3 compressor.docx")["path"].endswith("MEC2212_Applied-Thermodynamics/Unit-3/Lecture-Slides_Compressors.pdf"),
       "…and the corrected decision is accepted")
    ok(out("Machine Design I/output.txt")["action"] == "review", ".txt outside a lab's Records is refused by the rules (sent to review)")
    ok(out("extra/Kurmi.pdf")["action"] == "reject" and out("extra/Kurmi.pdf")["category"] == "copyright", "books are rejected without permission")
    link = [it for it in items if it.get("ext") == "url"][0]
    ok(link["outcome"]["action"] == "link", "web links become link entries")
    rep = open(os.path.join(work, "report.md")).read()
    ok("every file has an outcome" in rep and "Needs a person" in rep, "the report shows coverage and review items first")

    # ── apply
    sys.argv = ["sort_inbox.py", "apply", "Mech-Drive"]
    sort_inbox.main()
    state2 = sej.load_state()
    notes_p = f"{md}/Unit-1/Class-Notes_Unit-1_2025-26.pdf"
    ok(notes_p in state2 and os.path.exists(os.path.join(root, notes_p)), "placed files are in the archive and the catalog")
    ok(state2[notes_p]["meta"] == {"author": "Syed Mohammad Talha", "year": "2025-26", "credit": "Farhan Baig"}, "metadata and credit recorded")
    pyq = f"{md}/PYQs-and-Assignments/Sem-End_2023-24_MEC2130.pdf"
    ok(state2[pyq]["meta"]["credit"] == "Farhan Baig", "the existing copy gets the credit")
    ln = [p for p, e in state2.items() if e.get("link")]
    ok(len(ln) == 1 and state2[ln[0]]["url"].startswith("https://nptel"), "the link entry is in the catalog")
    ok(not os.path.exists(ib) and os.path.exists(os.path.join(root, ".sej-cache", "inbox-merged-originals", "Mech-Drive", "Mech Drive")),
       "inbox originals are moved aside, not deleted")
    ok(not any(p.endswith((".docx", ".exe", ".zip", ".txt")) for p in state2), "nothing unpublishable reached the archive")
    sys.argv = ["sej.py", "check"]
    try:
        sej.main()
    except SystemExit as e:
        ok(e.code == 0, "catalog check passes")
    # site index carries the link and the credit
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "site"))
    import build_index
    build_index._META = None
    idx = json.dumps(build_index.build(root))
    ok("nptel" in idx and "Farhan Baig" in idx, "the site index shows the link entry and credits")
    try:
        sys.argv = ["sort_inbox.py", "apply", "Mech-Drive"]
        sort_inbox.main()
        ok(False, "second apply should stop")
    except SystemExit as e:
        ok("already applied" in str(e), "a plan can't be applied twice")
    shutil.rmtree(root)
    print(f"\n{PASS} checks passed")


if __name__ == "__main__":
    main()
