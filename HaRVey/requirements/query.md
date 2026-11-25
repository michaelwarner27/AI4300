# Vector Query Pipeline

This document describes the LangGraph-based query pipeline that retrieves relevant passages from the vector database with hybrid (semantic + keyword + exact part-number) search and generates LLM-powered answers with source attribution. It supports multi-turn conversations, an agent loop that can search and read more of the documents, section-aware context, and diagram display.

## Overview

The query pipeline accepts a user question (optionally with earlier turns of the conversation), rewrites follow-ups into standalone search queries, embeds the query, searches the vector database (dense similarity blended with BM25 and exact part-number matching, see [hybrid_search.md](hybrid_search.md)), and lets an LLM answer with tool calls. The LLM can run further searches, read whole sections or pages, and finally answers with cited sources, optional step-by-step instructions and optional diagrams, or reports that the documents do not cover the question.

Every passage carries the **section** it came from (its heading path in the source document, e.g. `MAINTENANCE > A. Air Filter`), set at ingestion. The LLM sees it, can read a whole section with `read_section`, and citations show it.

### Architecture Diagram

```
User Query (+ chat history)
    ↓
┌─────────────────────────┐
│  user_query             │ Validate input
└─────────────┬───────────┘
              ↓
┌─────────────────────────┐
│  rewrite_query          │ Follow-up → standalone search query
│                         │ (no-op without chat history)
└─────────────┬───────────┘
              ↓
┌─────────────────────────┐
│  embed_query            │ Generate embedding vector
└─────────────┬───────────┘
              ↓
┌─────────────────────────┐
│  vector_store_search    │ Hybrid search: dense + BM25 +
│                         │ exact part numbers (top 10)
│                         │ + same-section neighbor chunks
│                         │ + exact-code lookup (short codes)
└─────────────┬───────────┘
              ↓
┌─────────────────────────┐
│  add_history_documents  │ Carry over documents cited
│                         │ in recent turns
└─────────────┬───────────┘
              ↓
        [Results Found?]
        /              \
      YES               NO
      ↓                 ↓
┌──────────────┐    [End: no_results_found]
│generate_     │◄──────────┐
│response      │           │
│(LLM + tools) │     ┌─────┴────────┐
└──────┬───────┘     │ search_more  │ search_documents /
       │ non-final   │ (max 3 rounds│ read_section /
       ├────────────►│  or 300 s)   │ read_page / find_code
       │ final answer└──────────────┘
       ↓
┌──────────────┐
│process_      │ Format answer, citations, diagrams;
│tool_call     │ record cited documents
└──────┬───────┘
       ↓
  Final Response (Output)
```

`generate_response` is one agent turn. If the LLM calls `search_documents`, `read_section`, `read_page` or `find_code`, `search_more` runs it, adds the results to the conversation and returns to `generate_response`. A final answer (`respond_to_user` / `insufficient_evidence`) goes to `process_tool_call`.

---

## Nodes

### 1. user_query

**Purpose**: Accept and validate user input queries.

**Inputs**: User query string

**Outputs**: Validated query string

**Responsibilities**:
- Validate the query is a non-empty string (whitespace-only is rejected)
- Strip surrounding whitespace
- Set `query_validated` flag in state
- Handle empty or invalid input gracefully

**Behavior**:
- Validation failures trigger `query_validation_failed` status
- Valid queries proceed to `rewrite_query`

---

### 2. rewrite_query

**Purpose**: Turn a follow-up question into a standalone search query, so retrieval finds what "it", "that diagram" or "step 3" refers to.

**Inputs**: Validated query + `chat_history`

**Outputs**: `retrieval_query`

**Responsibilities**:
- With no chat history, use the query as-is (no LLM call)
- Otherwise make one LLM call (`REWRITE_PROMPT`, temperature 0) with the last `MAX_HISTORY_TURNS` (5) turns. An earlier answer longer than `REWRITE_ANSWER_CHARS` (1500) keeps its first 1000 and last `REWRITE_ANSWER_TAIL_CHARS` (500) characters, so "the last one in that list" still resolves. Clarifying questions are labelled `Assistant (clarifying question)` and include their options. When older turns were dropped, a note says how many are not shown
- `REWRITE_PROMPT` rules: return standalone messages unchanged; after a clarifying question, combine the original question with the user's reply, keeping the user's distinguishing words (e.g. "5 button") even when they were not among the options, and treating "the second one" as the second option; apply corrections and drop the old model; don't carry the old model across a topic change; keep exact codes; don't add models the conversation doesn't mention; leave references to turns that are not shown unchanged
- Clean the reply: remove `<think>` blocks, a `Query:` prefix and surrounding quotes
- Fall back to the original query on any error, an empty reply, or a reply longer than `REWRITE_MAX_QUERY_CHARS` (500)

**Behavior**:
- The rewrite only affects retrieval (embedding). The LLM still answers the user's own words
- Sets status `query_rewritten` when a rewrite is used

---

### 3. embed_query

**Purpose**: Convert the retrieval query into a vector embedding.

**Inputs**: `retrieval_query` (falls back to the validated query)

**Outputs**: Vector embedding (array of floats)

**Responsibilities**:
- Call embedding service API with the query text
- Handle API errors (connection failures, authentication, timeouts)
- Validate response format and vector dimensions
- Propagate errors to next nodes

**Configuration**:
- Service URL: `EMBEDDING_BASE_URL`
- Model: `EMBEDDING_MODEL` (e.g., Qwen3-Embedding-8B)
- Authentication: API key from `EMBEDDING_API_KEY`
- Timeout: 30 seconds

**Key Functions**:
- Reuses `EmbeddingService` from the ingestion pipeline
- Single query embedding (not batched)
- Errors stored in state for diagnostics

---

### 4. vector_store_search

**Purpose**: Retrieve the most relevant passages with hybrid search: semantic similarity combined with keyword (BM25) and exact part-number matching.

**Inputs**: Query embedding vector + query text (`retrieval_query`, falling back to the original query: the same text that was embedded)

**Outputs**: Top K results (plus neighbor chunks) with metadata, blended scores and a score breakdown

**Responsibilities**:
- Connect to the Chroma collection
- Run `ChromaVectorStore.hybrid_search` for the top K chunks (`DEFAULT_K` = 10). Full details in [hybrid_search.md](hybrid_search.md):
  - Candidates: top 30 dense hits, top 30 BM25 hits, and every chunk containing a specific part number from the query (`457915.70X`, `8330-733`, `E1`)
  - Score: `HYBRID_ALPHA` (0.6) × cosine + 0.4 × saturated BM25, + `PART_BOOST` (0.3) for chunks containing a specific query part number
  - Chunks with a specific query part number always pass the cutoff and at least one per part number is returned
  - Falls back to dense-only results if the lexical index can't be built
- Cutoff `SEARCH_MIN_SIMILARITY` from `.env` (default `DEFAULT_MIN_SIMILARITY` = 0.5; the same cutoff is used by `search_documents`): a chunk is kept when its cosine **or** its blended score reaches it, so everything dense-only search returned is still returned. Relevant chunks score about 0.55 and up on cosine, off-topic queries about 0.4 at best
- Read each result's metadata: filename, page, chunk sequence, character range, and (with defaults, so older records still load) `chunk_type`, `diagram_kind`, `diagram_path`, `section_path` and `part_numbers`. `section_path` (`"A > B"`) is split back into `ChunkMetadata.section_headings`; the hybrid breakdown goes to `QueryResult.dense_score`, `bm25_score`, `match_type` and `matched_parts`
- Add neighbor chunks (see below)
- **Exact-code lookup**: when the search query contains a short code (3 characters or fewer, e.g. `E1`), mark retrieved passages that contain it and add up to 5 more passages containing it from the manuals of the direct hits, best match to the question first (`match_type` `exact_code`; see [hybrid_search.md](hybrid_search.md#exact-code-lookup-short-codes)). A failure is logged and the results are used unchanged
- Order the results: this turn's passages carrying `matched_part` (exact part-number hits and exact-code matches) first, then reading order
- Handle the no-results case gracefully (status: `no_results_found`)
- Handle database connection errors

**Neighbor context**: each of the top 5 text hits (`NEIGHBOR_TOP_HITS`) also brings the chunks immediately before and after it (`chunk_sequence` ±1, `NEIGHBOR_RADIUS`), **only if they are in the same section as the hit**. A heading is often in a different chunk than the text it introduces, so a hit alone can be misleading. Hits from records without a section accept any neighbor. A retrieval returns at most 10 matches + 10 neighbors (plus page diagrams and up to 5 exact-code matches), with neighbors flagged `"neighbor": true`.

**Configuration**:
- Persist directory: `CHROMA_PERSIST_DIRECTORY`
- Collection name: `CHROMA_COLLECTION_NAME`
- K: 10, min similarity: `SEARCH_MIN_SIMILARITY` (default 0.5)
- Hybrid weights: `HYBRID_ALPHA` (0.6), `HYBRID_CANDIDATES` (30), `PART_BOOST` (0.3)

---

### 5. add_history_documents

**Purpose**: Keep the documents behind recent answers available for follow-up questions.

**Inputs**: `search_results` + `chat_history`

**Outputs**: `search_results` with carried-over documents

**Responsibilities**:
- Copy the documents each of the last `HISTORY_DOC_TURNS` (2) turns **cited** (`ChatTurn.documents`, see `process_tool_call`), at most `MAX_HISTORY_DOCS` (30), most recent first
- Skip chunks this turn already retrieved; flag the rest `from_history` (shown to the LLM as `"earlier_turn": true`)
- Put the combined list in order (this turn's `matched_part` documents first, then reading order; carried-over documents are never moved first), so every document has one `ref`
- If this turn's search found nothing, the carried-over documents alone still allow an answer (status `history_documents_only`)

---

### 6. generate_response

**Purpose**: Run one agent turn: call the LLM with the documents and tools.

**Inputs**: Original query, documents, chat history, agent conversation so far

**Outputs**: One tool call

**Responsibilities**:
- First turn: build the conversation as system prompt → last 5 earlier turns (user question / assistant answer pairs) → current question with all documents as JSON
- Later turns: continue from `state.messages`
- Offer the non-final tools only while searching is allowed (see Limits); otherwise offer only the three final tools and tell the LLM to answer
- Up to `MAX_TOOL_CALL_RETRIES` (2) attempts per turn: a reply without a tool call, or a tool call the server can't parse, is retried once with a reminder at `RETRY_LLM_TEMPERATURE` (0.9). If both attempts fail, a plain-text reply is used as the answer; otherwise `process_tool_call` returns the fallback response

**What the LLM sees per document**:
```json
{
  "ref": 3,
  "filename": "Dometic 457915 Installation & Operating Instructions Manual1.pdf",
  "page": 16,
  "chunk_type": "text",
  "section": "MAINTENANCE > A. Air Filter",
  "text": "A. Air Filter\n1. Periodically (a minimum of every 2 weeks of op-\neration) remove the return air filter ...",
  "neighbor": true
}
```
- `ref`: stable number used for `diagram_refs`; it never changes during a query
- `section`: present when the chunk has one. The duplicate `Section: ...` line that ingestion puts in the chunk text is removed from `text`
- `neighbor`: `true` for neighbor chunks and passages from `read_section` / `read_page`
- `earlier_turn`: `true` for documents carried over from earlier turns
- `matched_part`: present when the passage contains a specific model / part number from the search query (e.g. `"457915.70X"`) or was found by the exact-code lookup (e.g. `"E1"`). These documents are listed first
- `diagram_kind`: only on `diagram_reference` chunks (no `diagram_path`: the LLM picks diagrams by ref)
- Scores are not sent to the LLM

**System prompt behavior**:
- Answer only from the documents; prefer `respond_to_user` when they are relevant
- Use earlier turns to understand follow-ups, but answer from documents, not earlier answers
- Use `page` (the PDF page, which can differ from the printed page) for citations and filters
- Use `section` to tell which procedure, component or model a passage belongs to; don't mix steps from different sections unless the question needs both
- When the question names a specific model or part number, answer from passages for that exact number (`matched_part`) and don't substitute another model's specifications
- Give procedures as `steps`, one action each, in order, with citations
- When context is incomplete (missing steps, "see section X", a heading without its content): call `read_section` for a section whose start was found, `read_page` when a document has no section, otherwise `search_documents`; never repeat the same call
- When the question names a code and no passage for the model asked about contains it: call `find_code`
- Select diagrams by `ref` only; never invent refs or file paths

**Configuration**:
- Service URL: `LLM_BASE_URL`
- Model: `LLM_MODEL` (e.g., Qwen3.8-Flash-Next)
- Authentication: API key from `LLM_API_KEY`
- Reasoning effort: `LLM_REASONING_EFFORT` (default `low`)
- Max tokens: 50000 (`DEFAULT_MAX_TOKENS`, room for reasoning models)
- Temperature: 0.7 (`DEFAULT_LLM_TEMPERATURE`); retries 0.9 (`RETRY_LLM_TEMPERATURE`)
- Timeout: 120 seconds per call

**Tool Calls**:

The LLM makes one tool call per turn. `search_documents`, `read_section`, `read_page` and `find_code` are non-final (the loop continues); `respond_to_user` and `insufficient_evidence` end the turn.

**Tool 1: respond_to_user** (final)
```json
{
  "name": "respond_to_user",
  "answer": "Synthesized answer text",
  "sources": [
    {"filename": "doc.pdf", "page": 16, "section": "MAINTENANCE > A. Air Filter", "snippet": "..."}
  ],
  "include_sources": true,
  "steps": [
    {"instruction": "Remove the return air filter", "source_filename": "doc.pdf", "page": 16,
     "section": "MAINTENANCE > A. Air Filter"}
  ],
  "diagram_refs": [3, 7]
}
```
Only `answer` is required.
- `steps`: ordered instructions for procedural questions, rendered as a numbered `Steps:` list between the answer and the sources
- `section` (in sources and steps): optional, copied from the document. It is only shown if it matches a retrieved document (see Citations)
- `diagram_refs`: `ref` values of retrieved `diagram_reference` documents whose images should be shown

**Tool 2: search_documents** (non-final)
```json
{
  "name": "search_documents",
  "query": "mounting bracket installation",
  "filename": "doc.pdf",
  "page_start": 4,
  "page_end": 8,
  "k": 5
}
```
Only `query` is required. `k` defaults to 5 (max 10). The tool description asks the LLM to include any exact model, part or error code in `query`, since the search also matches them exactly.

**Tool 3: read_section** (non-final)
```json
{
  "name": "read_section",
  "filename": "doc.pdf",
  "section": "MAINTENANCE > A. Air Filter"
}
```
Both fields are required. `section` can be a full section path, or just a heading (`"Air Filter"`, `"maintenance"`): case and leading labels like `A.` or `1.` are ignored. A parent section includes its sub-sections.

**Tool 4: read_page** (non-final)
```json
{
  "name": "read_page",
  "filename": "doc.pdf",
  "page_start": 16,
  "page_end": 17
}
```
`page_start` and `filename` are required; `page_end` defaults to `page_start` and is clamped to 3 pages. Pages are PDF page numbers (the `page` shown in documents).

**Tool 5: find_code** (non-final)
```json
{
  "name": "find_code",
  "code": "E1",
  "filename": "CCC2-Operating-Instructions.pdf"
}
```
Only `code` is required (1-24 characters). Finds passages containing the exact code as a whole token, any case (`E1` matches `e1`, not `E10`), in every manual already in context or only in `filename`. Returns up to 10 new passages, best match to the question first, marked `matched_part`. Meant for short codes such as error codes that many manuals share.

**Tool 6: insufficient_evidence** (final)
```json
{
  "name": "insufficient_evidence",
  "explanation": "Why documents don't address query",
  "query": "Original user query"
}
```

---

### 6b. search_more (agent loop)

**Purpose**: Execute the LLM's `search_documents`, `read_section`, `read_page` or `find_code` call and feed the new context back.

**Inputs**: The non-final tool call

**Outputs**: Updated `search_results` and `messages`; control returns to `generate_response`

**Responsibilities**:
- `search_documents`: embed the search text and run the same hybrid search as the first retrieval (dense + BM25 + exact part numbers, see [hybrid_search.md](hybrid_search.md)) with an optional metadata filter (`source_filename`, `page_number` range). The filter applies to the lexical hits too. The cutoff is `SEARCH_MIN_SIMILARITY` (default 0.5), the same one the first retrieval uses
- `read_section`: load the document's chunks and return every chunk whose section is the requested one or below it, in reading order (text, then the section's tables and diagrams), at most `READ_SECTION_MAX_CHUNKS` (25)
  - No match: the reply lists the document's sections (up to `READ_SECTION_LIST_LIMIT`, 60) so the LLM can pick one
  - Several matches (e.g. two `C. Condensation` sections under different parents): the reply lists them and asks for the full path
  - Longer sections are cut at 25 passages and the reply names the sub-sections not shown
  - Documents ingested without sections: the reply says to use `read_page`
- `read_page`: fetch every chunk on a page range (max 3 pages) by metadata only, in reading order
- `find_code`: Chroma substring search (`ChromaVectorStore.get_chunks_containing`) over the manuals in context (or one named manual), kept only when the code is a whole token, ranked by similarity to the question's embedding, at most `FIND_CODE_MAX_RESULTS` (10); matches already in context are listed by ref
- Filenames are matched leniently (exact, or a unique substring of a filename in context; omitted when only one document is in context)
- Skip chunks already in context (key: filename + chunk sequence); `read_section` and `read_page` list their refs instead of repeating them
- Append new chunks to `search_results`; existing `ref` numbers never shift, new chunks continue the numbering (so `diagram_refs` stay valid)
- Add the result as a `tool` message (full chunk text, in the same JSON format)
- Report failures, empty results and bad parameters to the LLM as the tool result instead of aborting

**Limits**: at most `MAX_SEARCH_ROUNDS` (3) non-final calls in total (all four tools share the budget) and `AGENT_DEADLINE_SECONDS` (300 s) since the first LLM turn. After either limit the non-final tools are no longer offered and the LLM is told to answer.

---

### 7. process_tool_call

**Purpose**: Turn the LLM's final tool call into the response text and record which documents it used.

**Inputs**: Tool call from `generate_response`

**Outputs**: `final_response` string and `source_documents`

**Responsibilities**:
- Route to the handler for the tool name; unknown or missing tool calls get the fallback response
- Format citations with sections (see Citations)
- Record `source_documents`: the retrieved chunks the answer cited, i.e. every chunk on a cited source's page (matching filename), plus each diagram that is shown. These, not all retrieved chunks, are what a caller stores in `ChatTurn.documents` and what `add_history_documents` carries into later turns

**Handler: respond_to_user**
- Answer text, then `Steps:` (if any), then `---` and `Sources:` (unless `include_sources=false`), then `Diagrams:` (if any)
- Step: `N. instruction (file, p.N, SECTION)`
- Source: `- file (page N, SECTION): snippet`
- Diagram: `- file (page N, kind, SECTION): /absolute/path/to/image`. Refs that are out of range, not `diagram_reference` chunks, or whose image is missing are dropped. Paths are resolved against the project root (falling back to `extracted_diagrams/<filename>`), so they work from any working directory. The block is shown even when `include_sources=false`
- Snippets are kept exactly as the LLM gave them

**Citations (section)**: the section in a citation always comes from the retrieved documents, never from the LLM alone:
1. The LLM's `section`, if it matches a retrieved document's section (preferring one on the cited page)
2. Otherwise the section of the retrieved document on that page whose text contains the snippet
3. Otherwise the only section among retrieved documents on that page

If none applies (e.g. a page with several sections and no usable snippet), the citation shows filename and page only.

**Handler: insufficient_evidence**
- Explanation, followed by the single most relevant retrieved document: the highest-scoring direct hit (neighbor and section/page-read chunks excluded), with its section and a 150-character snippet (without the `Section:` line)

---

## Output Examples

### Answer with steps, sources and a diagram (`query_main.py`, illustrative)

The passage text is real ingestion output for Dometic page 16; the LLM's wording is illustrative.

```
================================================================================
QUERY PIPELINE RESULT
================================================================================

Status: response_generated
Original Query: How do I clean the air filter on the Dometic unit?
Query Validated: True
Query Embedding: Generated (4096 dimensions)

================================================================================
LLM RESPONSE
================================================================================
Clean the return air filter at least every 2 weeks of operation. Never run the unit without the filter in place: dirt will plug the evaporator coil and reduce performance.

Steps:
1. Remove the return air filter located behind the return air grille (Dometic 457915 Installation & Operating Instructions Manual1.pdf, p.16, MAINTENANCE > A. Air Filter)
2. Wash it with soap and warm water (Dometic 457915 Installation & Operating Instructions Manual1.pdf, p.16, MAINTENANCE > A. Air Filter)
3. Let it dry, then reinstall it (Dometic 457915 Installation & Operating Instructions Manual1.pdf, p.16, MAINTENANCE > A. Air Filter)

---

Sources:
- Dometic 457915 Installation & Operating Instructions Manual1.pdf (page 16, MAINTENANCE > A. Air Filter): Periodically (a minimum of every 2 weeks of operation) remove the return air filter...

Diagrams:
- Dometic 457915 Installation & Operating Instructions Manual1.pdf (page 14, vector_figure, INSTALLATION INSTRUCTIONS > G. Installing Air Distribution Box): /home/.../extracted_diagrams/Dometic 457915 Installation & Operating Instructions Manual1_p13_fig3.png

================================================================================
NO ERRORS ENCOUNTERED
================================================================================
```

### Insufficient evidence (illustrative)

```
⚠️  Insufficient Information

The documents do not describe how to recharge the refrigerant.

However, here is the most relevant document that was retrieved:

  Source: Dometic 457915 Installation & Operating Instructions Manual1.pdf (page 7, SPECIFICATIONS > A. Table - Unit Data)
  Snippet: A. Table - Unit Data ...
```

### No results

`status` is `no_results_found` and there is no `final_response`. `query_main.py` prints the status without an LLM response; the web UI shows "No relevant documents found."

### Fallback (LLM tool call failed)

```
⚠️  Tool call generation failed

Errors encountered:
  - LLM tool calling failed after 1 attempts: <error>
```

---

## State Management

The pipeline uses a central `QueryState` object that flows through all nodes:

```python
@dataclass
class QueryState:
    original_query: str | None             # User's question
    query_validated: bool                  # Validation flag
    query_embedding: list[float] | None    # Query vector
    search_results: list[QueryResult]      # Retrieved documents (refs = list positions)
    tool_call: ToolCall | None             # Latest LLM tool call
    final_response: str | None             # Formatted response
    errors: list[str]                      # Error log
    config: IngestionConfig | None         # Configuration reference
    status: str                            # Pipeline status
    messages: list[dict]                   # LLM conversation (agent loop)
    search_round: int                      # Non-final tool calls executed
    started_at: float | None               # Start time of the agent loop
    chat_history: list[ChatTurn]           # Earlier turns, oldest first
    retrieval_query: str | None            # Standalone query used for embedding
    source_documents: list[QueryResult]    # Chunks the final answer cited

@dataclass
class QueryResult:
    content: str                           # Chunk text (includes the "Section:" line)
    metadata: ChunkMetadata                # section_headings, chunk_type, diagram_*, part_numbers, page, ...
    similarity_score: float                # Blended hybrid score for search hits
    is_neighbor: bool                      # Neighbor / section or page read, not scored
    from_history: bool                     # Carried over from an earlier turn
    dense_score: float | None              # Cosine similarity (hybrid hits only)
    bm25_score: float                      # Raw BM25 score
    match_type: str                        # "exact_part" | "hybrid" | "dense" | "lexical" ("" if not a search hit)
    matched_parts: list[str]               # Query part numbers this chunk contains

@dataclass
class ChatTurn:
    question: str                          # The user's message as typed
    answer: str                            # The final formatted response
    documents: list[QueryResult]           # source_documents of that turn
```

**Status Values**:
- `query_validated` - Query passed validation
- `query_rewritten` - A follow-up was rewritten for retrieval
- `query_embedded` - Embedding generated
- `search_completed` - Vector search completed
- `history_documents_only` - This turn found nothing, but carried-over documents exist
- `tool_call_received` - LLM returned a tool call
- `search_more_completed` - A search / section read / page read round finished
- `response_generated` - Answer formatted
- `insufficient_evidence_returned` - LLM reported insufficient evidence
- `response_generated_with_errors` - Fallback response after an error
- `no_results_found` - No documents matched the query (LLM skipped)
- `*_failed` - Error occurred at a specific stage

---

## Conversation History

`execute_query_pipeline(query, config, graph, chat_history=...)` takes earlier turns, oldest first. After each answered turn the caller appends:

```python
ChatTurn(question=user_message, answer=result.final_response, documents=list(result.source_documents))
```

- The last `MAX_HISTORY_TURNS` (5) turns are sent to the LLM as user/assistant messages and used by `rewrite_query`. When older turns were dropped, both the rewrite and the current question start with a note saying how many are not shown; the system prompt tells the LLM to reply with `insufficient_evidence` when asked about those turns instead of guessing
- Documents cited in the last `HISTORY_DOC_TURNS` (2) turns are carried into the next turn's context
- The web UI keeps history per browser session (in server memory, lost on restart). `query_main.py` treats each query separately

---

## Error Handling Pattern

The pipeline accumulates errors without stopping:

1. **Query Validation Failure** → Stop, return `query_validation_failed`
2. **Rewrite Failure** → Use the original query (not an error)
3. **Embedding Failure** → Log error and propagate; no search is possible
4. **Search Failure** → Log error; the LLM is skipped when there are no documents. A failure of only the lexical part of hybrid search falls back to dense-only results (warning, not an error)
5. **No Results Found** → Not an error; `no_results_found`, LLM skipped (unless carried-over documents exist)
6. **Agent Tool Failure** (search / section read / page read) → Reported to the LLM as the tool result; the loop continues
7. **LLM Tool Call Failure** → Fallback response after `MAX_TOOL_CALL_RETRIES` (2) attempts (a plain-text reply is used as the answer instead, if there is one)
8. **Unknown Tool Call** → Fallback response with the error

---

## Configuration

All pipeline behavior is controlled via environment variables in `.env`:

```bash
# Embedding Service
EMBEDDING_BASE_URL=http://golem:4000/v1
EMBEDDING_MODEL=Qwen3-Embedding-8B
EMBEDDING_API_KEY=your_api_key

# LLM Service
LLM_BASE_URL=http://golem:4000/v1
LLM_MODEL=Qwen3.8-Flash-Next
LLM_API_KEY=your_api_key
LLM_REASONING_EFFORT=low                  # optional (default: low)

# Vector Database
CHROMA_PERSIST_DIRECTORY=./chroma_db
CHROMA_COLLECTION_NAME=vector_ingestion_collection

# Similarity cutoff (optional)
SEARCH_MIN_SIMILARITY=0.5                 # first retrieval AND search_documents (default 0.5)

# Hybrid search (optional, see hybrid_search.md)
HYBRID_ALPHA=0.6                          # weight of cosine vs saturated BM25
HYBRID_CANDIDATES=30                      # dense and BM25 hits each considered before blending
PART_BOOST=0.3                            # boost for chunks containing a specific query part number

# Web UI (optional)
FLASK_SECRET_KEY=...                      # random per start if unset

# Document Input (for ingestion pipeline)
DOCUMENT_INPUT_DIR=./documents
```

**Tunable Parameters** (in `src/query/nodes.py`):
- `DEFAULT_K` - First retrieval: number of results (default: 10)
- `DEFAULT_MIN_SIMILARITY` - Score cutoff for the first retrieval and `search_documents` when `SEARCH_MIN_SIMILARITY` is unset (default: 0.5)
- `HYBRID_ALPHA`, `HYBRID_CANDIDATES`, `PART_BOOST` - Hybrid search blend weight, candidates per leg, exact part-number boost (default: 0.6, 30, 0.3; env-overridable)
- `NEIGHBOR_RADIUS`, `NEIGHBOR_TOP_HITS` - Neighbor chunks per hit and how many top hits get them (default: 1, 5)
- `MAX_SEARCH_ROUNDS`, `AGENT_DEADLINE_SECONDS` - Agent loop limits (default: 3, 300 s)
- `SEARCH_DEFAULT_K`, `SEARCH_MAX_K` - Results per `search_documents` call (default: 5, 10)
- `READ_PAGE_MAX_PAGES` - Pages per `read_page` call (default: 3)
- `READ_SECTION_MAX_CHUNKS`, `READ_SECTION_LIST_LIMIT` - Passages per `read_section` call, sections listed on no match (default: 25, 60)
- `SHORT_CODE_MAX_CHARS`, `AUTO_CODE_MATCHES`, `FIND_CODE_MAX_RESULTS` - Exact-code lookup: longest code that triggers the automatic pass, passages it adds, passages per `find_code` call (default: 3, 5, 10)
- `MAX_HISTORY_TURNS`, `HISTORY_DOC_TURNS`, `MAX_HISTORY_DOCS` - Conversation history (default: 5, 2, 30)
- `REWRITE_MAX_TOKENS`, `REWRITE_ANSWER_CHARS`, `REWRITE_ANSWER_TAIL_CHARS`, `REWRITE_MAX_QUERY_CHARS` - Follow-up rewriting (default: 4096, 1500, 500, 500)
- `DEFAULT_MAX_TOKENS`, `DEFAULT_LLM_TEMPERATURE`, `RETRY_LLM_TEMPERATURE`, `MAX_TOOL_CALL_RETRIES` - LLM call (default: 50000, 0.7, 0.9, 2)
- `SYSTEM_PROMPT`, `REWRITE_PROMPT` - LLM instructions

---

## Running the Pipeline

### Single Query

```bash
python3 query_main.py "How do I clean the air filter?"

# Verbose output (all retrieved documents with chunk type, match type, score,
# hybrid breakdown "Hybrid: exact_part (cosine=..., bm25=..., parts=...)" and full text)
python3 query_main.py "How do I clean the air filter?" --verbose
python3 query_main.py "How do I clean the air filter?" -v
```

### Interactive Mode

```bash
python3 query_main.py --interactive
python3 query_main.py -i

# Inside interactive mode:
#   - Type your query and press Enter (each query is answered separately, no history)
#   - Type 'verbose' to toggle detailed output
#   - Type 'clear' to clear the screen
#   - Type 'quit' to exit
```

### Web UI (multi-turn chat)

```bash
python3 web_main.py                         # http://127.0.0.1:5000
python3 web_main.py --host 0.0.0.0 --port 8000
```

- `GET /` - chat page (re-renders this session's conversation)
- `POST /api/chat` - `{"message": "..."}` → answer, diagram image URLs, errors, and the rewritten query if one was used
- `POST /api/reset` - start a new conversation
- `GET /diagrams/<filename>` - serve an image from `extracted_diagrams/`

The web UI removes the `Diagrams:` block from the answer text and shows those images instead. It reads the image path as everything after the last `": "` on each diagram line.

---

## Key Design Decisions

✅ **Tool Calling Architecture** - The LLM answers only through structured tool calls

✅ **Agent Loop** - The LLM can search, read sections and read pages (3 rounds / 300 s) before answering

✅ **Hybrid Search** - Dense similarity blended with BM25 and exact part-number matching, so a query naming a model or part number retrieves passages containing it; short error codes shared by many manuals get an exact-code lookup inside the retrieved manuals (see [hybrid_search.md](hybrid_search.md))

✅ **Section Awareness** - Passages carry their heading path; neighbors stay in the hit's section; `read_section` fetches whole sections; citations show sections confirmed from retrieved documents

✅ **Conversation Memory** - Follow-ups are rewritten for retrieval, and documents cited in recent turns stay available

✅ **Stable Refs** - Documents are numbered once per query; diagrams are chosen by ref, never by file path

✅ **Prefer Answering** - The system prompt asks for more context before giving up

✅ **Source Attribution** - Answers cite filename, page, section and snippet; procedures cite each step

✅ **Fallback Documents** - Insufficient-evidence responses still show the most relevant retrieved document

✅ **High-Level Logging** - Only node entry/exit and significant events are logged

---

## Conditional Routing

```python
add_history_documents
    ↓
[Any documents?]
    /        \
  YES         NO
   ↓           ↓
generate_    END (no_results_found)
response

generate_response
    ↓
[Tool call?]
    ├─ search_documents / read_section / read_page / find_code → search_more → generate_response
    └─ respond_to_user / insufficient_evidence / none → process_tool_call → END
```

---

## Timeouts

**Per-Node Timeouts**:
- `user_query` - Immediate (no external calls)
- `rewrite_query` - 120 seconds (one LLM call, only with chat history)
- `embed_query` - 30 seconds
- `vector_store_search` - Chroma query + in-memory BM25 (local; the lexical index is built once per process, about 0.2 s)
- `generate_response` - 120 seconds per LLM call
- `search_more` - `search_documents`: embedding 30 seconds + Chroma search; `read_section` / `read_page` / `find_code`: Chroma only

**Agent loop**: searching stops after 3 rounds or 300 s, then the LLM must give a final answer. Worst case is 9 LLM calls: the rewrite plus 4 agent turns of up to 2 attempts each. The deadline is checked only before each agent turn, so one run can take roughly 11 minutes in the worst case.

**Pipeline Total Timeout**: none enforced across the whole run (LangGraph recursion limit: 40 steps)

---

## Files

| File | Purpose |
|------|---------|
| `src/query/nodes.py` | Node implementations (user_query, rewrite_query, embed_query, vector_store_search, add_history_documents, generate_response, search_more, process_tool_call), tools and prompts |
| `src/query/tools.py` | Embedding, Chroma and LLM service integrations (`chat_with_tools`, plain `chat`) |
| `src/query/lexical.py` | BM25 + exact part-number index for hybrid search |
| `src/utils/part_numbers.py` | Part-number extraction and normalization (shared with ingestion) |
| `src/ingestion/tools.py` | `ChromaVectorStore.hybrid_search` (and `query_similarity`, `get_chunks`) |
| `src/query/graph.py` | LangGraph pipeline definition and `execute_query_pipeline` |
| `src/data_models.py` | `QueryState`, `QueryResult`, `ChatTurn`, `ChunkMetadata` |
| `query_main.py` | Command-line entry point (single query / interactive) |
| `web_main.py`, `web/app.py` | Flask web UI with multi-turn chat and diagram display |
| `tests/verify_query_loop.py` | Offline checks: part-number extraction, hybrid search, exact-code lookup and `find_code`, agent loop, read_page / read_section, neighbors, history, citations (scripted LLM, fake embeddings) |
| `requirements/hybrid_search.md` | Hybrid search specification |

---

## Troubleshooting

**Error: "Connection timeout"**
- Verify embedding/LLM services are running
- Check `EMBEDDING_BASE_URL` and `LLM_BASE_URL` are accessible

**Error: "No documents found"**
- Run the ingestion pipeline first: `python ingestion_main.py`
- Verify documents are in `./documents/`
- Check the Chroma database exists at `CHROMA_PERSIST_DIRECTORY`

**Error: "API key rejected"**
- Verify API keys in `.env` are valid

**Error: "Chroma connection failed"**
- Verify `CHROMA_PERSIST_DIRECTORY` exists and is writable
- Check disk space

**No `section` on passages / `read_section` says "no section information"**
- The documents were ingested before section support. Re-ingest them (`python manage_documents.py --update <path>` then `python ingestion_main.py`, or delete and re-run)

**A query naming a part / model number retrieves the wrong manual, or no `exact_part` hits in verbose mode**
- The documents were ingested before `part_numbers` metadata existed. Re-ingest them
- Check the number is extracted: `python -c "from src.utils.part_numbers import extract_part_numbers as e; print(e('your query'))"`
- Very common numbers (in more than 20 chunks) count as broad and get only a small boost (see [hybrid_search.md](hybrid_search.md))

**Empty or nonsensical answers**
- Check retrieved documents in verbose mode: `query_main.py "query" -v`
- Try rephrasing the query
- Consider adjusting `SEARCH_MIN_SIMILARITY` in `.env`
