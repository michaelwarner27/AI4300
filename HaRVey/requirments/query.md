# Vector Query Pipeline

This document describes the LangGraph-based query pipeline that retrieves semantically similar documents from the vector database and generates LLM-powered responses with source attribution.

## Overview

The query pipeline accepts a user question, converts it to an embedding, searches the vector database for similar documents, and uses an LLM to synthesize an answer with proper source citations. It handles cases where insufficient evidence is found in the knowledge base.

### Architecture Diagram

```
User Query (Input)
    ↓
┌─────────────────────────┐
│  user_query             │ Validate input
└─────────────┬───────────┘
              ↓
┌─────────────────────────┐
│  embed_query            │ Generate embedding vector
└─────────────┬───────────┘
              ↓
┌─────────────────────────┐
│  vector_store_search    │ Query Chroma, retrieve top 10
└─────────────┬───────────┘
              ↓
        [Results Found?]
        /              \
      YES               NO
      ↓                 ↓
┌──────────────┐    [Skip to Output]
│generate_     │◄────────┐   ↓
│response      │         │  Final Response
│(LLM + tools) │         │
└──────┬───────┘   ┌─────┴────────┐
       │ search_   │ search_more  │ Run search_documents,
       │ documents │ (max 3 rounds)│ append new chunks
       ├──────────►└──────────────┘
       │ final answer
       ↓
┌──────────────┐
│process_      │ Handle tool call
│tool_call     │
└──────┬───────┘
       ↓
  Final Response (Output)
```

`generate_response` is an agent turn. If the LLM calls `search_documents`, `search_more` runs the search, adds the results to the conversation and returns to `generate_response`. Any final answer (`respond_to_user` / `insufficient_evidence`) goes to `process_tool_call`.

---

## Nodes

### 1. user_query

**Purpose**: Accept and validate user input queries.

**Inputs**: User query string

**Outputs**: Validated query string

**Responsibilities**:
- Validate query is not empty
- Ensure query is within reasonable length limits
- Log validation success or failure
- Set `query_validated` flag in state
- Handle empty or invalid input gracefully

**Behavior**:
- Validation failures trigger `query_validation_failed` status
- Valid queries proceed to embedding generation
- No preprocessing or modification of query text

---

### 2. embed_query

**Purpose**: Convert the validated query string into a vector embedding.

**Inputs**: Validated query string

**Outputs**: Vector embedding (array of floats)

**Responsibilities**:
- Call embedding service API with the query text
- Handle API errors (connection failures, authentication, timeouts)
- Validate response format and vector dimensions
- Log successful embedding with vector size
- Propagate errors to next nodes

**Configuration**:
- Service URL: `EMBEDDING_BASE_URL`
- Model: `EMBEDDING_MODEL` (e.g., Qwen3-Embedding-8B)
- Authentication: API key from `EMBEDDING_API_KEY`
- Timeout: 30 seconds

**Key Functions**:
- Reuses `EmbeddingService` from ingestion pipeline
- Single query embedding (not batched)
- Errors stored in state for diagnostics

---

### 3. vector_store_search

**Purpose**: Query the Chroma vector database for semantically similar documents.

**Inputs**: Query embedding vector

**Outputs**: Top K results with metadata and similarity scores

**Responsibilities**:
- Connect to Chroma collection with configured persistence directory
- Search for K nearest neighbors (default: 10 results)
- Filter results by minimum similarity score (default: 0.5)
- Extract and return full metadata for each result (filename, page, chunk content, plus `chunk_type`, `diagram_kind` and `diagram_path` for diagram chunks; these three are read with defaults so older records still load)
- Handle no-results case gracefully (status: `no_results_found`)
- Handle database connection errors

**Configuration**:
- Database: Chroma
- Persist directory: `CHROMA_PERSIST_DIRECTORY`
- Collection name: `CHROMA_COLLECTION_NAME`
- K (number of results): 10
- Min similarity threshold: 0.5

**Key Functions**:
- Queries Chroma with vector similarity search
- Extracts metadata for each result
- Filters by similarity score threshold
- Returns conditional status for routing

---

### 4. generate_response

**Purpose**: Use an LLM with tool calling to synthesize an answer based on retrieved documents.

**Inputs**: Original query + Top K documents with similarity scores

**Outputs**: Tool call invocation (respond_to_user or insufficient_evidence)

**Responsibilities**:
- Call LLM service with retrieved documents as context
- Present system prompt that prefers answering over deferring
- Receive tool call from LLM (structured output)
- Handle tool call parsing errors with retry logic (up to 3 attempts)
- Fall back to document display on repeated failures
- Log response generation success or errors

**Configuration**:
- Service URL: `LLM_BASE_URL`
- Model: `LLM_MODEL` (e.g., Qwen3.8-Flash-Next)
- Authentication: API key from `LLM_API_KEY`
- Max tokens: 2048
- Temperature: 0.7
- Timeout: 120 seconds

**System Prompt Behavior**:
- Instructs LLM to prefer providing answers when documents are relevant
- Encourages use of `respond_to_user` tool over `insufficient_evidence`
- Restricts responses to document content (no hallucination)
- Guides source attribution format

**Neighbor context**: after the vector search, each of the top 5 text hits (`NEIGHBOR_TOP_HITS`) also brings the chunks immediately before and after it (`chunk_sequence` ±1, `NEIGHBOR_RADIUS`), so a retrieval returns at most 10 matches + 10 neighbors. Headings are often split from the text they introduce, and PDF text can be out of reading order. Results are listed in reading order and neighbors are flagged `"neighbor": true`.

**Diagram context**: each retrieved document is sent to the LLM with a numeric `ref`, its `chunk_type`, and, for `diagram_reference` chunks, `diagram_kind` and `diagram_path`. The LLM selects diagrams by `ref` through `diagram_refs`; it never supplies file paths.

**Tool Calls**:

The LLM makes one tool call per turn. `search_documents` is non-terminal (the loop continues); the other two end the turn:

**Tool 1: respond_to_user**
```json
{
  "name": "respond_to_user",
  "answer": "Synthesized answer text",
  "sources": [
    {"filename": "doc.pdf", "page": 5, "snippet": "..."},
    {"filename": "doc2.pdf", "page": 12, "snippet": "..."}
  ],
  "include_sources": true,
  "steps": [
    {"instruction": "Mount the bracket", "source_filename": "doc.pdf", "page": 5}
  ],
  "diagram_refs": [3, 7]
}
```

`steps` is optional: ordered step-by-step instructions for procedural questions, rendered as a numbered `Steps:` list between the answer and the sources.

`diagram_refs` is optional: `ref` values of retrieved `diagram_reference` documents whose images should be shown.

**Tool 2: search_documents** (non-terminal)
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
Only `query` is required. `k` defaults to 5 (max 10).

**Tool 3: read_page** (non-terminal)
```json
{
  "name": "read_page",
  "filename": "doc.pdf",
  "page_start": 16,
  "page_end": 17
}
```
`page_start` and `filename` are required; `page_end` defaults to `page_start` and is clamped to 3 pages. Pages are PDF page numbers (the `page` shown in documents), which can differ from the printed page numbers.

**Tool 4: insufficient_evidence**
```json
{
  "name": "insufficient_evidence",
  "explanation": "Why documents don't address query",
  "query": "Original user query"
}
```

---

### 4b. search_more (agent loop: search_documents / read_page)

**Purpose**: Execute the LLM's `search_documents` or `read_page` call and feed new context back to the LLM.

**Inputs**: `search_documents` (`query`, optional `filename`, `page_start`, `page_end`, `k`) or `read_page` (`filename`, `page_start`, optional `page_end`) tool call

**Outputs**: Updated `search_results` and `messages`; control returns to `generate_response`

**Responsibilities**:
- `search_documents`: embed the search text and query Chroma with an optional metadata filter (`source_filename`, `page_number` range). The similarity cutoff is `SEARCH_MIN_SIMILARITY` (default 0.0 = none; `k` limits results), unlike the first retrieval (0.6)
- `read_page`: fetch every chunk on a page range (max 3 pages per call) by metadata only, in reading order, with no similarity step. Chunks already in context are not repeated; their refs are listed. Use it when a heading was found without the text that belongs to it
- Filenames are matched leniently (exact, or a unique substring of a filename in context; omitted when only one document is in context)
- Skip chunks already retrieved (key: filename + chunk sequence) so each passage is shown once
- Append new chunks to `search_results`; existing `ref` numbers never shift, new chunks continue the numbering (so `diagram_refs` stay valid)
- Add the result as a `tool` message to the conversation (full chunk text)
- Report search failures or empty results to the LLM as the tool result instead of aborting

**Limits**: at most `MAX_SEARCH_ROUNDS` (3) tool rounds in total (`search_documents` and `read_page` share the budget) and `AGENT_DEADLINE_SECONDS` (300 s) since the first LLM turn. After either limit both tools are no longer offered and the LLM is told to answer.

---

### 5. process_tool_call

**Purpose**: Process the LLM's tool call and format the final response.

**Inputs**: Tool call from generate_response node

**Outputs**: Final response string formatted for user display

**Responsibilities**:
- Parse tool call JSON structure
- Route to appropriate handler based on tool name
- Extract parameters from tool call
- Format response with proper source citations
- Handle unknown or malformed tool calls
- Return formatted string ready for display

**Handler: respond_to_user**
- Extract synthesized answer and sources
- Format as: "Answer\n\n---\n\nSources:\n- filename (page X): snippet"
- Omit sources if `include_sources=false`
- If `diagram_refs` is present, append `\nDiagrams:\n- filename (page X, kind): /absolute/path/to/image`. Refs that are out of range, not `diagram_reference` chunks, or whose image file is missing are dropped. Paths are resolved against the project root (falling back to `extracted_diagrams/<filename>`), so they work from any working directory. The block is shown even when `include_sources=false`.
- Preserve snippet content exactly as provided by LLM

**Handler: insufficient_evidence**
- Format as: "⚠️ Insufficient Information\n\nExplanation\n\nHowever, here is the most relevant document that was retrieved:\n\n  Source: ...\n  Snippet: ..."
- Display only the single most relevant retrieved document (highest-scoring direct hit; neighbor chunks excluded) with its source and snippet
- Reference original query in output

---

## State Management

The pipeline uses a central `QueryState` object that flows through all nodes:

```python
@dataclass
class QueryState:
    original_query: str                    # User's question
    query_validated: bool                  # Validation flag
    query_embedding: list[float]           # Query vector
    search_results: list[QueryResult]      # Retrieved documents
    tool_call: ToolCall | None             # Latest LLM tool call
    messages: list[dict]                   # LLM conversation (agent loop)
    search_round: int                      # search_documents calls executed
    started_at: float | None               # Start time of the agent loop
    final_response: str                    # Formatted response
    errors: list[str]                      # Error log
    status: str                            # Pipeline status
    config: IngestionConfig                # Configuration reference
```

**Status Values**:
- `query_validated` - Query passed validation
- `query_embedded` - Embedding generated
- `search_completed` - Vector search completed
- `tool_call_received` - LLM returned a tool call
- `search_more_completed` - A `search_documents` round finished
- `response_generated` - LLM response generated
- `response_generated_with_errors` - Response generated but with errors
- `no_results_found` - No documents matched query
- `*_failed` - Error occurred at specific stage

---

## Error Handling Pattern

The pipeline accumulates errors without stopping:

1. **Query Validation Failure** → Stop immediately, return error status
2. **Embedding Failure** → Log error, propagate to next nodes, attempt response with fallback
3. **Search Failure** → Log error, skip to response generation with error context
4. **No Results Found** → Not an error; return status `no_results_found` and skip LLM
5. **LLM Tool Call Failure** → Retry up to 3 times, then return fallback response
6. **Unknown Tool Call** → Return fallback with error message

**Fallback Response Format**:
```
⚠️ Unable to generate LLM response due to the following errors:
  - [Error 1]
  - [Error 2]

However, here are the documents retrieved from the database that may be relevant:

Document 1:
  Source: filename.pdf (Page 5)
  Similarity Score: 0.8234
  Content: [first 500 characters]
```

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

# Vector Database
CHROMA_PERSIST_DIRECTORY=./chroma_db
CHROMA_COLLECTION_NAME=vector_ingestion_collection

# Agent search (optional)
SEARCH_MIN_SIMILARITY=0.0
LLM_REASONING_EFFORT=medium

# Document Input (for ingestion pipeline)
DOCUMENT_INPUT_DIR=./documents
```

**Tunable Parameters** (in `src/query/nodes.py`):
- `MAX_SEARCH_ROUNDS`, `AGENT_DEADLINE_SECONDS` - Agent loop limits (default: 3, 300 s)
- `SEARCH_DEFAULT_K`, `SEARCH_MAX_K` - Results per `search_documents` call (default: 5, 10)
- `NEIGHBOR_RADIUS`, `NEIGHBOR_TOP_HITS`, `READ_PAGE_MAX_PAGES` - Neighbor chunks per hit, how many top hits get neighbors, pages per `read_page` (default: 1, 5, 3)
- `DEFAULT_K` - Number of results to retrieve (default: 10)
- `DEFAULT_MIN_SIMILARITY_SCORE` - Similarity threshold (default: 0.5)
- `DEFAULT_MAX_TOKENS` - Max LLM response length (default: 2048)
- `DEFAULT_LLM_TEMPERATURE` - LLM creativity (default: 0.7)
- `SYSTEM_PROMPT` - LLM behavior instructions

---

## Running the Pipeline

### Single Query

```bash
# Standard output
python3 query_main.py "What is in the documents?"

# Verbose output (shows all retrieved documents)
python3 query_main.py "What is in the documents?" --verbose
python3 query_main.py "What is in the documents?" -v
```

### Interactive Mode

```bash
# Start interactive REPL
python3 query_main.py --interactive
python3 query_main.py -i

# Inside interactive mode:
#   - Type your query and press Enter
#   - Type 'verbose' to toggle detailed output
#   - Type 'clear' to clear the screen
#   - Type 'quit' to exit
```

### Batch Testing

```bash
# Check available models
python3 check_models.py

# Update LLM model
python3 update_model.py
```

---

## Output Formats

### Standard Output

```
Status: response_generated
Query: "What food safety topics are covered?"

Retrieved Documents:
  - food_safety.pdf (page 5): Similarity 0.87
  - guidelines.pdf (page 12): Similarity 0.82

Answer:
The documents cover several key food safety topics including proper storage
temperatures, cross-contamination prevention, and employee hygiene requirements.

Sources:
- food_safety.pdf (page 5): "Proper storage prevents bacterial growth..."
- guidelines.pdf (page 12): "Employees must wash hands regularly..."
```

### Verbose Output

Shows full retrieved documents with longer content snippets.

### No Results Found

```
Status: no_results_found
Query: "What is in the documents?"

No documents found matching your query.
Try rephrasing your question or using different keywords.
```

### Error Response

```
Status: response_generated_with_errors
Query: "Your question"

⚠️ Unable to generate LLM response due to the following errors:
  - LLM service timeout after 120 seconds

However, here are the documents retrieved from the database that may be relevant:
[Retrieved documents listed...]
```

---

## Key Design Decisions

✅ **Tool Calling Architecture** - LLM uses structured tool calls for reliability

✅ **Prefer Answering** - System prompt biases LLM toward providing answers

✅ **Retry Logic** - Up to 3 retries on LLM tool call failures before fallback

✅ **Source Attribution** - All answers include filename, page, and content snippet

✅ **Fallback Documents** - Insufficient evidence responses show retrieved documents anyway

✅ **Single User System** - No per-user isolation; error handling is simplified

✅ **High-Level Logging** - Only logs node entry/exit and significant events

---

## Conditional Routing

The pipeline uses conditional edges for smart routing:

```python
vector_store_search
    ↓
[Results Found?]
    /        \
  YES         NO
   ↓           ↓
generate_    output
response

generate_response
    ↓
[Tool Call Valid?]
    /         \
  YES          NO
   ↓            ↓
process_     fallback
tool_call    response
```

---

## Timeouts

**Per-Node Timeouts**:
- `user_query` - Immediate (no external calls)
- `embed_query` - 30 seconds
- `vector_store_search` - 30 seconds
- `generate_response` - 120 seconds per LLM call
- `search_more` - embedding 30 seconds + Chroma search

**Agent loop**: each LLM call has the 120 s timeout; searching stops after 3 rounds or 300 s, then the LLM must give a final answer. Worst case is several LLM calls.

**Pipeline Total Timeout**: none enforced across the whole run

**Timeout Handling**: Errors logged and propagated to next nodes

---

## Files

| File | Purpose |
|------|---------|
| `src/query/nodes.py` | 6 node implementations (incl. `search_more`) |
| `src/query/tools.py` | Embedding and LLM service integrations |
| `src/query/graph.py` | LangGraph pipeline definition |
| `query_main.py` | Entry point and query execution |
| `tests/verify_query_loop.py` | Offline checks of the agent loop (scripted LLM, fake embeddings) |
| `check_models.py` | Display available models |
| `update_model.py` | Interactive model selection |

---

## Troubleshooting

**Error: "Model not available"**
```bash
python3 check_models.py           # See available models
python3 update_model.py           # Change the model
```

**Error: "Connection timeout"**
- Verify embedding/LLM services are running
- Check `EMBEDDING_BASE_URL` and `LLM_BASE_URL` are accessible
- Increase timeout values if services are slow

**Error: "No documents found"**
- Run ingestion pipeline first: `python ingestion_main.py`
- Verify documents are in `./documents/` directory
- Check Chroma database exists at `CHROMA_PERSIST_DIRECTORY`

**Error: "API key rejected"**
- Verify API keys in `.env` are valid
- Check they match the service requirements
- Try `update_model.py` to verify connection

**Error: "Chroma connection failed"**
- Verify `CHROMA_PERSIST_DIRECTORY` exists
- Check disk space available
- Ensure no permission issues with database directory

**Empty or nonsensical answers**
- Try rephrasing your query
- Check retrieved documents in verbose mode: `query_main.py "query" -v`
- Consider adjusting `DEFAULT_MIN_SIMILARITY_SCORE` if threshold is too low
