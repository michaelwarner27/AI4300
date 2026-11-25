# Hybrid Search (Dense + Lexical)

This document describes the hybrid retrieval used by the query pipeline: semantic (dense vector) search combined with lexical search (BM25 keyword ranking and exact part-number matching). It replaces dense-only search in both the first retrieval (`vector_store_search`) and the agent's `search_documents` tool.

## Overview

RV repair manuals are full of exact identifiers: model numbers (`457915.70X`, `640312C85X`), part numbers (`8330-733`, `3106481.009`), product names (`CCC2`, `MACH-1`) and fault codes (`E1`–`E9`). Embeddings capture meaning, not exact strings, so dense-only search blurs `457915.70X` with `640312.80X` and can retrieve the wrong unit's manual. Hybrid search keeps the dense ranking and adds:

1. **BM25** over the chunk text, with a tokenizer that keeps part numbers whole
2. **Exact part-number matching** against a `part_numbers` metadata field written at ingestion
3. A **weighted blend** of the scores, a **boost** for chunks that contain a specific part number from the query, and a **guarantee** that such chunks are returned

### Architecture Diagram

```
query text + query embedding (+ optional where filter)
    │
    ├──────────────────────────┬─────────────────────────────┐
    ↓                          ↓                             ↓
┌───────────────────┐  ┌───────────────────┐  ┌────────────────────────────┐
│ Dense (Chroma)    │  │ BM25 (in memory)  │  │ Exact part numbers         │
│ top 30 by cosine  │  │ top 30 by score   │  │ part keys from the query → │
│ no cutoff yet     │  │                   │  │ chunks with that key       │
└─────────┬─────────┘  └─────────┬─────────┘  └──────────────┬─────────────┘
          └──────────────────────┼───────────────────────────┘
                                 ↓
               ┌──────────────────────────────────┐
               │ Union of candidates              │ Missing cosine scores are
               │                                  │ computed from stored embeddings
               └────────────────┬─────────────────┘
                                ↓
               ┌──────────────────────────────────┐
               │ Blend + boost + cutoff           │
               │ sort, top k, part guarantee      │
               └────────────────┬─────────────────┘
                                ↓
               results (query_similarity format + score breakdown)
```

If the lexical index cannot be built, the search falls back to dense-only results (logged as a warning).

Short codes such as fault codes (`E1`, `E5`) are shared by many manuals, so hybrid search alone tends to return the code from the wrong manual. They get an extra **exact-code lookup** inside the retrieved manuals, both automatically and as the agent's `find_code` tool (see [Exact-Code Lookup](#exact-code-lookup-short-codes)).

---

## Part-Number Extraction

**File**: `src/utils/part_numbers.py`. The same functions run over chunk text at ingestion and over the query at search time, so both sides always agree.

**Candidate tokens**: runs of letters/digits joined by `.` or `-` (`/` splits tokens, so `5/16` and `Mfd/370V` become harmless pieces). Tokens are uppercased.

**Kept** when the token contains a digit and:
- it is a pure number (with separators) of at least 5 digits (`457915`, `8330-733`, `3106481.009`), excluding decimals with a short integer part (`63.0`) and equal-length increasing ranges (`561-566`), or
- it mixes letters and digits, is not a number + unit (`12VDC`, `115VAC`, `24V`, `7.5-MFD`), has no word segment like `PIN`, `PC`, `PK`, `EA`, `PDF`, and no letter run longer than 4 (`MANUAL1` rejected; `CCC2`, `MACH-1`, `E1` kept)

**Normalization** (`normalize_part`): uppercase, `.`, `-` and spaces removed, so `457915.70X`, `457915-70x` and `457915 70X` all become `45791570X`.

**Family** (`part_family`): the leading digit run of a model number when it has at least 5 digits and differs from the full key: `457915.70X` → `457915`. A query for "the 457915 manual" then matches chunks naming a specific variant.

**Keys** (`part_keys`): each normalized part number plus its family, in first-seen order.

| Text | Extracted | Keys |
|------|-----------|------|
| `Dometic 457915.70X unit` | `457915.70X` | `45791570X`, `457915` |
| `error code E1 on the ccc2` | `E1`, `CCC2` | `E1`, `CCC2` |
| `Coleman 8330-733, 12VDC, 9-Pin, 5/16` | `8330-733` | `8330733` |
| `best sourdough recipe at 5000 ft` | — | — |

---

## Ingestion: `part_numbers` Metadata

`TextChunker.chunk_all` sets `ChunkMetadata.part_numbers` on every chunk (text, table, diagram_reference):

- `part_keys(chunk.content)`, plus
- `filename_part_keys(filename)`: codes in the manual's filename (`CCC2-Operating-Instructions.pdf` → `CCC2`, `Dometic 457915 ... Manual1.pdf` → `457915`). Filename words are split on `-`, `_` and spaces first. These tag every chunk of the manual, so the manual can always be identified by its model number

Chroma metadata values must be scalars, so the list is stored as one delimited string (`join_keys` / `split_keys`):

```
"part_numbers": "|45791570X|457915|64031280X|640312|"
```

Records ingested before this field existed have no `part_numbers`; they still work for BM25 and dense search but get no exact-match boost. **Re-ingest** to add it.

---

## Lexical Index

**File**: `src/query/lexical.py`

**`tokenize(text)`**: lowercase word tokens (`[a-z0-9]+`) with a small stopword list removed, **plus** each part key as one whole token. A chunk containing `457915.70X` therefore gets the word tokens `457915` and `70x` plus the whole key `45791570x` and the family `457915`, so it matches queries for either the variant or the family.

**`LexicalIndex`** (built from `collection.get(include=["documents", "metadatas"])`):
- `bm25`: `rank_bm25.BM25Okapi` over the tokenized chunks
- `parts`: `dict[key → set[chunk_id]]` from the `part_numbers` metadata
- `bm25_search(query, k, allowed=None)` → `[(chunk_id, score)]` with score > 0, best first
- `exact_part_hits(keys, allowed=None)` → `{chunk_id: [matching keys]}`

`allowed` is the set of chunk ids that match the search's `where` filter (fetched from Chroma with `collection.get(where=...)`), so lexical results obey exactly the same filename / page filters as dense results.

**Caching** (`get_lexical_index`): built once per process (about 0.2 s for 607 chunks) and cached by `(persist_directory, collection_name)`. The cache is rebuilt when `collection.count()` changes, e.g. after re-ingestion. A long-running web server picks up re-ingested documents on the next query when the count differs; restart it if a re-ingest kept the same chunk count.

---

## Scoring

**Function**: `ChromaVectorStore.hybrid_search` (`src/ingestion/tools.py`)

### Candidates
- Top `candidates` (30) dense hits, without a cutoff
- Top `candidates` (30) BM25 hits
- Every chunk containing a **specific** part key from the query

Candidates found only lexically get their cosine similarity computed from their stored embeddings, so every candidate is scored on the same scale.

### Specific vs broad part keys
A query key is **specific** when at most `specific_part_max` (20) chunks contain it, e.g. `45791570X` (4 chunks), `E1` (2), `8330733` (18). A key that tags a whole manual, e.g. `457915` (106 chunks) or `CCC2` (108), is **broad**.

| | Boost | Bypasses cutoff | Guaranteed in results | `match_type` |
|---|---|---|---|---|
| Chunk contains a specific key | `part_boost` (0.3) | yes | yes (best chunk per key) | `exact_part` |
| Chunk contains only broad keys | `part_boost / 4` (0.075) | no | no | `hybrid` / `dense` / `lexical` |
| No key match | 0 | no | no | `hybrid` / `dense` / `lexical` |

### Blended score
```
score = alpha × cosine + (1 − alpha) × bm25 / (bm25 + bm25_saturation) + boost
```
- `alpha` = 0.6, `bm25_saturation` = 10
- BM25 is **saturated**, not divided by the best hit. With max-normalization the best keyword match of an off-topic query would always score 1.0; with saturation a weak match stays weak (sourdough query: BM25 ≈ 5.7 → 0.36; real matches: 25–37 → 0.71–0.79)

### Cutoff
A non-exact candidate is kept when **its cosine OR its blended score** reaches `min_similarity_score` (`SEARCH_MIN_SIMILARITY`, default 0.5). Using the OR means every chunk that dense-only search returned is still returned (no regression for semantic matches with few shared keywords), while strong keyword matches can be added.

### Ordering and guarantee
Results are sorted by blended score and cut to `k`. Then, for each specific part number in the query that no returned chunk contains, its best-scoring chunk is appended (so the result can exceed `k` by the number of such part numbers).

### Returned fields
The same dict format as `query_similarity` (`id`, `content`, `metadata`, `similarity_score`), where `similarity_score` is the blended score, plus:

| Field | Meaning |
|-------|---------|
| `dense_score` | Cosine similarity |
| `bm25_score` | Raw BM25 score (0 if not a BM25 hit) |
| `match_type` | `exact_part`, `hybrid` (dense + BM25), `dense` or `lexical` |
| `matched_parts` | The query's specific part numbers in this chunk, as written in the query |

`_build_query_result` copies these into `QueryResult` (`dense_score`, `bm25_score`, `match_type`, `matched_parts`).

---

## Exact-Code Lookup (Short Codes)

**Why**: a key is "specific" only while it appears in at most 20 chunks. With 49 manuals (5,822 chunks) the fault codes are far past that (`E1` 48 chunks, `E5` 47, `E6` 28), so they count as broad and get no boost. Asking for "E1 on the CCC2" returned ten passages that all contained `E1`, but mostly from a service manual covering many units. Long part numbers are unaffected (`33063`: rank 54 by meaning alone, rank 1 with hybrid search).

**Functions** (`src/query/nodes.py`):

| Function | Purpose |
|---|---|
| `_short_codes(text)` | Codes from `extract_part_numbers` with at most `SHORT_CODE_MAX_CHARS` (3) characters after removing separators: `E1`, `E10`. Not `CCC2` or `457915.70X` |
| `_has_code(text, code)` | Whole-token, case-insensitive match: `E1` matches `e1`, not `E10` or `E1A` |
| `_code_matches(store, code, filenames, embedding)` | Every chunk of the given manuals containing the code, marked `matched_parts=[code]` and `match_type="exact_code"`, best cosine match to the question first |
| `_add_code_matches(...)` | The automatic pass in the first retrieval |
| `_run_find_code(...)` | The `find_code` tool |

**Store**: `ChromaVectorStore.get_chunks_containing(text, where, query_embedding=None)` (`src/ingestion/tools.py`) uses Chroma's `where_document={"$contains": text}`. That match is a case-sensitive substring, so the lookup tries the code as written, upper and lower case, then keeps only whole-token matches. With an embedding, each chunk's cosine similarity to the question is returned for ranking.

### Automatic pass (first retrieval)
When the search query (the rewritten follow-up, if there is one) contains a short code:
1. Retrieved passages containing it are marked with the code (`matched_part` for the LLM).
2. Up to `AUTO_CODE_MATCHES` (5) more passages containing it are added, taken from the manuals of the direct hits only (not neighbors or diagrams), best match to the question first.
3. Failures are logged and the results are used unchanged.

### `find_code` tool
Parameters: `code` (required, 1-24 characters), `filename` (optional; otherwise every manual in context). It returns up to `FIND_CODE_MAX_RESULTS` (10) new passages, best match to the question first, and lists the refs of matches already in context. It is a non-final tool and counts toward the three search rounds. The system prompt tells the agent to call it when the question names a code and no passage for the model asked about contains it.

### Ordering
Documents carrying `matched_part` from this turn (hybrid `exact_part` hits and exact-code matches) are listed **first**, then everything else in reading order (`_context_order_key`). Documents carried over from earlier turns keep reading order. The order is fixed before the LLM sees the list, so refs stay stable.

### Example
"What does error code E5 mean on the CCC2 thermostat?" on the 49-manual corpus: the first passage listed is the CCC2 Operating Instructions error-code page (page 10, added as `exact_code`), and every passage listed before the rest contains `E5`.

---

## Named-Manual Match

**Why**: a model named in words rather than a code is invisible to the part-number matching. "5 button Comfort Control Center reset" retrieved 4-button and CCC2 manuals; the 5-button manuals were about 7th by meaning, and "5" is not a code.

**How** (`_named_manuals`, `_named_manual_passages`, `_mark_named` in `src/query/nodes.py`):
- The stored manuals come from `ChromaVectorStore.source_filenames()` (cached with the lexical index, `LexicalIndex.filenames`). Only the file's own name is used, not its folders.
- A filename word counts when the question contains it, it is not generic (`_NAME_GENERIC`: document types such as "manual", "instructions", "service"; categories such as "water", "heater", "thermostat", "furnace"; common component words such as "electric", "power", "button", "mode"), it is not a bare number, and at most `NAMED_MANUAL_MAX_DF` (4) filenames contain it
- A pair of consecutive filename words counts when the question has the same pair and at most 4 filenames contain it: "5 button", "non ducted". This is how numbers and generic words take part
- Matches are weighted by rarity (log of manuals / manuals containing it, pairs double) to rank manuals. Up to `NAMED_MANUAL_MAX` (2) manuals; a second one needs `NAMED_MANUAL_RELATIVE` (0.6) of the best score
- From each matching manual, the closest text or table chunk of each of its `NAMED_MANUAL_PASSAGES` (5) closest sections is added (from the `NAMED_MANUAL_CANDIDATES` (60) chunks of that manual closest by meaning, no cutoff; a section already represented by a retrieved passage counts as covered). Picking by section stops one long section from crowding out the one that answers: for "how thick can my roof be" the Blizzard "Roof Requirements" passage is about 8th by passage but 4th-5th by section, and the Nautilus "WINTERIZING" section 5th. They and the manual's already-retrieved direct hits get `matched_manual = True`, shown to the LLM as `"named_manual": true`, and are listed first with the `matched_part` documents
- Runs in the first retrieval and in `search_documents` (not when the agent filters by filename). Failures are logged and ignored

| Question | Manuals matched |
|---|---|
| 5 button Comfort Control Center reset | both 5 Button manuals |
| Blizzard roof thickness | Blizzard Ducted and Non-Ducted |
| Non-ducted Blizzard roof thickness | Blizzard Non-Ducted, Air Command Non-Ducted |
| reset the CT thermostat | CT Operating Instructions, CT Reset & Diag |
| How do I reset my thermostat? / winterize my water heater / get my thermostat into diagnostic mode | none (left open for a clarifying question) |

**Limitations**: depends on descriptive filenames (`Roof top service manual (1).pdf` names no model); a filename word that is also an ordinary word in a question can match wrongly, so such words go in `_NAME_GENERIC`.

---

## Pipeline Integration

**File**: `src/query/nodes.py`, via `_hybrid_search(vector_store, query_text, embedding, k, where)`

- **`vector_store_search`** (first retrieval): query text is `retrieval_query or original_query`, the same text that was embedded (a rewritten follow-up when there is one). `k` = `DEFAULT_K` (10). Neighbor and page-diagram expansion run on the hybrid hits unchanged, then the exact-code lookup for short codes and the named-manual match, then documents with `matched_part` or `named_manual` are moved first
- **`search_documents`** (`_run_search`): query text is the LLM's `query`; `k` and the `where` filter (filename, page range) as before

**What the LLM sees**: documents from exact hits and exact-code matches carry `"matched_part": "457915.70X"` (or `"E1"`) and are listed first. The system prompt explains the field and tells the LLM to answer from passages for the exact model / part number asked about, without substituting another model's specifications. The `search_documents` tool description asks the LLM to include exact model, part or error codes in its search query.

**Verbose output** (`query_main.py -v`), per direct hit:
```
    Similarity Score: 0.862864
    Hybrid: exact_part (cosine=0.5398, bm25=14.84, parts=8330-733)
```

---

## Configuration

```bash
# All optional; same names override the constants in src/query/nodes.py
SEARCH_MIN_SIMILARITY=0.5   # cutoff (cosine OR blended score); shared by both searches
HYBRID_ALPHA=0.6            # weight of cosine vs saturated BM25
HYBRID_CANDIDATES=30        # dense and BM25 hits each considered before blending
PART_BOOST=0.3              # boost for chunks containing a specific query part number
```
Invalid values fall back to the default with a warning.

**Tunable Parameters**:
- `HYBRID_ALPHA`, `HYBRID_CANDIDATES`, `PART_BOOST` - `src/query/nodes.py` (env-overridable)
- `bm25_saturation` (10), `specific_part_max` (20) - `hybrid_search` keyword arguments
- `SHORT_CODE_MAX_CHARS` (3), `AUTO_CODE_MATCHES` (5), `FIND_CODE_MAX_RESULTS` (10) - exact-code lookup, `src/query/nodes.py`
- `NAMED_MANUAL_MAX` (2), `NAMED_MANUAL_PASSAGES` (5), `NAMED_MANUAL_CANDIDATES` (60), `NAMED_MANUAL_MAX_DF` (4), `NAMED_MANUAL_RELATIVE` (0.6), `_NAME_GENERIC` - named-manual match, `src/query/nodes.py`
- Extraction rules: `_UNIT`, `_WORD_SEGMENTS`, `_MAX_LETTER_RUN` (4), `_MIN_PURE_DIGITS` (5) - `src/utils/part_numbers.py`
- `_STOPWORDS` - `src/query/lexical.py`

**Dependency**: `rank_bm25>=0.2.2` (`requirements.txt`)

---

## Error Handling

1. **Lexical index build or lexical search fails** → warning logged, dense-only results (`match_type` `dense`), same cutoff
2. **Empty query text** → dense-only results
3. **Chunks without `part_numbers`** (old ingestion) → no exact matches; BM25 and dense still work
4. **Dense search fails** → raised as before (`RuntimeError`); the node reports it like any search failure
5. **Exact-code lookup fails** → warning logged, first-retrieval results used unchanged; a failing `find_code` call is reported to the LLM as the tool result

---

## Results

Live comparison on the current corpus (7 manuals, 607 chunks), dense-only vs hybrid:

| Query | Dense only | Hybrid |
|-------|-----------|--------|
| What is part 8330-733 used for? | Top hits from the wrong manuals (Mach parts list p8, Mechanical controls p25), none containing 8330-733 | All 10 hits contain 8330-733 (Wall Thermostat p16–19, Mach parts list p5–10); correct answer "Wall Thermostat (12 Volt)" |
| Q02: error code E1 on the CCC2 | Second E1 page tied at 0.58 with an unrelated page | Both E1 pages ranked 1st and 2nd (`exact_part`) |
| Q04: Dometic 457915.70X specs | Correct table on top | Same table on top; answer now cites the exact `457915.70X` table row |
| Q03, Q07, Q11 | Correct | Unchanged |
| Q12: sourdough (off-topic) | No results | No results |

Full run: `tests/results/query_results_003.txt` (12/12 completed) vs baseline `tests/results/query_results_HW11.txt`.

---

## Testing

**Offline** (`python tests/verify_query_loop.py`, no services needed):
- `test_part_numbers` - kept / dropped tokens, lowercase queries, normalization, families, filename keys, storage format
- `test_hybrid_search` (real temporary Chroma database) - exact-part chunk below the cutoff ranks first, other model still returned, unrelated chunk dropped, off-topic query empty, `where` filter applies to lexical hits, broad keys give no bypass, guarantee beyond `k`, fallback when the index fails
- `test_hybrid_wiring` - query text (and rewritten follow-up) passed, default and env-overridden settings, `QueryResult` breakdown, `matched_part` only on exact hits, prompt mentions it
- `FakeStore.hybrid_search` delegates to `query_similarity`, so the agent-loop tests exercise the hybrid call path
- `test_named_manuals` - filename matching (pairs, shared names, generic words, folders ignored, limit), passages added and marked, filters, ordering, failures, `search_documents` wiring; also the manuals list and the clarify guard
- `test_find_code` - short-code detection, whole-token matching, `get_chunks_containing` and `_code_matches` on a real temporary database (filter, case, ranking), the automatic pass (marking, adding, scope, ordering, failure), wiring with the rewritten query, history ordering, and the `find_code` tool through the agent loop (new vs already-in-context matches, no match, bad input)

**End-to-end** (`python tests/test_queries.py`, live services): Q02 (E1), Q03 (command 10), Q04 (457915.70X), Q07 (457915) and Q11 (capacitor part numbers) depend on exact tokens; Q10 and Q12 must still decline. R11 (E5 on the CCC2), RW07, M03 and S04 cover short codes; all passed after the exact-code lookup was added (`tests/results/query_results_008.txt`).

**Manual**: `python query_main.py "What is part 8330-733 used for?" -v` should show `exact_part` hits containing 8330-733.

---

## Known Limitations

- Some non-part tokens are extracted: phone numbers (`574-294-2511`), postal codes (`N3T`, `5L8`) and OCR fragments (`3OR4`, `7.E`). They only matter when a query contains the same token
- `specific_part_max` is absolute (20 chunks). With a much larger corpus a common part number may become "broad"; consider a fraction of the corpus instead. Short codes are covered by the exact-code lookup
- The exact-code lookup only searches manuals that hybrid search already returned (or one the agent names); if the right manual is not among them, it cannot help
- Within the documents listed first, the order is reading order (by filename), not relevance
- Hyphenated line breaks in the PDF text (`op-\neration`, TODO #5) also split words for BM25
- The exact boost (0.3) cannot overcome a very large cosine gap (e.g. 0.3 vs 1.0); in practice gaps between same-topic chunks are small (0.52 vs 0.61 for 8330-733)
- The index is in memory and rebuilt per process; fine for hundreds or thousands of chunks, not for millions

---

## Files

| File | Purpose |
|------|---------|
| `src/utils/part_numbers.py` | Part-number extraction, normalization, family, filename keys, storage format |
| `src/query/lexical.py` | `tokenize`, `LexicalIndex` (BM25 + part index + list of stored filenames), cached `get_lexical_index` |
| `src/ingestion/tools.py` | `TextChunker.chunk_all` (sets `part_numbers`), `store_chunks` (stores it), `ChromaVectorStore.hybrid_search`, `get_chunks_containing`, `_cosine` |
| `src/data_models.py` | `ChunkMetadata.part_numbers`; `QueryResult.dense_score`, `bm25_score`, `match_type`, `matched_parts` |
| `src/query/nodes.py` | `HYBRID_*` / `PART_BOOST` settings, `_hybrid_search`, callers, `matched_part` for the LLM, prompt lines; exact-code lookup (`_short_codes`, `_code_matches`, `_add_code_matches`, `_run_find_code`, `find_code` tool, `_context_order_key`) |
| `query_main.py` | Verbose score breakdown |
| `tests/verify_query_loop.py` | Offline tests (see Testing) |
