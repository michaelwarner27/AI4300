# Dexter Catalog Baseline: OCR Test Plan

## Purpose

Record how well the current pipeline (PyMuPDF text, pdfplumber tables, image and vector-figure extraction) handles a large, table-heavy catalog **before** OCR / Docling is added. After OCR is added, the same tests are run again and the results are compared.

**Document:** `documents/Dexter Trailer Catalog V1-24 p1-100.pdf`. These are PDF pages 1–100 of `scratch/Dexter Trailer Parts & Accessories Catalog V1-24.pdf` (516 pages in total).

- All 100 pages are catalog section A, *Axle Assemblies*. Catalog page `A<n>` is usually PDF page `n + 2`, so A43 is PDF page 45. Tests always use **PDF page numbers**.
- Every page has a text layer. OCR will make little difference to body text. It should matter for:
  1. text that exists only inside images, such as diagram labels and product photo callouts
  2. table structure, which pdfplumber currently breaks up (see below)
- Pages 1–100 contain about 2,600 embedded images. Many are tiny vector fragments on the Torflex dimension pages, which have around 98 images each.

**Baseline ingestion (2026-11-26):** 920 chunks. Log: `scratch/ingest-dexter-baseline.log`. Database snapshot taken before the Dexter document was added: `scratch/chroma_db.bak-pre-dexter`.

## What the survey found

| Observation | Example | Why it matters |
|---|---|---|
| pdfplumber breaks tables into pieces | Page 45 (#9 Torflex) *Full Load Dimensions* comes out as 3 separate tables: the header, row 4.80-8 and row 20.5X8.0-10. **Rows ST175/80R13 and 4.80-12 are lost**, probably because of the alternating row shading. *Mounting Bracket Dimensions* has the same problem. | This is the main thing table fidelity should measure. Docling's table model should fix it. |
| Some diagram labels exist only in images | Page 45: "Arm Length", "Center of Spindle". Page 7: "Stack Height". | Questions about these labels should fail now and pass after OCR. |
| Most callouts *are* in the text layer | Page 7: "Equalizer", "Shackle Link", "Free Arch Height". Page 15: part captions "23-26", "84545". | These are regression guards. OCR must not make them worse, for example by duplicating text. |
| Repetitive page templates | 40+ "DEXTER AXLE OPTIONS" pages that differ only in model and values (D35, D44, …, M12E) | Retrieval has to tell near-identical pages apart. This is a hard test for both BM25 and vectors. |
| Page footer and header repeat on every page | "Browse inventory … DEXTERINDEX.COM", "1-877-973-3632" | Noise in chunks. Track whether OCR adds more of it. |
| The junk-table filter drops most table pieces | pdfplumber extracted 460 tables, but only **41** were stored. The filter (`TextChunker.is_meaningful_table`) needs at least 2 rows, 2 columns and 6 filled cells. Page 45's one-row data pieces fail it, so that page stores only two **header-only** table chunks with no values. | Two different failures look the same from outside: the extractor broke the table, or the filter threw the pieces away. Measure them separately. |
| Table section paths can be stale | Page 45's tables are labelled `INSTALL DIRECTLY FROM THE BOX WITH LESS MESS > » Easy to identify retail packaging`, a heading from page 39 (a marketing page). | Wrong section lines hurt retrieval and citations. Track how often a chunk's section path doesn't match its page. |
| Text chunks are tagged with the wrong page | A text chunk's page is the page where it **starts** (`section.page_at(local)` in `TextChunker._chunk_sections`). Heading detection fails on this catalog, so sections, and therefore chunks, run over page boundaries. About half of the Dexter text chunks don't start on their tagged page. Many facts end up under page N−1: D35 bearings (p14) under page 13, K23-026-00 (p15) under page 14, M11V (p90) under page 89. | Citations point to the wrong page, and strict page checks fail. This is a pipeline bug, not an OCR target. Tests use the **true** page and report a lenient (true page or the page before) score next to the strict one, so the bug's effect stays apart from OCR's. Fixing it is a separate decision. |
| Tables are never split | Each table is one chunk whatever its size. The largest baseline table chunk is 1,208 characters, and there is no logic to repeat headers. | Docling may produce whole, larger tables. Table-splitting logic, with the header repeated on each piece, will be designed together with the Docling integration (not part of this baseline). |

## Representative page set

Most detailed checks focus on these pages. Each one stands for a type of page.

| Type | PDF pages | Notes |
|---|---|---|
| Section index / contents | 3, 5 | Short lines, no tables |
| Explainer text plus labelled diagrams | 6, 7, 9, 12 | Page 7 has 86 images and many callouts |
| Leaf-spring "Axle Options" plus overhang table | 14 (D35), 20 (D70), 32 (D100) | Key/value options and an overhang min/max table |
| Replacement-parts guide | 15, 21, 27, 31, 96, 97 | Part number, Dexter number and description rows. pdfplumber finds **0 tables** on these pages |
| Torflex model page | 44, 58 | Options and bullet points |
| Torflex dimension page | 45, 59, 75 | Wide multi-header tables, articulation table, image-only labels |
| Versa-Flex / Eliminator marine | 88, 92 | Similar layout, different product families |
| Rockwell (another brand) | 99, 100 | "ROCKWELL AXLE OPTIONS" instead of Dexter |
| Marketing / mostly image | 2, 10, 16, 25, 98 | Very little text. OCR may add text from the artwork |

## Test layers

Each layer writes results to `tests/results/dexter/<label>/`. The label is `baseline` now and `ocr-<n>` later, so results can be compared directly.

### 1. Ingestion snapshot: `tests/dexter_snapshot.py` (new)

Reads the live Chroma collection, filtered to the Dexter file. No LLM or embedding calls. Writes `snapshot.json` containing:

- **Totals:** chunk count by type (text / table / diagram), diagram files on disk, part numbers found
- **Per page:** text characters, chunk count, table chunks, diagram chunks, `Section:` heading path
- **Probe strings.** For each string in a probe list, record whether it appears in any chunk on its expected page. The list includes:
  - image-only labels (expected missing now)
  - text-layer labels (expected present)
  - table values that pdfplumber drops (e.g. `ST175/80R13` in a *table* chunk on page 45)
- **Noise:** how many chunks have a repeated footer, and the share of near-duplicate chunks
- **Table pipeline counts:** tables extracted, tables dropped by the junk filter and tables stored, in total and per page. Also the number of stored table chunks that contain only header rows (no data cells), and the largest table chunk size, which feeds into the later table-splitting design.
- **Section-path accuracy:** for each chunk, whether its `Section:` path matches a heading actually printed on that page or a nearby page. The check uses the page's title band, e.g. "#9 TORFLEX AXLES" on page 45. The snapshot records the share of stale paths and lists the worst pages.
- **Page-tag accuracy:** the share of text chunks whose opening text is printed on their tagged page, and how far off the rest are (in pages)
- **Probes:** listed in `tests/dexter-ground-truth/probes.yaml`. Probes with `baseline_fail: page-tag` are reported separately.

A `--compare baseline ocr-1` option prints a per-page diff and highlights changes of more than 20%.

### 2. Table fidelity: `tests/dexter_tables.py` (new)

**Ground truth:** `tests/dexter-ground-truth/tables.yaml`, one file for all tables so they can be verified in one pass. Each entry holds the page, a title, flattened column names, the rows, `verified` and notes on what to check.

**Starting set (about 10 tables):**

- p14 D35 overhang restrictions
- p15 boxed brake assemblies (parts list)
- p15 hubs (parts list)
- p45 Full Load Dimensions
- p45 Mounting Bracket Dimensions
- p45 Articulation Dimensions
- p59 Full Load Dimensions
- p96 replacement guide (one section)
- p100 RT35 options
- p32 D100 overhang

**Metrics per table:**

- **Found:** does a table chunk on that page contain the table's title or header?
- **Fragments:** how many table chunks the table was split into. 1 is ideal.
- **Row recall:** share of ground-truth rows whose cells all appear, in order, in one table chunk
- **Cell accuracy:** share of ground-truth cells present in the right row
- **Header kept:** whether the column headers appear in the same chunk as the data rows

Each table is scored at three stages, so the step where the loss happens is visible:

1. **Extracted:** the raw output of `TableExtractor`, before filtering
2. **After filter:** what is left after `is_meaningful_table`
3. **Stored:** the table chunks in Chroma

For example, on page 45 the data rows are partly found at stage 1 and all lost at stage 2. Each table also gets a **section path correct** flag.

Once table splitting is added with Docling, two more metrics apply:

- **Header repeated:** every piece of a split table carries its column headers
- **No row cut in half:** no row is divided between two pieces

### 3. Retrieval only: `tests/dexter_retrieval.py` (new)

Calls the hybrid search function used by `vector_store_search` (vectors, BM25 and exact part numbers). It needs the embedding server but **no LLM**, so it is cheap and repeatable.

**Cases:** `tests/dexter-ground-truth/retrieval.yaml`, 40 queries. Each has the expected PDF page(s), a category and an optional `xfail` flag. Scores are reported **strict** (true page) and **lenient** (true page or the page before), because of the page-tag bug.

| Category | Count | Example |
|---|---|---|
| Exact part number | 8 | "K23-026-00" → p15 |
| Spec lookup (text layer) | 8 | "bearings for a D35 3,500 lb axle" → p14 |
| Table cell | 10 | "#9 Torflex 4.80-12 tire full load 0° B dimension" → p45 |
| Telling near-identical pages apart | 6 | "M11V Versa-Flex capacity" → p90, not p88/p89 |
| Diagram label (image only) | 5, `xfail` | "what is arm length on a Torflex axle" → p45 |
| Concept / explainer | 3 | "what is torsion" → p6 |

**Metrics:** recall@5, recall@10 and MRR for each category. Also record whether the top hit was a text, table or diagram chunk. Cases are run against the whole corpus, so the RV manuals compete as they would in real use.

### 4. End to end: `DX` cases in `tests/test-queries.md`

**Cases:** about 15 cases in a new `DX` group, run with `python tests/test_queries.py --only DX --repeat 3`. They use the existing `retrieved`, `cited`, `facts` and `diagrams` checks.

- 4 part-number lookups. Example: "What is the Dexter number for K23-026-00?" expects the facts `182753`, `10\s*x\s*2.25`, `LH`.
- 4 table questions. Example: "Max hub-face overhang for a D35 with 4-inch drop 3.00 tubing and disc brakes?" expects `23"` and min `15"`, citing p14.
- 2 multi-row or comparison questions. Example: "Which Torflex axles cover 7,000 lb?" expects #12, #13N and #13.
- 2 diagram questions with `diagrams: true`. Example: "Show me the Torflex start angle diagram" (p7 / p45).
- 3 image-only questions, marked `xfail`.
- 1 cross-document question. Example: a question that names an RV manual and Dexter together, to check the catalog doesn't drown out other sources.

**Changes needed:**

- Add the Dexter file to `tests/corpus.md`.
- Add an `xfail: true` key to the case format. `test_queries.py` would then report such cases as `XFAIL` or `XPASS` instead of counting them as failures. This is a small runner change: `_check_case` accepts the key and `summarize` reports it separately.

## Expected-fail convention

- A case or probe gets `xfail: true` only if the answer exists **only in pixels**, meaning the text layer doesn't contain it. Check this with `page.get_text()` before tagging.
- **XPASS after OCR** is the success signal. Remove the tag once it passes reliably (3 out of 3 runs).
- **A non-xfail case that fails after OCR** is a regression and must be investigated before OCR is merged.

## Ground-truth workflow (I draft, you verify)

1. I pull candidate facts from the page text and from rendered page images, and write them with `verified: false`.
2. You check each item against the catalog. Rendered pages will be put in `scratch/dexter-pages/` for convenience.
3. Only `verified: true` items count toward pass rates. The scripts report unverified items as `UNVERIFIED`.

Values I have already read from the page images. These are drafts and still need your check:

- **p14 (D35):** bearings are L68149 inner / L44649 outer. Overhang for 4" DROP—3.00" DISC: minimum 15", maximum 23".
- **p15:** K23-026-00 → 182753, "ELECTRIC BRAKE, DEXTER, 10 X 2.25, LH, 3.5K/PR". Hub 84545 → 106800, 1750 lb capacity.
- **p45 (#9 Torflex):** row 4.80-12 at 0°: H -1.1", B 8.4", C 11.9". Bolt size 5/8". Tube size 2.17".

## Comparing before and after OCR

1. Commit the baseline results in `tests/results/dexter/baseline/`.
2. Add OCR / Docling behind a config flag, so the old path still exists.
3. Re-ingest only the Dexter file: `manage_documents.py --update` and then `ingestion_main.py`. Record the ingestion time, which OCR may increase a lot.
4. Run all four layers with the label `ocr-1`.
5. `dexter_snapshot.py --compare` plus a short summary table (by layer and metric: baseline → OCR, with the change) goes into a HW report.
6. Run the existing suites (`verify_ingestion.py`, `test_queries.py`) as well, to confirm the RV manuals didn't get worse.

## Implementation steps (check in after each)

1. **Ground-truth drafts.** *(drafted 2026-11-26, waiting for your check)* The drafts in `tests/dexter-ground-truth/` are `tables.yaml` (11 tables), `retrieval.yaml` (40 queries), `probes.yaml` (32 probes, all checked against the live database) and `dx-cases.md` (16 cases). The reference pages are rendered in `scratch/dexter-pages/`.
2. **Snapshot script.** *(done 2026-11-26)* `tests/dexter_snapshot.py` wrote `tests/results/dexter/baseline/snapshot.json`. Baseline results:
   - **Chunks:** 920 in total: 232 text, 41 table, 647 diagram.
   - **Tables:** 460 extracted, 41 kept by the filter, 41 stored. **40 of the 41 stored tables are header-only**, so only one stored table chunk has values (the p12 hub-face table).
   - **Page tags:** 69% of text chunks sit on their tagged page, and 79% of text content is on its tagged page. 45 chunks belong mainly to the next page.
   - **Section paths:** 36% are current. 365 chunks share one stale path from page 39.
   - **Noise:** the footer appears in 525 chunks, and 14% of text chunks are near-duplicates.
   - **Probes:** 30 pass, 2 known page-tag failures.
3. **Table fidelity script.** *(done 2026-11-26)* `tests/dexter_tables.py` wrote `tests/results/dexter/baseline/tables.json`. Baseline results over the 11 tables:

   | Stage | Found | Row recall | Cell accuracy | Header kept |
   |---|---|---|---|---|
   | Extracted | 7/11 | 34% | 44% | 1/9 |
   | After filter | 1/11 | 9% | 9% | 1/9 |
   | Stored | 1/11 | 9% | 9% | 1/9 |

   - Only page 12 survives whole.
   - pdfplumber returns only the shaded rows of the Torflex tables, as one-row pieces, and the filter then drops them all.
   - The parts lists (p15, p96) are never detected as tables.
   - Every stored page 45 and page 59 table chunk has a stale section path.

   Scoring rules: a row earns credit only if at least half its cells, and at least 2, match in order. A filled-in first cell that differs from the expected key means a different row. Both rules were checked against the raw page 45 pieces.
4. **Retrieval script.** *(done 2026-11-26)* `tests/dexter_retrieval.py` wrote `tests/results/dexter/baseline/retrieval.json`. It records ranked hybrid hits (top 20) and whether the right page reaches the LLM's first context. Baseline results, strict / lenient, for the 36 cases that aren't xfail (this includes DXR-X03):

   | Category | R@10 | MRR | In context |
   |---|---|---|---|
   | Part number | 88% / 100% | 0.73 / 0.92 | 100% |
   | Spec | 38% / 88% | 0.18 / 0.64 | 75% / 88% |
   | Table cell | 70% / 80% | 0.36 / 0.52 | 80% |
   | Disambiguation | 50% / 83% | 0.08 / 0.55 | 67% / 83% |
   | Concept | 100% | 1.00 | 100% |
   | **All** | **67% / 89%** | **0.43 / 0.69** | **83% / 89%** |

   - **Table chunks:** only 1 of 9 typed table cases reaches a table chunk in the top 10. Table answers come from flat text chunks.
   - **Page-tag bug:** this is the biggest gap between strict and lenient (spec: 38% → 88%).
   - **DXR-X03:** moved out of xfail. Page 7 ranks first without OCR, so the case can't show a gain.
   - **Image-only cases are a weak OCR signal** at page level. The probes and DX facts are the stronger checks.
5. **End-to-end cases.** *(done 2026-11-26)* What changed:
   - `test_queries.py` accepts `xfail`, `baseline_fail` and `verified` case keys. It reports XFAIL/XPASS and KFAIL, and none of these count toward the exit code.
   - The 16 DX cases are in `tests/test-queries.md`.
   - The catalog is documented in `tests/corpus.md`.

   The baseline run is `tests/results/query_results_018.txt` (3 runs per case, and every case gave the same result on all 3):

   | Result | Cases |
   |---|---|
   | PASS 3/3 | DX02, DX04, DX05, DX07, DX09, DX11, DX12, DX16. That is 8 of the 10 ordinary cases (24/30 runs). |
   | **FAIL 0/3** | **DX06, DX08: wrong numbers stated with confidence.** The agent answered from p43 (#8 Torflex) instead of p45 (#9). The chunk's section path is stale, so nothing in it names the model, and both pages share one table layout. B was given as 10.5" instead of 8.4", and F as 5.12" instead of 3.75" / 2.88". |
   | KFAIL (page-tag) | DX01 and DX03 give correct answers but cite page N−1. DX10 retrieves the D20 page (p13) filed under the wrong heading and says the documents don't cover D35 or D70. |
   | XFAIL | DX13, DX14, DX15 (image-only facts) |

   - **Root causes:** DX06, DX08 and DX10 fail because of stale headings and page tags, not missing OCR. Docling's layout analysis may fix headings as a side effect, so these cases show both effects.
   - **Mistake found in my cases:** the first run used unquoted YAML questions containing `#9`, which YAML treated as a comment, so the questions were cut off. They are quoted now. A scan of all 81 cases found no other cut-off values.
6. **Baseline report and commit.** *(done 2026-11-26)* `tests/results/dexter/baseline/README.md` summarises all four layers. It also says what improvement should look like after OCR and how to reproduce each run. All 11 tables in `tables.yaml` were verified by the user. The DX case facts are still unverified.

## Risks and notes

- **Answers vary between runs.** End-to-end results vary from run to run, so use `--repeat 3` and pass rates. Retrieval and table metrics are fully repeatable and are the main comparison.
- **Chunk counts will change with OCR.** Docling will change chunk boundaries, so per-chunk diffs will be noisy. Compare by page and by probe instead.
- **Part numbers may clash** between Dexter part numbers and the RV manuals, for example 5-digit hub numbers. Watch for this in exact-match search.
- **Table splitting is out of scope for the baseline.** Splitting logic will be added with Docling, and its metrics only apply from then on.
- **Only 100 of 516 pages are used.** Results don't cover sections B onward (brakes, fenders, tires, …). Add more pages later if needed.
- **The live database now includes Dexter.** The existing query suite results may shift slightly as a result. Re-run it once to set a new baseline, or restore `scratch/chroma_db.bak-pre-dexter` when you need the old corpus only.
