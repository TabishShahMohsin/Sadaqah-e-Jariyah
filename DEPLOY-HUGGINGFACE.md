# Publishing: files on Hugging Face, website on Cloudflare Pages

```
browser ──► sadaqah-e-jariyah.pages.dev   (Cloudflare Pages: website + index.json, ~6 MB)
        └─► huggingface.co/datasets/TabishShahMohsin/sej-files   (every study file, ~27 GB)
```

Both are free and need no card. Everything runs from this Mac; the files never go to GitHub.

## Every time the archive changes

```bash
pip3 install -r tools/requirements.txt              # once
python3 tools/hf_publish.py scan                     # check new files (ISBN, personal metadata); cached
python3 tools/hf_publish.py push --dry-run           # see what will change on Hugging Face
python3 tools/hf_publish.py push                     # make the dataset match; re-run if it stops early
python3 tools/hf_publish.py site                     # build _dist/ (website + index.json)
npx wrangler pages deploy _dist --project-name sadaqah-e-jariyah --branch production
```

Run `wrangler` from the repo folder: it also uploads `functions/` (the visit counter).

## What gets published

One rule set decides both what is uploaded and what the website lists, so they always agree.

| | |
|---|---|
| **Left out** | `Books-and-Resources/` sections · names with a publisher or textbook author (Pearson, Pressman, Tocci, Wiley, "textbook", "edition" …) · PDFs with an ISBN on their first pages (syllabi excepted) · anything listed in `catalog/exclude.txt` · non-study formats (`.exe`, `.out`, `.class`, saved web pages) |
| **Renamed** | enrolment / faculty numbers are taken out of file names |
| **Cleaned** | author fields of PDFs and Office files, and personal photo EXIF (GPS, owner), are blanked in the published copy; your originals are never changed |

The list of left-out files is written to `.sej-cache/left-out.txt` by `site`.
To take something down: add `=<path>` (or a pattern like `*/Some-Folder/*`) to `catalog/exclude.txt`, then `push` and `site`.

`push` makes Hugging Face match exactly: new files are uploaded, renamed ones are copied on the server (no re-upload),
anything no longer published is deleted. Deleted files stay in the dataset's git history until it is squashed
(dataset Settings → "Squash history", or `HfApi().super_squash_history(...)`).

## Setup (already done)

`catalog/hf-config.json` (git-ignored):

```json
{ "repo_id": "TabishShahMohsin/sej-files", "token": "hf_… (write token)" }
```

`HF_REPO` / `HF_TOKEN` environment variables override it.

## Versions

* **The catalog is the archive's history.** `catalog/base.jsonl` plus one file per change in `catalog/changes/`
  (who added what, moves, credits, conversions) are committed to git with every change: `git log catalog/` shows
  every version of the archive. The study files themselves stay out of git.
* **Every publish is tagged** on Hugging Face (`v2026-11-04`, `v2026-11-04.2` …), so any published state can be
  fetched again: `huggingface-cli download TabishShahMohsin/sej-files --repo-type dataset --revision v2026-11-04`.
* **Nothing is overwritten or deleted on this Mac.** Files that are replaced (e.g. slides converted to PDF) move to
  `_Originals/` at the same path; the change set records their sha256, and every such change can be undone.
* **Back up the originals** (`BTech-and-BE/`, `_Originals/`, `_Sources/`) somewhere other than this Mac, e.g. an
  external drive with Time Machine: Hugging Face has only the published copies, not the originals.

## Slides → PDF

```bash
python3 tools/sorter/install_fonts.py             # once per machine (Linux): free stand-ins for Office fonts
python3 tools/convert_slides.py convert            # converts decks not done yet; re-run until "all done"
python3 tools/convert_slides.py report --sheets    # what the checks found + contact sheets of flagged decks
python3 tools/convert_slides.py apply [--accept PATH]
python3 tools/convert_slides.py undo <change-id>  # if needed
```

Checks per deck: one PDF page per visible slide, no character printed as an empty box, fonts without a close
stand-in, audio/video (published next to the PDF as `… (video 1).mp4`). Then `hf_publish.py scan / push / site`.
