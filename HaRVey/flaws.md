# Known Flaws

Issues found in the ingestion pipeline while testing against `Dometic 457915 Installation & Operating Instructions Manual1.pdf`. Page numbers are 1-indexed (as shown in the PDF).

## Diagram context (layout analysis)

Every diagram now gets a `diagram_reference` chunk. Its text holds the section heading path, a nearby "FIG. N" label, the text inside the figure and up to 500 characters of text leading up to it from the previous heading (`src/ingestion/layout.py`). Caption-less figures such as Dometic pages 11 and 17 are found through their section path. Remaining issues:

- **Cover pages and site banners can become headings.** On the Dometic file, "Manualslib.com - The Global Manuals Library" (page 1) and "Other ManualsLib Projects" (page 3) are detected as headings, so the page 1–4 figures carry them in their path.
- **Document titles can sit at the top of every path.** On Penguin, "Roof Top Air Conditioner > INSTALLATION & OPERATING INSTRUCTIONS" prefixes the sections on pages 1–4. A first-page style is only dropped when it is larger than every later heading.
- **Context is the text before the figure in reading order,** which is not always the text that describes it (e.g. a figure placed above the paragraph that refers to it).
- **Page-sized vector regions (over 50% of the page) are treated as frames.** Their text counts as page text, and the figure is positioned at the bottom of the page. A real full-page diagram is therefore given every heading on that page and the text before it, not its own labels as "figure text".
- **Reading order is a two-column heuristic.** Blocks are split at the page middle. Large headings and full-width blocks start a new band. Three-column pages or offset columns will be read in the wrong order.

## Diagram extraction

- **Non-diagrams detected as figures.** PyMuPDF groups any cluster of vector drawing as a figure. Seen on this manual:
  - Page 4: info/title boxes ("RECORD THIS INFORMATION FOR FUTURE REFERENCE", service office address).
  - Page 11: a table region ("table below. See (FIG. 11)...").
  - Page 19: the "Other ManualsLib Projects" banner.
  These get chunks because they have captions.
- **Merged figures.** Figures that sit close together may be cropped as one image. Accepted for now.
- **Caption is raw text from the region,** truncated to 300 characters. It can include stray labels and run-on text, and words cut off at the crop edge (Dometic page 4: "VICE OFFICE etic Corporation").
- **Shifted-font text is decoded, other bad encodings are dropped.** Text stored with character codes shifted by 29 (Dometic page 18: `7+(50267$7` → `THERMOSTAT`) is decoded in captions and page text. Captions that are still unreadable after that are left empty rather than OCR'd.
- **Kerned text keeps stray spaces,** e.g. Penguin "KEEP T HIS A REA F REE OF OBST RUCTIONS".

## Tables

- **Table text is duplicated.** A kept table is embedded as a `table` chunk (with its section line) and its text also appears inside the regular text chunks. Intentional, but it can return two hits for one table.
- **Table filter thresholds are tuned to one manual.** It requires at least 2 rows, 2 columns, 6 cells with text and 60% of cells filled. 28 tables were found and 5 kept. Other PDFs may need different thresholds.
- **Whole tables are never split.** The largest table here is about 2,150 characters, about twice `CHUNK_SIZE` (1000). Very large tables could exceed the embedding model's input limit.

## Section headings

Headings now come from `FontHeadingDetector` (font size, bold, position), not from ALL CAPS text. The old regex `HeadingExtractor` has been removed: if PyMuPDF can't open a file, it gets no headings and is chunked page by page. Run `python tests/verify_ingestion.py --headings` to print every heading found in `documents/`. Remaining issues:

- **Bold labels at body size can still become low-level headings,** e.g. Dometic "USA" / "CANADA" (address blocks) and CCC2's "Quick reference to LCD icons" callouts. Body-size bold is dropped only when a style is used more than 4 times per page on average.
- **Bold table headers become headings when pdfplumber doesn't find the table** (or the table fails the junk filter), e.g. CCC2 Diagnostic "Diagnostic / Command".
- **Heading levels are relative ranks of font styles.** A style used only once is folded into the next smaller style. Different documents can't be compared by level number.
- **Fixed thresholds:** 15% larger than body text, at most 80 characters / 12 words per line, a style run of at most 3 lines, repeats on 30% of pages = header/footer, 3 repeated entries = TOC page. They were tuned on the 7 PDFs in `documents/`.
## Text chunks (sections)

Text chunks come from the layout text, split at headings. Each chunk starts with `Section: A > B`. Remaining issues:

- **Wrong headings give wrong sections.** The section path is only as good as heading detection (see above). On Dometic, pages 1–3 are filed under "Manualslib.com - The Global Manuals Library" / "Other ManualsLib Projects", not the document title.
- **The section line counts toward `CHUNK_SIZE`,** so a long path leaves less room for text (never under 200 characters).
- **Words hyphenated across lines stay split** ("op-\neration"), and printed page numbers ("13") that differ per page are not removed like repeated footers.
- **Table text in text chunks comes out one cell per line** (each cell is its own text block). The `table` chunk still holds the table as Markdown.
- **Bullets are written as "- ".** Private-use font bullets (e.g. Wingdings U+F097) and common bullet glyphs are normalized and re-attached to their item. Dots that have text to their left (table markers, e.g. Coleman Parts availability columns) are left in place, but still written as "-".
- **Very short sections make very short chunks** (e.g. "C. Fan Motor" is 117 characters). Sections are never merged.

## Query side

- **Sections need re-ingestion.** Records stored before section support have no `section_path`. They still work (no `section` field, neighbors not restricted, `read_section` says to use `read_page`), but get none of the section benefits.
- **The first-retrieval cutoff (`SEARCH_MIN_SIMILARITY`, default 0.5, shared with `search_documents`) is a narrow band.** Relevant chunks score about 0.55–0.8, off-topic queries at most about 0.4 (measured 2026-11-23). Small text changes can move a borderline chunk across it: the CCC2 E1 causes list scored 0.555 with detached bullets and was cut at the old 0.6 cutoff (Q02).
- **`read_section` loads every chunk of the document** to match sections in Python (Chroma can't prefix-match metadata). Fine for manuals of a few hundred chunks; slow for very large documents.
- **`read_section` returns at most 25 passages.** Larger sections (e.g. Dometic "INSTALLATION INSTRUCTIONS", 7 pages) are cut off, and the reply names the sub-sections left out.
- **Citation sections are only shown when they can be confirmed** from the retrieved passages: the LLM's own `section` must match one, or the snippet or a single-section page must identify it. Otherwise the citation shows just filename and page.


