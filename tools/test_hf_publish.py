#!/usr/bin/env python3
"""Checks for tools/hf_publish.py: what gets published, under which name, cleaned how, and the sync plan."""
import hashlib
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hf_publish as hp  # noqa: E402

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)
    ok += 1


def put(root, rel, data):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(data)
    return {"p": rel, "s": len(data), "h": hashlib.sha256(data).hexdigest(), "t": 1}


def pdf(author=None, text="Unit 1 notes", pages=2):
    import io
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    if author:
        c.setAuthor(author)
    for i in range(pages):
        c.drawString(72, 700, text if i == 0 else f"page {i}")
        c.showPage()
    c.save()
    return buf.getvalue()


# names
check(hp.clean_name("Z_Assignment_20COB258-Prabhat-Kumar-Gupta_COC2030.pdf") == "Z_Assignment_Prabhat-Kumar-Gupta_COC2030.pdf", "enrolment number removed")
check(hp.clean_name("20COB258_Binary Tree(Output).pdf") == "Binary Tree(Output).pdf", "leading enrolment number")
check(hp.clean_name("Project1_20COB258.c") == "Project1.c", "trailing enrolment number")
check(hp.clean_name("Sem-End_2022-23_MEC2130.pdf") == "Sem-End_2022-23_MEC2130.pdf", "course codes and years untouched")
check(hp.clean_name("24COBEA102.pdf") == "file.pdf", "name that was only a number")
check(hp.clean_name("Insertion Sorting_20COB258 Output.JPG") == "Insertion Sorting_Output.JPG", "number between words")
check(hp.clean_name("Lecture - Intro__v2.pdf") == "Lecture - Intro__v2.pdf", "names without numbers never change")
check(not hp.BOOKISH.search("Lecture_Bookkeeping.pdf") and not hp.BOOKISH.search("grayscale_manometer.pdf"), "no substring false positives")
check(all(hp.BOOKISH.search(n) for n in ("Pressman-Design-Engineering.ppt", "Machining Ops - Wiley.pdf", "Old-Teacher_pressman-ch-25.pdf", "2022-23_Memory-Tocci_pp12.ppt", "Book-Excerpt_x.pdf")), "publishers/authors caught")
check(not any(hp.BOOKISH.search(n) for n in ("Notebook_Unit1.pdf", "Facebook-Ads.pdf", "Sullivans-notes")), "words that merely contain them pass")

root = tempfile.mkdtemp()
try:
    C = "Faculty-of-Engineering-and-Technology/BTech-and-BE/Computer-Engineering/Sem3/COC2030_DS"
    state = {}
    for rel, data in [
        (f"{C}/Unit-1/notes.pdf", pdf()),
        (f"{C}/Unit-1/signed.pdf", pdf(author="Prof. Someone")),
        (f"{C}/Unit-1/chapter-scan.pdf", pdf(text="ISBN 978-0-13-468599-1 Pearson")),
        (f"{C}/Syllabus/syllabus.pdf", pdf(text="Reference: ISBN 978-0-13-468599-1")),
        (f"{C}/Books-and-Resources/x.pdf", pdf(pages=45)),
        (f"{C}/Books-and-Resources/figure.pdf", pdf(text="reference pages", pages=3)),
        (f"{C}/Records/Project1_20COB258.c", b"int main(){}"),
        (f"{C}/Records/Project1.c", b"int main(){return 0;}"),
        (f"{C}/Records/Project1_20COB258.exe", b"MZ"),
        (f"{C}/Records/empty.txt", b""),
        (f"{C}/Unit-2/secret.pdf", pdf(text="x")),
    ]:
        e = put(root, rel, data)
        state[rel] = e
    state[f"{C}/Unit-1/video.link"] = {"p": f"{C}/Unit-1/video.link", "s": 0, "h": "", "t": 1, "link": True,
                                      "meta": {"url": "https://youtu.be/x"}}
    put(root, "catalog/exclude.txt", f"={C}/Unit-2/secret.pdf\n".encode())

    scan = {e["h"]: hp.scan_one(os.path.join(root, p), p.rsplit(".", 1)[-1]) for p, e in state.items()
            if not e.get("link") and p.rsplit(".", 1)[-1] in hp.SCAN_EXT}
    check(scan[state[f"{C}/Unit-1/chapter-scan.pdf"]["h"]]["isbn"], "ISBN found on first page")
    check(scan[state[f"{C}/Unit-1/signed.pdf"]["h"]]["personal"] == ["Author"], "author field found")
    check(not scan[state[f"{C}/Unit-1/notes.pdf"]["h"]]["personal"], "plain notes have nothing personal")

    pub, left, unscanned = hp.select(state, scan, root)
    why = dict(left)
    check(unscanned == 0, "everything scanned")
    check(why[f"{C}/Unit-1/chapter-scan.pdf"].startswith("ISBN") and f"{C}/Syllabus/syllabus.pdf" not in why, "ISBN rule, syllabi exempt")
    check(why[f"{C}/Books-and-Resources/x.pdf"].startswith("Books-and-Resources, 45 pages"), "long PDF in books section left out")
    check(f"{C}/Books-and-Resources/figure.pdf" in pub, "short reference pages are published")
    check("not study material" in why[f"{C}/Records/Project1_20COB258.exe"], ".exe left out")
    check(why[f"{C}/Records/empty.txt"] == "empty file", "empty file left out")
    check(why[f"{C}/Unit-2/secret.pdf"] == "catalog/exclude.txt", "exclude.txt honoured")
    names = set(pub)
    check(f"{C}/Records/Project1.c" in names and f"{C}/Records/Project1_2.c" in names, "renamed file never overwrites another")
    check(not any(hp.ENROLL.search(p) for p in names), "no enrolment numbers in published paths")
    check(pub[f"{C}/Unit-1/signed.pdf"]["clean"] and not pub[f"{C}/Unit-1/notes.pdf"]["clean"], "only files with personal data are cleaned")

    hp.STAGE = os.path.join(root, ".sej-cache", "hf-stage")
    f = hp.staged(pub[f"{C}/Unit-1/signed.pdf"], root)
    from pypdf import PdfReader
    check(f.startswith(hp.STAGE) and not (PdfReader(f).metadata or {}).get("/Author"), "cleaned copy has no author")
    check((PdfReader(os.path.join(root, f"{C}/Unit-1/signed.pdf")).metadata or {}).get("/Author"), "original untouched")
    check(hp.staged(pub[f"{C}/Unit-1/notes.pdf"], root) == os.path.join(root, f"{C}/Unit-1/notes.pdf"), "clean files published as they are")

    # sync plan
    loc = lambda s256, s1, size: {"sha256": s256, "sha1": s1, "size": size, "file": "x"}  # noqa: E731
    local = {"a/same.pdf": loc("A", None, 10), "a/renamed.pdf": loc("B", None, 20), "a/new.pdf": loc("C", None, 30),
             "a/small.c": loc("D", "d1", 5), "a/changed.pdf": loc("E", None, 40)}
    remote = {"a/same.pdf": {"size": 10, "sha256": "A", "sha1": "x"}, "a/old_20COB258.pdf": {"size": 20, "sha256": "B", "sha1": "y"},
              "a/small.c": {"size": 5, "sha256": None, "sha1": "d1"}, "a/changed.pdf": {"size": 41, "sha256": "E0", "sha1": "z"},
              "Computer-Engineering/old-layout.pdf": {"size": 9, "sha256": "Q", "sha1": "q"},
              "README.md": {"size": 1, "sha256": None, "sha1": "r"}, ".gitattributes": {"size": 1, "sha256": None, "sha1": "g"}}
    copies, uploads, deletes = hp.plan(local, remote)
    check(copies == [("a/old_20COB258.pdf", "a/renamed.pdf")], "rename becomes a server-side copy")
    check(uploads == ["a/changed.pdf", "a/new.pdf"], "new and changed files uploaded, identical ones skipped")
    check(deletes == ["Computer-Engineering/old-layout.pdf", "a/old_20COB258.pdf"], "everything unpublished deleted, card kept")
    check(hp.file_ids_bytes(b"abc")[1] == "f2ba8f84ab5c1bce84a7b441cb1959cfc7093b7f", "git blob id matches git")

    # website index
    import json
    os.makedirs(os.path.join(root, "catalog"), exist_ok=True)
    json.dump({"programmes": []}, open(os.path.join(root, "catalog", "programmes.json"), "w"))
    idx, pub2, _, _ = hp.build_index(state, scan, "u/sej-files", root)
    txt = json.dumps(idx)
    check(idx["fileBase"] == "https://huggingface.co/datasets/u/sej-files/resolve/main/", "files come from the dataset")
    check("20COB258" not in txt and "chapter-scan" not in txt and "\"x.pdf\"" not in txt and ".exe" not in txt, "index lists only published files")
    check("video.link" in txt and "youtu.be" in txt, "link entries still listed")
    check(set(pub2) == set(pub), "site and dataset use the same selection")
finally:
    shutil.rmtree(root, ignore_errors=True)
print(f"{ok} checks passed")
