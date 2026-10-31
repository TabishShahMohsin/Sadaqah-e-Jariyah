# Sadaqah-e-Jariyah — Archive Conventions

These rules decide where every file lives and what it is called. People follow them when adding files by hand;
the contribution pipeline gives them to Claude and then **checks every placement against them in code**
(`tools/rules.py`). If you change a rule here, change it there too.

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

## 3. Metadata (kept in the catalog, not the file name)
`author` (who made the notes), `professor` (who taught), `year` (`YYYY-YY`), `credit` (contributor's chosen name), `note`.

## 4. What we never publish
- **Copyrighted books or book chapters** (textbooks, Schaum's, publisher PDFs, scanned books). Link the library
  catalogue entry instead. Lecture slides and notes by teachers and students are fine.
- Personal data: phone numbers, attendance sheets, marks lists, ID cards, anything with another student's details.
- Executables, archives, HTML/SVG, or anything that isn't study material.

## 5. Formats accepted from contributors
`pdf png jpg jpeg webp pptx ppt docx doc xlsx xls mp4 mov webm txt md c cpp h hpp java py ipynb csv`
— up to 500 MB per file, 20 files per contribution. Bigger videos: share a Google Drive link.
