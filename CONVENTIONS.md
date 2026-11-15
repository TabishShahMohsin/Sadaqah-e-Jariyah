# Sadaqah-e-Jariyah — Archive Conventions

These rules decide what the archive accepts, where every file lives and what it is called. People follow them when
adding files by hand; the contribution pipeline gives them to Claude and then **checks every placement against them in
code** (`tools/rules.py`). If you change a rule here, change it there too. `python3 tools/conform.py` lists every file
that breaks a rule (older material is brought in line over time; the rules are not bent to fit it).

## 0. Principles
1. **The official curriculum is the structure.** Degree → Branch → Programme → Semester → Course → Section.
   Programmes, semesters, course codes and syllabi come from AMU's published curricula (`catalog/programmes.json`,
   built from `_Sources/AMU-Official/`). Folders are only storage: each file is stored once and every programme that
   takes the course shows it under its own code.
2. **Specialisations are full programmes** (AI in Computer, VLSI in Electronics, Automobile in Mechanical, B.E. in its
   branch): their own semester lists, their own codes, the same navigation as the main programme.
3. **Course relationships are recorded, not guessed at display time.** B.E. code = B.Tech code with `E` in front
   (`MEC2112` ↔ `EMEC2112`). An old-scheme course maps to its current equivalent by title and syllabus, with the
   evidence kept in the catalog; an old course with no successor stays in `_Old-Syllabus-Subjects`.
4. **Scope:** B.Tech and B.E. at ZHCET. Other programmes (B.Arch, diploma, M.Tech) get their own umbrella later.
5. **Contributors only dump; the archive does the sorting.** Any naming, any mess, files, folders, zips or links.
   The pipeline converts, de-duplicates, names, places and credits, and a maintainer approves every change.
6. **Every uploaded file gets exactly one recorded outcome:** placed, credited to an existing copy, duplicate,
   link entry, or rejected with a reason. Nothing disappears silently.

## 1. Folder tree

```
BTech-and-BE/                   umbrella for B.Tech & B.E. (other AMU programmes get their own umbrella later)
 <Branch>/
  _Curriculum/                  course structures, syllabi bundles, notices
  _General-PYQs/                papers that don't belong to one course
  _Old-Syllabus-Subjects/       courses no longer in the current scheme
  Electives/                    elective courses (one folder each)
  Sem<N>/                       Sem3 … Sem8   (First Year uses Phys-Sem / Chem-Sem)
    <Course>/
      Syllabus/
      Complete-Notes/
      Unit-1/ … Unit-5/
      PYQs-and-Assignments/
      Books-and-Resources/
      Manuals/                  labs only
      Records/                  labs only
```

Branch folders: `First-Year-Common`, `Computer-Engineering` (CoE & AI), `Electronics-Engineering` (ECE & VLSI),
`Electrical-Engineering`, `Mechanical-Engineering` (ME, Automobile & B.E. ME), `Civil-Engineering`, `Chemical-Engineering`,
`Chemical-Technology` (Refining & Petrochemicals, formerly Petrochemical Engg), `Food-Technology`, plus `Shared-Courses`
for a course several branches take under the same code (e.g. `MEH3452`).
A course that two branches teach from the same syllabus under different codes lives in one of them with both codes
(`CHC2012-PKC2112_Basic-Principles-of-Chemical-Engineering`, in Chemical-Engineering); the other programme points to it.
Which courses each programme takes in each semester lives in `catalog/programmes.json`, generated from the official
curricula in `_Sources/AMU-Official/` (not published as a folder). A course lives in one folder only; programme views
on the site point to it.

### Course folder names
`<CODE>_<Title-In-Title-Case>` — words joined by `-`, no spaces.

| Case | Example |
|---|---|
| One code | `AMS2612_Higher-Mathematics` |
| Same class taught under two codes (CoE first, then AI) | `COC2072-AIC2072_Digital-Logic-and-System-Design` |
| Elective slot without a fixed course | `DE-4_Departmental-Elective-IV`, `OE-2_Open-Elective-II` |

A code is 2–4 capital letters + 3–4 digits (`COC2072`, `EZH1112`). 2023-scheme codes end in **2**, 2021-scheme in **0**;
an old-scheme file of a current course goes in the current course's folder (its syllabus is marked `Old-Syllabus`).
Theory and its lab are **separate courses** (`ACS1112_Applied-Chemistry` vs `ACS1912_Applied-Chemistry-Lab`).

**Programmes covered:** B.Tech only. B.Arch and B.E. (evening) have no branches or course folders of their own.
**B.E. material:** when a B.E. course is the same course as a B.Tech one (same title and syllabus), its notes, papers and
assignments go into the **B.Tech course's folder**, like any other file. The B.E. code goes in the file name where a
code is part of the pattern (e.g. `Sem-End_2023-24_<BE-CODE>.pdf`) and in the catalog's `note`, so search finds it by
either code. B.E. material for a course with no B.Tech twin is not added.
An **old-syllabus** course with a B.E. twin is one folder carrying both codes, B.Tech first
(`CE217-ECE217_Construction-Practice`). A course whose code changed between schemes keeps only its newest code in the
folder name; papers under an older or B.E. code carry that code in the file name (`Sem-End_2023-24_EMEE4210.pdf`).
Duplicate folders are merged with `tools/merge_courses.py` (dry run first), which records every move in the catalog.

### Which section
| Section | Put here |
|---|---|
| `Syllabus` | official syllabus pages, course outlines |
| `Complete-Notes` | notes covering the whole course or several units |
| `Unit-N` | slides, notes, lectures for one unit. Lecture videos go in `Unit-N/Video-Lectures/` |
| `PYQs-and-Assignments` | past papers (mid-sem, sem-end, quizzes), assignments, tutorial sheets, with solutions |
| `Books-and-Resources` | links lists, reference material, papers, **only material we may share** (see §4) |
| `Manuals` | lab manuals, experiment sheets, viva questions |
| `Records` | lab records, programs written in lab, outputs |

Unsure between `Unit-N` and `Complete-Notes` → `Complete-Notes`.

## 2. File names

`Part_Part_Part.ext` — parts joined by `_`, words inside a part joined by `-`. Keep the original extension, lower-case.
Allowed characters: letters, digits, `-`, `_`, `.`, `(`, `)`. No spaces in new names.

| Kind | Pattern | Examples |
|---|---|---|
| Sem-end paper | `Sem-End_<YYYY-YY>[_Autumn\|_Winter][_<old-code>][_Set-N]` | `Sem-End_2023-24.pdf`, `Sem-End_2024-25_Autumn_AMS-1110_Set-1.pdf` |
| Mid-sem paper | `Mid-Sem[-1\|-2]_<YYYY-YY>` | `Mid-Sem-1_2023-24.pdf` |
| With answers | add `_Solution` or `_Handwritten-Answers` | `Mid-Sem_2021-22_Solution.pdf` |
| Assignment / quiz / tutorial | `Z_<Kind>[-N]_<YYYY-YY>[_detail]` (the `Z_` sorts them after papers) | `Z_Assignment-2_2022-23_Solution.pdf`, `Z_Quiz-1_2025-26_Set-B.jpeg` |
| Syllabus | `<CODE>_Syllabus_<YYYY-YY>` or `<OLDCODE>_Old-Syllabus_<YYYY-YY>` | `COC2072_Syllabus_2024-25.pdf` |
| Lecture | `Lecture-<NN>_<Topic>` | `Lecture-06_Boolean-Algebra.pdf` |
| Notes | `<Kind>_<Topic-or-Unit>_<YYYY-YY>[_<Author>]` | `Class-Notes_Unit-2_2023-24_Faisal.pdf`, `Notes_Digital-Logic_2023-24.pdf` |
| Lab | `Experiment-<N>_<Topic>`, `Viva-Questions_<detail>` | `Experiment-3_Flash-Point.pdf` |

Academic year is always `YYYY-YY` (`2023-24`), never `2023` alone when the session is known.
If a name would collide with an existing file, append `_2`, `_3`, …

## 3. Metadata and credits (kept in the catalog, not the file name)
`author` (who made the notes), `professor` (who taught), `year` (`YYYY-YY`), `credit` (who shared it with the archive), `note`.
The site shows them when a file is opened: *notes by … · taught by … · shared by …*.

- **`credit`** is a contributor's chosen name or enrolment number (`gk7093`) or a source (`AMU Cloud`, `AMU Drive`,
  `Engineering College Library`, `AMU website (official)`). A file found in more than one place keeps every credit, joined with `, `.
- **No sources or teachers in file names.** `Notes_Unit-1_FROM_AMU_Cloud.pdf` becomes `Notes_Unit-1.pdf` with credit
  `AMU Cloud`; `Ishtiyak-Sir_Lecture-3.pdf` becomes `Lecture-3.pdf` with professor `Ishtiyak`.
- Credits are set with `tools/credit.py` (each run writes one reviewable catalog change set):
  `report` · `set --credit NAME PATH…` · `change CHANGE-ID --credit NAME` (everything a batch added) ·
  `tags` / `teachers` (move source tags and teacher prefixes from names into the catalog; dry run unless `--apply`).
- A batch imported with `source.credit` in its change set credits every file it adds.
- The maintainer is never credited and never named anywhere.

## 4. What may be contributed
**Yes:** your own notes and solutions; handouts, slides, tutorial and assignment sheets a teacher gave the class;
question papers (mid-sem, sem-end, quizzes); syllabi and course outlines; lab manuals and your lab records.

**No:**
- **Piracy is haram.** No whole published books, solution manuals, paid course material (Unacademy, etc.).
  Point to the library catalogue instead. Reference pages, figures and short excerpts used while studying are fine:
  they go in `Books-and-Resources`, and PDFs there over 40 pages are not published (they are usually whole books).
- **Projects downloaded from the web** (other colleges, project sites) are added as a **link** to the original page in
  `Books-and-Resources`, not as copied files.
- **Personal records:** marks or attendance lists, admit cards, fee receipts, ID or registration cards, CVs, phone
  numbers. A student's name, faculty or enrolment number on notes, assignments, lab records or answer sheets is fine.
- **Answers to work that is still due.** Assignment, tutorial, lab and test solutions only after the deadline.
- Anything that isn't AMU B.Tech/B.E. study material (notices, forms, memes, software installers).

**Closed sources** (AMU Cloud, AMU Pedia, AMU library sites): say where it came from in the contribution message;
the source is credited. Obtaining permission is the contributor's responsibility.
**Material the maintainer adds** has been cleared with its library, author or teacher; the catalog records that with
`rights` (e.g. `permission: Engineering College Library`). Published books are allowed only with such a record.

**Takedowns** from a teacher, student or rights holder are honoured without argument.

### Out of scope for now: parked, not rejected
The current focus is B.Tech/B.E. at ZHCET, AMU. Next come the rest of the Faculty of Engineering (M.Tech, Diploma),
other AMU faculties, other Muslim colleges, then colleges across India. Genuine study material for those (an M.Tech
syllabus, Diploma papers, another college's course notes) is **parked**: copied into `_Parked/<AMU|Other-Institutions>/…`
(not published, not in git) and listed with its source and credit in `catalog/parked.json`. Books, personal records and
junk are still rejected.

## 5. Formats
| Kind | Stored and published as |
|---|---|
| Documents and slides (`pptx ppt docx doc odt odp pages key`) | **PDF** (converted by the pipeline) |
| Spreadsheets (`xlsx xls ods numbers`) | **CSV** (one per sheet) |
| Photos and scans (`heic png webp jpg`) | **JPEG**; PNG only for screenshots and diagrams |
| Video | **MP4** |
| Code and tool files (`c cpp h java py ipynb asm a51 v vhd circ m sql`, …) | as they are |
| Circuit simulation files (`ms14` Multisim, `pdsprj` / `dsn` Proteus) | as they are (download only); build output (`hex lst obj bin`) is not kept |
| Program output inside lab `Records` | `.txt` |
| Links (Drive, YouTube, websites) | a **link entry** in the catalog, shown on the site like a file (no `.md` / `.url` files) |

When a document already in the archive is converted to PDF, the original stays in the Hugging Face dataset's history:
its catalog entry gets `orig` = `<release-tag>/<path>`, and the site shows an "Original DOCX" link next to the PDF.

No `.md`, `.txt` (outside lab records), `.html`, executables (`exe out class`) or archives in the published archive;
archives are unpacked. Contributors may upload any of the left-hand formats; conversion is the pipeline's job.
Up to 500 MB per file. There is no limit on how many files or links one contribution may carry.

## 6. Licence
- **Code** in this repository: GPL-3.0 (`LICENSE`).
- **Catalog, structure and metadata** (`catalog/`, the index): CC0, free for any use, including as a dataset
  (e.g. on Hugging Face).
- **Contributors' own work** (their notes, solutions, records): contributed under CC BY 4.0, credited by the name
  they choose.
- **Third-party material** (teachers' slides, question papers, library scans) belongs to its owners. It is shared as
  provided, with `rights` and `credit` in the catalog, and is not relicensed by us. A dataset export carries those tags.
