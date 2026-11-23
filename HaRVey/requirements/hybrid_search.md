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

## Pipeline Integration

**File**: `src/query/nodes.py`, via `_hybrid_search(vector_store, query_text, embedding, k, where)`

- **`vector_store_search`** (first retrieval): query text is `retrieval_query or original_query`, the same text that was embedded (a rewritten follow-up when there is one). `k` = `DEFAULT_K` (10). Neighbor and page-diagram expansion run on the hybrid hits unchanged
- **`search_documents`** (`_run_search`): query text is the LLM's `query`; `k` and the `where` filter (filename, page range) as before

**What the LLM sees**: documents from exact hits carry `"matched_part": "457915.70X"`. The system prompt explains the field and tells the LLM to answer from passages for the exact model / part number asked about, without substituting another model's specifications. The `search_documents` tool description asks the LLM to include exact model, part or error codes in its search query.

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
- Extraction rules: `_UNIT`, `_WORD_SEGMENTS`, `_MAX_LETTER_RUN` (4), `_MIN_PURE_DIGITS` (5) - `src/utils/part_numbers.py`
- `_STOPWORDS` - `src/query/lexical.py`

**Dependency**: `rank_bm25>=0.2.2` (`requirements.txt`)

---

## Error Handling

1. **Lexical index build or lexical search fails** → warning logged, dense-only results (`match_type` `dense`), same cutoff
2. **Empty query text** → dense-only results
3. **Chunks without `part_numbers`** (old ingestion) → no exact matches; BM25 and dense still work
4. **Dense search fails** → raised as before (`RuntimeError`); the node reports it like any search failure

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

**End-to-end** (`python -m tests.test_queries`, live services): Q02 (E1), Q03 (command 10), Q04 (457915.70X), Q07 (457915) and Q11 (capacitor part numbers) depend on exact tokens; Q10 and Q12 must still decline.

**Manual**: `python query_main.py "What is part 8330-733 used for?" -v` should show `exact_part` hits containing 8330-733.

---

## Known Limitations

- Some non-part tokens are extracted: phone numbers (`574-294-2511`), postal codes (`N3T`, `5L8`) and OCR fragments (`3OR4`, `7.E`). They only matter when a query contains the same token
- `specific_part_max` is absolute (20 chunks). With a much larger corpus a common part number may become "broad"; consider a fraction of the corpus instead
- Hyphenated line breaks in the PDF text (`op-\neration`, TODO #5) also split words for BM25
- The exact boost (0.3) cannot overcome a very large cosine gap (e.g. 0.3 vs 1.0); in practice gaps between same-topic chunks are small (0.52 vs 0.61 for 8330-733)
- The index is in memory and rebuilt per process; fine for hundreds or thousands of chunks, not for millions

---

## Files

| File | Purpose |
|------|---------|
| `src/utils/part_numbers.py` | Part-number extraction, normalization, family, filename keys, storage format |
| `src/query/lexical.py` | `tokenize`, `LexicalIndex` (BM25 + part index), cached `get_lexical_index` |
| `src/ingestion/tools.py` | `TextChunker.chunk_all` (sets `part_numbers`), `store_chunks` (stores it), `ChromaVectorStore.hybrid_search`, `_cosine` |
| `src/data_models.py` | `ChunkMetadata.part_numbers`; `QueryResult.dense_score`, `bm25_score`, `match_type`, `matched_parts` |
| `src/query/nodes.py` | `HYBRID_*` / `PART_BOOST` settings, `_hybrid_search`, callers, `matched_part` for the LLM, prompt line |
| `query_main.py` | Verbose score breakdown |
| `tests/verify_query_loop.py` | Offline tests (see Testing) |
