# Query Node Requirements

## Overview
This document outlines the specification for the Query component of the vector ingestion semantic memory tool, implemented using LangGraph nodes.

## Architecture

### Node Flow
```
User Query → Embed Query → Search Vector Store → Generate Response → Process Tool Call → Return Result
```

## Nodes

### 1. User Query Node
**Purpose**: Accept and validate user input queries

**Input**: User query string
**Output**: Validated query string

**Requirements**:
- [ ] Input validation specifications
- [ ] Error handling strategy

### 2. Embed Query Node
**Purpose**: Convert user query into vector embeddings using the configured embedding model

**Model Details**:
- Base URL: `EMBEDDING_BASE_URL` from environment
- Model: `EMBEDDING_MODEL` from environment
- API Key: `EMBEDDING_API_KEY` from environment

**Input**: Validated query string (as-is, no preprocessing)
**Output**: Vector embedding (array of floats)

**Requirements**:
- No caching of embeddings
- Error handling for embedding failures must be passed to next nodes

### 3. Vector Store Search Node
**Purpose**: Query the Chroma vector database for semantically similar documents

**Database Details**:
- Database: Chroma
- Persist Directory: `CHROMA_PERSIST_DIRECTORY` from environment
- Collection Name: `CHROMA_COLLECTION_NAME` from environment

**Input**: Vector embedding
**Output**: Top K results (documents + metadata + similarity scores)

**Configuration**:
- **K (Number of Results)**: 10
- **Similarity Threshold**: Use minimum similarity score filtering to prevent hallucination
- **Metadata**: Include all metadata necessary to locate the chunk in the source file (e.g., source filename, chunk ID, page number)

**Requirements**:
- Filter results by minimum similarity score threshold
- Error handling for database queries

### 4. LLM Response with Tool Calling Node (Generate Response)
**Purpose**: Use the LLM with tool calling to generate a response or indicate insufficient evidence

**Model Details**:
- Base URL: `LLM_BASE_URL` from environment
- Model: `LLM_MODEL` from environment
- API Key: `LLM_API_KEY` from environment

**Input**: Original query + Top K documents (in JSON format) + similarity scores
**Output**: Tool call with either:
  - `respond_to_user` tool call (with synthesized answer + sources)
  - `insufficient_evidence` tool call (with explanation)

**Configuration**:
- **Response Format**: Tool calling (structured output)
- **Max Tokens**: Generous limit
- **Source Attribution**: Sources passed to respond_to_user tool

**Available Tools**:
1. **`respond_to_user`**
   - Parameters:
     - `answer`: str (synthesized response to the user query)
     - `sources`: list of source information (filename, page, chunk_sequence)
   - Purpose: Return a well-supported answer based on retrieved documents
   
2. **`insufficient_evidence`**
   - Parameters:
     - `explanation`: str (why the retrieved documents don't provide sufficient evidence)
     - `query`: str (original user query for reference)
   - Purpose: Indicate that the retrieved documents don't adequately address the query

**Requirements**:
- LLM must use tool calling to invoke one of the two tools
- Error handling for LLM failures: Return fallback response with error message
- Tool call responses are processed by process_tool_call node

### 5. Process Tool Call Node
**Purpose**: Process the tool call returned by the LLM and format the final response

**Input**: Tool call from generate_response node
**Output**: Final response string (user answer or insufficient evidence message)

**Tool Handlers**:
1. **respond_to_user Handler**:
   - Extract answer and sources
   - Format as final response with source citations
   - Return formatted string
   
2. **insufficient_evidence Handler**:
   - Extract explanation
   - Format as informative message indicating lack of evidence
   - Return formatted string

**Requirements**:
- Parse and validate tool call structure
- Handle unknown tool calls gracefully
- Format responses consistently
- Preserve source information in output
**Purpose**: Use the LLM with tool calling to generate a response or indicate insufficient evidence

**Model Details**:
- Base URL: `LLM_BASE_URL` from environment
- Model: `LLM_MODEL` from environment
- API Key: `LLM_API_KEY` from environment

**Input**: Original query + Top K documents (in JSON format) + similarity scores
**Output**: Tool call with either:
  - `respond_to_user` tool call (with synthesized answer + sources)
  - `insufficient_evidence` tool call (with explanation)

**Configuration**:
- **Response Format**: Tool calling (structured output)
- **Max Tokens**: Generous limit
- **Source Attribution**: Sources passed to respond_to_user tool

**Available Tools**:
1. **`respond_to_user`**
   - Parameters:
     - `answer`: str (synthesized response to the user query)
     - `sources`: list of source information (filename, page, chunk_sequence)
   - Purpose: Return a well-supported answer based on retrieved documents
   
2. **`insufficient_evidence`**
   - Parameters:
     - `explanation`: str (why the retrieved documents don't provide sufficient evidence)
     - `query`: str (original user query for reference)
   - Purpose: Indicate that the retrieved documents don't adequately address the query

**Requirements**:
- LLM must use tool calling to invoke one of the two tools
- Error handling for LLM failures: Return fallback response with error message
- Tool call responses are processed by process_tool_call node

## Cross-Cutting Concerns

### Logging
- Log each time a node is executed (entry/exit)
- Log errors only (no debug logs for normal operation)

### Error Handling and Recovery
- Embedding failures: Pass error to subsequent nodes
- Vector store query failures: Return error message with fallback documents
- LLM failures: Return retrieved documents with error message to user
- Single-user system (no per-user error isolation needed)

### Performance
- **Pipeline Timeout**: 2 minutes (120 seconds) for entire query pipeline
- **Rate Limiting**: Not needed (single-user toy system)

## Environment Variables
All required environment variables are documented in `.env.example`

## LangGraph Implementation

### Graph Structure

```
                    ┌─────────────────┐
                    │   User Query    │
                    │     (input)     │
                    └────────┬────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  user_query     │
                    │ (validate input)│
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │   Validation    │
                    │    Success?     │
                    └────┬─────────┬──┘
                    YES  │         │  NO
                         │         └─────► FAIL
                         ▼
                    ┌─────────────────┐
                    │  embed_query    │
                    │(gen embedding)  │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │   Embedding     │
                    │   Success?      │
                    └────┬─────────┬──┘
                    YES  │         │  NO
                         │         └─────► FAIL
                         ▼
                ┌─────────────────────────┐
                │vector_store_search      │
                │(query Chroma, K=10,     │
                │ similarity threshold)   │
                └────────┬────────────────┘
                         │
                ┌────────▼─────────────┐
                │  Results Found?      │
                └────┬─────────────┬───┘
              YES    │             │  NO
                     │             └──────► SKIP_TO_RESPONSE
                     ▼
            ┌───────────────────────────┐
            │   generate_response       │
            │  (LLM with tool calling)  │
            └────────┬──────────────────┘
                     │
            ┌────────▼───────────────────┐
            │  Tool Call Returned?        │
            └────┬──────────────────┬─────┘
         YES     │                  │  NO
                 │                  └──► FALLBACK_RESPONSE
                 ▼
        ┌────────────────────────────┐
        │  process_tool_call         │
        │ (Handle tool response)     │
        └────────┬───────────────────┘
                 │
        ┌────────▼──────────────────┐
        │  Tool Type?                │
        └────┬──────────────┬────────┘
        │                   │
   respond_to_user    insufficient_evidence
        │                   │
        ▼                   ▼
    ┌─────────────────┐  ┌──────────────────┐
    │Format Answer    │  │Format "No Evidence"
    │with Sources     │  │Message
    └────────┬────────┘  └────────┬─────────┘
             │                    │
             └────────┬───────────┘
                      ▼
            ┌──────────────────────┐
            │  Final Response      │
            │     (output)         │
            └──────────────────────┘
```

### State Machine Flow

| Current State | Transition Condition | Next State | Action |
|---|---|---|---|
| `initialized` | User provides query | `query_validated` | Store and validate query |
| `query_validated` | Embedding succeeds | `query_embedded` | Store embedding vector |
| `query_embedded` | Search succeeds with results | `search_completed` | Store QueryResult objects |
| `search_completed` | LLM tool call succeeds | `tool_call_received` | Store tool call result |
| `tool_call_received` | Tool = respond_to_user | `response_generated` | Store user answer + sources |
| `tool_call_received` | Tool = insufficient_evidence | `insufficient_evidence_returned` | Store evidence message |
| `search_completed` | LLM fails | `response_generated_with_errors` | Store fallback response |
| `*` | Results not found | `no_results_found` | Return no results status |
| `*` (any stage) | Validation/connection fails | `*_failed` | Stop and return error |

### Node Connections and Conditional Routing

```python
graph.add_edge("user_query", "embed_query")
graph.add_edge("embed_query", "vector_store_search")
graph.add_conditional_edges(
    "vector_store_search",
    _should_generate_response,
    {
        "generate": "generate_response",
        "skip": "output"
    }
)
graph.add_edge("generate_response", "process_tool_call")
graph.add_edge("process_tool_call", "output")
```

**Conditional Logic Explanation:**
- `_should_generate_response()` function determines if LLM response generation should proceed
- Returns "generate" if search_results is not empty
- Returns "skip" if search_results is empty (no relevant documents found)
- `generate_response` node uses tool calling to invoke one of two tools
- `process_tool_call` node handles the tool invocation and formats output
- All paths lead to "output" node which returns final state

**Tool Call Routing in generate_response:**
- LLM returns one of two tool calls:
  1. `respond_to_user` - LLM found sufficient evidence
  2. `insufficient_evidence` - LLM found insufficient evidence
- Tool call is passed to `process_tool_call` node for processing

### Configuration Parameters

| Parameter | Default | Description | Tunable |
|---|---|---|---|
| `DEFAULT_K` | 10 | Number of top results from vector store | Yes (in nodes.py) |
| `DEFAULT_MIN_SIMILARITY_SCORE` | 0.5 | Minimum similarity threshold for filtering | Yes (in nodes.py) |
| `DEFAULT_MAX_TOKENS` | 2048 | Maximum tokens in LLM response | Yes (in nodes.py) |
| `DEFAULT_LLM_TEMPERATURE` | 0.7 | LLM temperature (0.0-1.0) | Yes (in nodes.py) |
| `SYSTEM_PROMPT` | See notes below | System prompt for LLM behavior | Yes (in nodes.py) |

### System Prompt (UPDATED FOR TOOL CALLING)

```
You are a helpful assistant that answers questions based on provided documents.

You have two tools available:
1. respond_to_user: Use this when you can provide a well-supported answer based on the documents
2. insufficient_evidence: Use this when the documents do not adequately address the query

INSTRUCTIONS:
- Prefer to provide an answer using respond_to_user if the documents contain relevant information
- Only use insufficient_evidence if the documents clearly do not address the query
- Err on the side of providing an answer rather than saying there's insufficient evidence
- When using respond_to_user:
  * Synthesize a clear, concise answer based on the retrieved documents
  * Include relevant sources (filename, page, snippet) in your sources list
  * Set include_sources=true to show sources, or false to omit them
- When using insufficient_evidence:
  * Explain clearly why the documents don't provide sufficient evidence
  * Reference the original query

Answer ONLY based on the information in the provided documents. Do not hallucinate or infer beyond what the documents contain.
```

**Purpose**: 
- Instructs LLM to use tool calling for structured responses
- Encourages `respond_to_user` tool use when possible (prefer answers)
- Provides clear guidance on tool selection
- Prevents hallucination by restricting to document content
- Allows flexibility in source inclusion

### Timeout Strategy

**Pipeline Timeout**: 120 seconds (2 minutes) for entire query execution

**Per-Node Timeouts**:
- `user_query`: Immediate (no external calls)
- `embed_query`: 30 seconds (embedding service timeout)
- `vector_store_search`: 30 seconds (Chroma query timeout)
- `generate_response`: 120 seconds (LLM service timeout)

**Timeout Handling**:
- If any node times out, accumulate error in state.errors
- Proceed to next node (errors propagate)
- LLM timeout returns fallback response with documents

### Error Handling Strategy

**By Node:**

| Node | Error Type | Handling | Output |
|---|---|---|---|
| user_query | Invalid input | Log error, set flag | status: "query_validation_failed" |
| embed_query | Connection error | Log error, propagate | status: "embed_query_failed" |
| embed_query | Invalid response | Log error, propagate | status: "embed_query_failed" |
| vector_store_search | Connection error | Log error, propagate | status: "vector_store_search_failed" |
| vector_store_search | No results | Log info, set status | status: "no_results_found" |
| generate_response | Invalid tool call | Retry up to 3 times, then fallback | status: "response_generated_with_errors" |
| generate_response | Tool call parsing error | Retry up to 3 times, then fallback | status: "response_generated_with_errors" |
| process_tool_call | Invalid tool name | Return fallback with error | status: "response_generated_with_errors" |
| process_tool_call | Missing parameters | Use received data as-is (no validation) | status: "response_generated" or "insufficient_evidence_returned" |

**Fallback Response Format (LLM failure)**:
```
⚠️  Unable to generate LLM response due to the following errors:
  - [Error 1]
  - [Error 2]
  - [Error 3]

However, here are the documents retrieved from the database that may be relevant:

Document 1:
  Source: filename.pdf (Page 5)
  Similarity Score: 0.8234
  Content: [first 500 characters]
```

### Input/Output Contracts

**Pipeline Input**:
```python
QueryState(
    original_query: str,  # Required: user's question
    config: IngestionConfig,  # Required: loaded from .env
)
```

**Pipeline Output**:
```python
QueryState(
    original_query: str,
    query_validated: bool,
    query_embedding: list[float],
    search_results: list[QueryResult],
    final_response: str,
    errors: list[str],
    status: str  # One of: "response_generated", "response_generated_with_errors", 
                 # "no_results_found", "*_failed"
)
```

### Logging Specification

**Log Levels**:
- INFO: Node entry point (when node is executed)
- WARNING: No results found, batches skipped
- ERROR: Any failure at any stage

**Log Format**:
```
[LEVEL] message
```

**Example Logs**:
```
[INFO] Entering user_query node
[INFO] user_query: Successfully validated query (length: 42 characters)
[INFO] Entering embed_query node
[INFO] embed_query: Successfully embedded query (vector dimension: 768)
[INFO] Entering vector_store_search node
[INFO] vector_store_search: Retrieved 10 results from vector store
[INFO] Entering generate_response node
[INFO] generate_response: Successfully generated response (1245 characters)
```

## Tool Calling Architecture (NEW)

### Overview
Instead of having the LLM directly return a free-form text response, the LLM now makes tool calls to structured functions. This ensures:
- Explicit decision-making by the LLM (answer vs. insufficient evidence)
- Structured response data that can be reliably parsed
- Better separation of concerns between LLM reasoning and response formatting

### Tool Definitions

#### Tool 1: `respond_to_user`
```json
{
  "name": "respond_to_user",
  "description": "Return a well-supported answer to the user's query based on retrieved documents",
  "parameters": {
    "answer": {
      "type": "string",
      "description": "The synthesized answer to the user's query"
    },
    "sources": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "filename": {"type": "string"},
          "page": {"type": "integer"},
          "chunk_sequence": {"type": "integer"},
          "snippet": {"type": "string"}
        }
      },
      "description": "List of sources used in the answer"
    }
  }
}
```

#### Tool 2: `insufficient_evidence`
```json
{
  "name": "insufficient_evidence",
  "description": "Indicate that the retrieved documents do not provide sufficient evidence to answer the query",
  "parameters": {
    "explanation": {
      "type": "string",
      "description": "Explanation of why the documents don't provide sufficient evidence"
    },
    "query": {
      "type": "string",
      "description": "The original user query for reference"
    }
  }
}
```

### Tool Invocation Flow
1. `generate_response` node calls LLM with tools available
2. LLM analyzes retrieved documents and makes a tool call:
   - Calls `respond_to_user` if evidence is sufficient
   - Calls `insufficient_evidence` if evidence is lacking
3. `process_tool_call` node processes the tool call:
   - Extracts parameters from tool call
   - Formats structured response
   - Returns formatted string to user

## Implementation Notes
- System prompt for the LLM defined in nodes.py (can be customized)
- Tool calling requires LLM that supports function_calling
- Minimum similarity score threshold (0.5) will be calibrated during testing
- Maximum tokens (2048) is generous; can be reduced if needed
- All configuration constants are in nodes.py for easy tuning
- QueryState dataclass manages state flow through pipeline
- No user needs to create QueryState manually; LangGraph handles state management

## Tool Calling Implementation Decisions

### 1. Tool Response Format - DECIDED ✓
**Decision**: Create optionality to omit sources if user only wants synthesized response. Otherwise, sources trail the synthesized response.

**Implementation**:
- `respond_to_user` tool has optional `include_sources` parameter (default: True)
- If `include_sources=True`: Format as "Answer\n\n---\n\nSources:\n- [filename] (page [page]): [snippet]"
- If `include_sources=False`: Return only the answer text
- User can control this via configuration or query parameter

### 2. Insufficient Evidence Response - DECIDED ✓
**Decision**: Display a message declaring not enough information and return the retrieved documents.

**Implementation**:
- Format: "⚠️ Insufficient Information\n\n{LLM explanation}\n\nHowever, here are the retrieved documents that may be relevant:\n\n{documents list}"
- Always show retrieved documents even when evidence deemed insufficient
- Include LLM's explanation of why evidence is insufficient
- Documents display with snippet preview

### 3. Source Information Detail - DECIDED ✓
**Decision**: Include filename, page, and snippet of the content.

**Implementation**:
- Source object includes:
  * `filename` (string)
  * `page` (integer)
  * `snippet` (string, max 200 chars)
- Do NOT include chunk_sequence or similarity score in final output
- Snippet extracted from full document content

### 4. LLM System Prompt Guidance - DECIDED ✓
**Decision**: Prefer `respond_to_user` unless evidence is clearly insufficient.

**Implementation**:
- System prompt instructs LLM:
  * "Prefer to provide an answer if the documents contain relevant information"
  * "Only use insufficient_evidence if the documents clearly do not address the query"
  * "Err on the side of providing an answer"

### 5. Error Handling for Invalid Tool Calls - DECIDED ✓
**Decision**: Retry the LLM call up to 3 times, then default to returning retrieved documents with LLM failure error message.

**Implementation**:
- `generate_response` node catches tool call errors
- Retry logic: up to 3 attempts to get valid tool call
- After 3 failures: Return fallback response with:
  * Error message explaining LLM tool call parsing failed
  * All retrieved documents
  * Status: "response_generated_with_errors"
- Log each retry attempt with error details

### 6. Tool Call Validation - DECIDED ✓
**Decision**: Trust the LLM output as-is (no parameter validation beyond JSON parsing).

**Implementation**:
- No validation of `answer` field (accept empty strings)
- No validation of `sources` array length (accept empty array)
- No validation of `snippet` content
- Only validate that tool call is JSON-parseable
- Trust LLM to return sensible data

## Tool Call Parameter Specifications (FINAL)

### Tool 1: respond_to_user (FINAL)
```json
{
  "name": "respond_to_user",
  "description": "Return a well-supported answer to the user's query based on retrieved documents",
  "parameters": {
    "answer": {
      "type": "string",
      "description": "The synthesized answer to the user's query"
    },
    "sources": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "filename": {"type": "string"},
          "page": {"type": "integer"},
          "snippet": {"type": "string", "description": "Preview of source content (max 200 chars)"}
        },
        "required": ["filename", "page"]
      },
      "description": "List of sources used in the answer (optional)"
    },
    "include_sources": {
      "type": "boolean",
      "description": "Whether to include sources in the final response (default: true)",
      "default": true
    }
  }
}
```

### Tool 2: insufficient_evidence (FINAL)
```json
{
  "name": "insufficient_evidence",
  "description": "Indicate that the retrieved documents do not provide sufficient evidence to answer the query",
  "parameters": {
    "explanation": {
      "type": "string",
      "description": "Explanation of why the documents don't provide sufficient evidence"
    },
    "query": {
      "type": "string",
      "description": "The original user query for reference"
    }
  }
}
```
