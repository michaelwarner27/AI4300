# Test corpus

The manuals the cases in `tests/test-queries.md` run against: the 7 original manuals, 41 added from `Harvey-Manuals-All/` and 1 made-up test document. Ingested 2026-11-25 with no errors: **49 documents, 5,822 chunks** (3,343 text, 192 table, 2,287 diagram reference).

## Rebuilding

```bash
python tests/build_corpus.py --copy          # files listed in tests/corpus-manifest.txt, plus the test document
python ingestion_main.py                     # ingests new or changed files only
python tests/test_queries.py --validate      # every document and page the cases expect is present
```

## Documents

"Distractor" means no case expects the document; it competes for search results. Chunks are total / diagram reference.

### Thermostats: similar manuals with different answers

| Document | Chunks | Tests | Cases |
|---|---|---|---|
| CCC2-Diagnostic-Mode-With-Reset | 15 / 8 | diagnostic commands, error codes | Q01-Q03, RW01, RW07, RW13, M03 |
| CCC2-Operating-Instructions | 93 / 24 | fan speeds, filter reminder | Q02, Q08-Q10 |
| CCC II Operating Instructions | 89 / 19 | same content as the CCC2 manual | accepted for Q02, Q08, Q09 |
| CCC II Reset & Diag | 35 / 25 | same content as CCC2 diagnostics | Q01, Q03, RW02 |
| CT Operating Instructions | 58 / 8 | CT reset | RW06, C05 |
| CT Reset & Diag | 31 / 22 | CT reset and diagnostics differ from CCC2 | RW02, RW06, RW09, C02, C05, C08, M01, R05, S05 |
| CT-Bluetooth | 110 / 40 | app control that the CCC2 lacks | Q10 |
| SZLCD Operating Instructions | 43 / 8 | another reset procedure | ambiguity for C01 |
| Analog Operating Instructions | 35 / 15 | another thermostat | ambiguity for C01 |
| 4 Button Reset Instructions | 6 / 4 | multi-step reset | RW03 |
| 4 Button Trouble Shooting Bulletin | 84 / 25 | another reset procedure | ambiguity for C01 |
| 5 Button Operation Manual | 63 / 20 | another thermostat | ambiguity for C01 |
| 5 Button Trouble Shooting Bulletin | 65 / 26 | 5 button reset | RW06, C01 |

### Dometic air conditioners

| Document | Chunks | Tests | Cases |
|---|---|---|---|
| Dometic 457915 Installation & Operating | 106 / 41 | spec table, maintenance | Q04, Q07, Q08 |
| Penguin-Brisk-Air | 103 / 30 | distractor | |
| Blizzard H5403XX Ducted | 113 / 37 | variant pair: roof depth, charge | C03, C07, C09, C10, M03, R07 |
| Blizzard H5403XX Non-Ducted | 112 / 40 | variant pair | RW05, C03, C07, C09, C10, R06 |
| Air Command Ducted | 119 / 54 | variant pair: bolt torque | distractor for R08 |
| Air Command Non-Ducted | 111 / 48 | variant pair | R08 |
| AC-HP Diag Service Manual | 491 / 197 | same text as the next file | distractor |
| Service Manual AC_3311071.017 | 491 / 197 | same text as the previous file | distractor |
| Discharging Start and Run Capacitors | 24 / 15 | safety warning, false premise | S03, S08 |
| Air Conditioner Freeze-Up | 5 / 0 | short document, no model named | RW12 |

### Furnaces

| Document | Chunks | Tests | Cases |
|---|---|---|---|
| 79 Series User's Manual | 180 / 71 | shared safety steps | C06 |
| 85 Series Installation | 142 / 50 | shared safety steps | C06 |
| 89 Series Installation | 295 / 85 | gas pressure, two fault charts | RW04, R04 |
| AFS Installation | 115 / 51 | near-identical sibling | C06 |
| AFM Installation | 125 / 53 | near-identical sibling | C06 |
| DF Operating Instructions | 58 / 13 | shared safety steps | C06 |
| DF Parts List | 25 / 8 | part numbers | RW08, M06, R01, R02 |
| DF Service Manual | 318 / 134 | fault codes | R03 |
| Suburban 205620 Furnace | 222 / 77 | other brand | distractor |

### Water heaters

| Document | Chunks | Tests | Cases |
|---|---|---|---|
| Dometic LP Models IOM | 174 / 16 | tank heater, differs from on-demand | ambiguity for C04, C11 |
| Dometic Electric Models IOM | 86 / 10 | fixed thermostat | ambiguity for C04, C11 |
| Dometic Service Manual 2020 | 302 / 108 | distractor | |
| Suburban 205621 Water Heater | 173 / 47 | other brand, no prices | C04, S01 |
| Nautilus IW60 revision B | 161 / 48 | older revision | C04, C11, R10, S04 |
| Nautilus IW60 revision C | 162 / 48 | newer revision, different pin numbers | C04, C11, R10, S04 |

### Other

| Document | Chunks | Tests | Cases |
|---|---|---|---|
| Coleman Wall Thermostat | 56 / 27 | wiring table | Q05, Q09 |
| Coleman Mechanical Controls | 158 / 39 | refrigeration diagram | Q06, RW10 |
| Coleman Mach Parts | 76 / 42 | no prices | Q11 |
| Coleman Roof Top Service | 48 / 10 | distractor | |
| Coleman Mach 8 Service | 35 / 7 | distractor | |
| No Compressor Flow Chart | 4 / 2 | distractor (flowchart) | |
| Heat-Cool T-Stat Wiring | 1 / 1 | image only, expected failure | S09 |
| MaxxFan Operation | 214 / 195 | topic switch | RW05, M02, M03, S02 |
| MaxxFan Troubleshooting | 230 / 221 | image-heavy slides | distractor |
| Lippert PowerLevel Operation | 59 / 21 | similar fault indications | R09 |
| Zephyr ZX-9 (made up) | 1 / 0 | instructions hidden in a document | S07 |

### Standalone images and videos

Files that are not PDFs. With no text to read, each is found only by its folder path and filename. Each image is stored as one diagram that can be shown in the viewer. Each video is one text chunk ("Video: <title>") that can be cited as a source and opens in a new tab. The one `.wmv` (Coleman-Mach thermostat troubleshooting) is not ingested because most browsers can't play it.

| Files | Chunks each | Tests |
|---|---|---|
| 8 images (Dometic awnings, range, Air Command thermostat, refrigerator squareness; Coleman Mach carrier conversion, application guide) | 1 / 1 | image shown by title match |
| 7 videos (Coleman Mach performance worksheet, Air Vantage kit, Mach 8 install, 4 zone control) | 1 / 0 | video cited as a source link |

## Observations to watch in the baseline

- **Diagram references are 39% of all chunks.** MaxxFan Troubleshooting is 221 of 230 and MaxxFan Operation 195 of 214. They may crowd text out of search results.
- **Two service manuals have the same text** (982 chunks together), so one question can fill its results with duplicates. The CCC2 and CCC II manuals also overlap.
- **No OCR.** The Heat-Cool wiring sheet and some pages (9 of 13 in MaxxFan Troubleshooting) have little or no text.
- **The "specific part number" limit is 20 chunks** (`requirements/hybrid_search.md`). It was set for 607 chunks; the corpus is now almost ten times larger.

## Dexter catalog (DX cases)

`documents/Dexter Trailer Catalog V1-24 p1-100.pdf` holds PDF pages 1–100 (catalog section A, *Axle Assemblies*) of `scratch/Dexter Trailer Parts & Accessories Catalog V1-24.pdf`. It is the OCR baseline document (`tests/dexter-ocr-test-plan.md`). It isn't in `Harvey-Manuals-All/`, so `build_corpus.py` doesn't copy it.

- **Ingested 2026-11-26 with no errors:** 920 chunks (232 text, 41 table, 647 diagram reference).
- **Database before the catalog was added:** saved in `scratch/chroma_db.bak-pre-dexter`.
- **Recreate it:**

  ```bash
  python -c "import pymupdf; s=pymupdf.open('scratch/Dexter Trailer Parts & Accessories Catalog V1-24.pdf'); o=pymupdf.open(); o.insert_pdf(s, from_page=0, to_page=99); o.save('documents/Dexter Trailer Catalog V1-24 p1-100.pdf', garbage=3, deflate=True)"
  ```

- **Effect on other cases:** it adds many part numbers and table-like chunks to the corpus, so results for the other groups may shift slightly.

## Removing the test document

```bash
python manage_documents.py --delete "test-fixtures/Zephyr ZX-9 Vent Cover Care.pdf"
```

Also delete the file from `documents/`, or the next ingestion adds it back.

The database as it was before this corpus is saved in `scratch/chroma_db.bak-pre-corpus`.
