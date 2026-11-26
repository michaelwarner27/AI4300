# Dexter catalog baseline (before OCR)

This baseline records how the pipeline handles a large, table-heavy catalog **before** OCR / Docling is added. It is the reference point for every later comparison. The method is in [`tests/dexter-ocr-test-plan.md`](../../../dexter-ocr-test-plan.md).

| | |
|---|---|
| **Recorded** | 2026-11-26 |
| **Document** | `Dexter Trailer Catalog V1-24 p1-100.pdf`: PDF pages 1–100 of the 516-page catalog, all in section A *Axle Assemblies*. The file hash starts `131c7438816a99a5`. |
| **Pipeline** | PyMuPDF layout text, pdfplumber tables, image and vector-figure extraction. Chunk size 1000, overlap 100. |
| **Models** | Embeddings: Qwen3-Embedding-8B. LLM: Gemma-4-26B-A4B. |
| **Corpus** | 6,757 chunks: the 49-document test corpus plus this catalog. Searches compete with the RV manuals. |
| **Ingestion** | 920 chunks (232 text, 41 table, 647 diagram), no errors |

## Headline results

1. **Almost no table data reaches the database as tables.**
   - pdfplumber extracts 460 table pieces, the junk filter keeps 41, and **40 of those 41 are header rows with no values**.
   - Of the 11 verified ground-truth tables, only one (p12) is stored intact.
   - Table values survive only as flat text in ordinary chunks.
2. **The agent can give confidently wrong numbers on near-identical pages.**
   - Asked about the #9 Torflex, it quoted the #8 Torflex table on p43 in all 3 runs of DX06 and DX08.
   - The chunks carry a stale section heading, so nothing in them names the model.
3. **Page tags are often off by one.**
   - Text chunks are tagged with the page where they start, and they run onto the next page.
   - Only 69% of text chunks sit on their tagged page, and correct answers cite page N−1.
   - Scoring with lenient pages (which also accept the page before) raises retrieval recall@10 from 67% to 89%.
4. **Some text exists only in images.**
   - Six probed labels (e.g. "Arm Length", "Center of Axle", "Made in America") are missing from every chunk.
   - All three image-only DX cases fail.

Points 2 and 3 come from heading detection and chunking, not missing OCR. They are reported separately (KFAIL, lenient scores) so an OCR change can be judged on its own.

## Layer 1: ingestion snapshot ([`snapshot.json`](snapshot.json))

| Measure | Baseline |
|---|---|
| Chunks (text / table / diagram) | 920 (232 / 41 / 647) |
| Diagram files (on disk) | 647 (647) |
| Distinct part numbers | 472 |
| Tables extracted, kept by filter, stored | 460 → 41 → 41 |
| Header-only stored tables | 40 of 41 |
| Largest table chunk | 1,208 chars (p12) |
| Text chunks fully on their tagged page | 154 / 223 (69%). 28 straddle two pages, 41 belong mostly to another page. |
| Text content on its tagged page | 79% |
| Section path current (heading on its page or the one before) | 334 / 920 (36%). 365 chunks share one stale path from p39. |
| Chunks containing the repeated footer | 525 |
| Near-duplicate text chunks | 33 (14%) |
| Probes (32) | 30 pass, 2 known page-tag failures. All 6 image-only labels are missing as expected. |

## Layer 2: table fidelity ([`tables.json`](tables.json))

11 ground-truth tables, all verified.

| Stage | Found | Row recall | Cell accuracy | Pieces per table found | Header kept |
|---|---|---|---|---|---|
| Extracted (pdfplumber) | 7/11 | 34% | 44% | 2.86 | 1/9 |
| After junk filter | 1/11 | 9% | 9% | 1.0 | 1/9 |
| Stored in Chroma | 1/11 | 9% | 9% | 1.0 | 1/9 |

- **Torflex dimension tables (p45, p59):** pdfplumber returns only the shaded rows, each as a separate one-row piece. Merged cells, such as the start angle, come out blank. The filter then drops every one-row piece.
- **Parts lists without ruled lines** (p15 brakes and hubs, p96 springs): never detected as tables.
- **Stored table chunks on p45 and p59:** all have a stale section path.

## Layer 3: retrieval only ([`retrieval.json`](retrieval.json))

40 queries scored by hybrid search rank (top 20) and by whether the right page reaches the LLM's first context. Scores are strict / lenient.

| Category | Cases | R@1 | R@5 | R@10 | MRR | In context |
|---|---|---|---|---|---|---|
| Part number | 8 | 62% / 88% | 88% / 100% | 88% / 100% | 0.73 / 0.92 | 100% / 100% |
| Spec | 8 | 12% / 50% | 38% / 88% | 38% / 88% | 0.18 / 0.64 | 75% / 88% |
| Table cell | 10 | 20% / 40% | 70% / 80% | 70% / 80% | 0.36 / 0.52 | 80% / 80% |
| Disambiguation | 6 | 0% / 33% | 17% / 83% | 50% / 83% | 0.08 / 0.55 | 67% / 83% |
| Concept | 3 | 100% | 100% | 100% | 1.00 | 100% |
| **All (not xfail)** | **36** | **33% / 58%** | **61% / 89%** | **67% / 89%** | **0.43 / 0.69** | **83% / 89%** |
| Image only (4 xfail + DXR-X03) | 5 | 20% / 20% | 20% / 60% | 20% / 80% | 0.22 / 0.38 | 60% / 80% |

The "All" row includes DXR-X03, which is no longer xfail. The image-only row is listed for reference.

- **Table chunks:** a table chunk reaches the top 10 in only 1 of 9 table-cell cases.
- **Disambiguation:** with strict pages, the right page beats its near-identical neighbours in 1 of 6 cases. With lenient pages it does in 5 of 6.
- **XPASS:** none. DXR-X03 was moved out of xfail because page 7 ranks first without OCR.

## Layer 4: end-to-end DX cases ([`../../query_results_018.txt`](../../query_results_018.txt))

16 cases, 3 runs each. Every case gave the same result in all 3 runs. The DX facts are still marked unverified.

| Result | Cases |
|---|---|
| PASS 3/3 | DX02 hub 84545, DX04 hanger kit, DX05 D35 overhang, DX07 hub face equation, DX09 Versa-Flex capacities, DX11 and DX12 diagrams, DX16 refuses the made-up D500 |
| FAIL 0/3 | DX06 #9 Torflex full-load B, DX08 #9 Torflex bracket F. Both quote the #8 Torflex table (p43). |
| KFAIL (page-tag) | DX01 K23-026-00, DX03 EB01-130 (correct answers, citing page N−1). DX10 D35 vs D70 bearings (says the documents don't cover it). |
| XFAIL | DX13 arm length, DX14 axle label, DX15 made in America |

Ordinary cases: 24 of 30 runs pass (8 of 10 cases).

## What should change after OCR / Docling

| Signal | Baseline | Improvement looks like |
|---|---|---|
| Stored tables with values / verified tables stored intact | 1 / 1 of 11 | Most tables stored whole, with headers |
| Table row recall (stored) | 9% | Close to extracted recall, and extracted recall well above 34% |
| Image-only probes found | 0 of 6 | Most found (XPASS in the snapshot) |
| DX13–DX15 | XFAIL | XPASS |
| Table-cell retrieval reaching a table chunk | 1 of 9 | Most |
| Section path current | 36% | Higher, if Docling's layout fixes headings (this would also help DX06, DX08 and DX10) |
| Page tags | 69% on page | Unchanged unless chunking changes. A gain here comes from the page-tag fix, not OCR. |
| Ordinary DX cases, regular probes, other query groups | as above | No regressions |

## Reproducing

```bash
python ingestion_main.py                                   # ingests the catalog if it is new or changed
python tests/dexter_snapshot.py   --label <label>          # layer 1 (no servers)
python tests/dexter_tables.py     --label <label>          # layer 2 (no servers)
python tests/dexter_retrieval.py  --label <label>          # layer 3 (embedding server)
python tests/test_queries.py --only DX --repeat 3          # layer 4 (embedding + LLM servers)

python tests/dexter_snapshot.py  --compare baseline <label>
python tests/dexter_tables.py    --compare baseline <label>
python tests/dexter_retrieval.py --compare baseline <label>
```
