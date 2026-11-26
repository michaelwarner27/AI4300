# Repeated diagram filtering: plan

Status: planned, not implemented.

## Problem

Logos and other decorations that repeat across pages are extracted every time,
and each copy gets its own `diagram_reference` chunk.

Measured on `Dexter Trailer Catalog V1-24 p1-100.pdf` (647 extracted images):

- SHA-256 finds 47 groups of identical files (219 redundant copies).
- The "Made in America" flag logo does not match by file or pixel hash: it is a
  vector drawing, and its rendered crop shifts on the pixel grid (153x124 vs
  154x124).
- A 256-bit dHash puts 55 copies of that logo within 34 bits of
  `_p3_fig0`; no other image is within 70 bits.

## Decisions

| Topic | Choice |
|---|---|
| Handling | Keep one copy of a filtered group, remove the rest |
| What counts | Candidates go on a review list; the user approves each group |
| Matching | SHA-256, then dHash, then a pixel comparison to confirm |
| Scope | Per document, at ingest |
| Review flow | Ingest keeps everything and writes a report; a command applies the marks and saves them for later ingests |
| Report | Numbered contact sheet plus a small editable decisions file |
| Threshold | Group appears on 5 or more distinct pages |
| Kept copy | First occurrence; its chunk text lists the other pages |

## Design

### Matching (new module `src/ingestion/dedup.py`)

1. Group files with the same SHA-256.
2. Merge groups whose dHash differs by at most 40 bits and whose width and
   height are close.
3. Confirm each merge with a grayscale pixel difference, taking the best result
   over shifts of a few pixels. The cutoff is set in step 1.

Every member is compared with the group's representative (its first occurrence),
so a chain of small differences cannot join two unlike images. An image that
cannot be opened stays on its own and is kept.

### Review files

Stored in `diagram_review/<image prefix>/`, outside `extracted_diagrams/` so
re-ingest and delete do not remove the user's choices.

- `sheet.png`: one numbered thumbnail per candidate group.
- `decisions.yaml`: per group, `decision: pending | filter | keep`, copy count,
  pages and one example caption.
- `group_<n>.png`: the representative image. Later ingests match saved decisions
  by image, not by filename, because filenames can change.

`pending` and `keep` both leave every copy in place. A new report keeps earlier
decisions and adds new groups as `pending`.

### At ingest

Runs after layout analysis and before chunking, so the kept copy already has its
heading and context.

1. Group the document's diagrams.
2. Groups matching a saved `filter` decision collapse to the first occurrence:
   other files are deleted, `also_on_pages` is set on the kept diagram and
   `diagram_count` is updated.
3. Groups on 5 or more pages are written to the report.
4. Log the number of candidate groups and removed copies.

If dedup raises an error, ingest continues with all diagrams and logs a warning.

`DiagramMetadata` gains `also_on_pages`, and `diagram_chunk_text` adds an
"Also on pages: ..." line, with consecutive pages written as ranges.

### Apply command

`python manage_documents.py --apply-diagram-review <document>`

1. Read the decisions and group the document's current diagram files.
2. Rewrite the kept chunk with the page list and re-embed it.
3. Delete the other copies' chunks from Chroma, then their files.

Running it twice changes nothing the second time. Changing a group from
`filter` back to `keep` needs `--update` on the document to restore the copies.

## Steps

Check in after each step.

1. **Matching and calibration.** Write `dedup.py`. Run it on the Dexter images
   without deleting anything. Set the pixel cutoff; confirm the flag logo forms
   one group and spot-check the other groups for false matches.
2. **Report.** Contact sheet and decisions file, with decisions preserved across
   runs.
3. **Ingest integration.** `also_on_pages`, chunk text, saved decisions applied.
4. **Apply command.** Chroma and file cleanup.
5. **Tests and docs.** Add cases to `tests/verify_ingestion.py`, update the
   README, add `diagram_review/` to `.gitignore`, run on Dexter end to end.

## Done when

- The flag logo is one numbered group on the contact sheet.
- After marking it `filter` and applying, one file and one chunk remain for it,
  and the chunk lists the other pages.
- Re-ingesting gives the same result without marking it again.
- Groups left `pending` or `keep` are untouched.

## To check during implementation

- Whether anything depends on `total_chunks` or an unbroken `chunk_sequence`
  after chunks are deleted.
- Whether a YAML library is already a pinned dependency; use JSON if not.
- Diagrams on a page whose copy was removed can no longer be auto-attached to
  answers citing that page. Acceptable for logos; consider it before filtering
  real content.
- Filtering changes Dexter chunk counts and retrieval, so decide whether it
  lands before or after the baseline in `tests/dexter-ocr-test-plan.md`.
