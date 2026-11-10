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
│generate_     │         ↓
│response      │    Final Response
│(LLM + tools)│
└──────┬───────┘
       ↓
┌──────────────┐
│process_      │ Handle tool call
│tool_call     │
└──────┬───────┘
       ↓
  Final Response (Output)
```

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
- Extract and return full metadata for each result (filename, page, chunk content)
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

**Tool Calls**:

The LLM returns one of two tool calls:

**Tool 1: respond_to_user**
```json
{
  "name": "respond_to_user",
  "answer": "Synthesized answer text",
  "sources": [
    {"filename": "doc.pdf", "page": 5, "snippet": "..."},
    {"filename": "doc2.pdf", "page": 12, "snippet": "..."}
  ],
  "include_sources": true
}
```

**Tool 2: insufficient_evidence**
```json
{
  "name": "insufficient_evidence",
  "explanation": "Why documents don't address query",
  "query": "Original user query"
}
```

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
- Preserve snippet content exactly as provided by LLM

**Handler: insufficient_evidence**
- Format as: "⚠️ Insufficient Information\n\nExplanation\n\nHowever, here are retrieved documents:\n..."
- Display all retrieved documents with snippets
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
    final_response: str                    # Formatted response
    errors: list[str]                      # Error log
    status: str                            # Pipeline status
    config: IngestionConfig                # Configuration reference
```

**Status Values**:
- `query_validated` - Query passed validation
- `query_embedded` - Embedding generated
- `search_completed` - Vector search completed
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

# Document Input (for ingestion pipeline)
DOCUMENT_INPUT_DIR=./documents
```

**Tunable Parameters** (in `src/query/nodes.py`):
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
- `generate_response` - 120 seconds

**Pipeline Total Timeout**: 2 minutes (120 seconds)

**Timeout Handling**: Errors logged and propagated to next nodes

---

## Files

| File | Purpose |
|------|---------|
| `src/query/nodes.py` | 5 node implementations |
| `src/query/tools.py` | Embedding and LLM service integrations |
| `src/query/graph.py` | LangGraph pipeline definition |
| `query_main.py` | Entry point and query execution |
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
- Run ingestion pipeline first: `python main.py`
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
