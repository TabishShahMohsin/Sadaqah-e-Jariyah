"""
The contribution sorter: anything in (files, folders, zips, links), everything placed, credited or explained.

Stages (each file gets exactly one outcome; nothing disappears):
  ingest.py   gather inputs, unpack zips, drop system junk, convert to published formats (CONVENTIONS.md §5)
  evidence.py evidence per file: pages, text, author field, text and visual fingerprints, thumbnails
  dedup.py    exact duplicates (archive or the same upload) are settled in code, near-duplicates become hints
  courses.py  the curriculum-backed course finder the agent searches
  agent.py    Claude with tools (find courses, look at pages, compare, decide) for everything that needs judgment
  decide.py   every decision is checked in code (rules.py) before it counts
  report.py   the review page: what happened to every file, uncertain items first
  pipeline.py ties the stages together; used by tools/sort_inbox.py (local) and tools/triage.py (website contributions)
"""
