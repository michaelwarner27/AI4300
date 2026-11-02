# Agent Report

## Repository Information
**Name:** Michael Warner

**Repository URL:** `git@github.com:michaelwarner27/AI4300.git`

**Commit Hash:**

## Agent Description
### Task
These agents allow a user to get answers to their questions based off the document base. This ensures that the user is receiving information accurate to the documents they're looking at. The first agent creates a semantic store of the given documents. The second agent takes a user query and retrieves relevant chunks of data to synthesis a response based on the documents. 

### Tools
<!-- List and describe the tools available to the agent -->
- respond_to_user - returns the agent's answer along with the sources it used
- insufficient_evidence - returns a message notifying the user their question could not be definitively answered, but it also returns the chunks the semantic store query returned.

## Vector Store Design
### Architecture
<!-- Describe the vector store implementation and structure -->
The vector store uses chroma_db to store all of our needed information. Chroma uses sqlite3 as its base.

```
PDF Documents (./documents/)
    ↓
Load Documents (discover PDFs + extract metadata)
    ↓
Parse Documents (extract text with pypdf)
    ↓
Chunk Text (fixed-size overlap chunking)
    ↓
Generate Embeddings (call embedding service)
    ↓
Store Vectors (persist to Chroma)
```

### Chunking Strategy
<!-- Explain how documents are chunked for embedding -->
I used a fixed chunking strategy for simplicity. I'm using 512 character chunks with 50 character overlap.
### Embedding Model
<!-- Specify which embedding model is used -->
Qwen3-Embedding-8B
## Source Attribution Method
<!-- Describe how sources are attributed in agent responses -->
When the top K answers are returned from the semantic store query, the meta data is passed with it so the agent can reference document and page number of the used source.

## Query Loop Design

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
## Required Query Outcomes
<!-- Document the expected outcomes for queries (e.g., "supported" or "insufficient-evidence") -->

| Query | Expected Outcome | Actual Outcome | Notes |
|-------|------------------|-----------------|-------|
|1 | Supported | Supported | Good response, it some of the references are cut off because of our chunking, so a future iteration would include a tool to gather that additional context. |
|2 | Supported | Supported | Very nice, gave step by step action|
|3 | Supported | Insufficient-Evidence | This one likely could have given a partial answer with the chunks received but chose to return insufficient information. |
|4 | Supported | Supported | Gave a nice table for compare contrast |
|5 | Supported | Insufficient-Evidence | Again the chunks cut off the data the llm actually needed to answer the question |
|6 | Supported | Supported | Good explanation |
|7 | Supported | Supported | Good |
|8| Insufficient-Evidence  | Insufficient-Evidence  | Accurately recognized it didn't have the need information |
|9 | Insufficient-Evidence | Insufficient-Evidence | Good |

### Summary
<!-- Overall summary of test execution -->
Most of the tests worked as expected but 2 of them (3 and 5) didn't. From what I can tell it's because they chunks that were returned from the semantic store query led up to the information the llm needed to answer the questions. This could be remedied by larger chuck sizes, or a llm tool that allows it to grab more chunks of data.

## Observed Limitations
<!-- Describe any limitations encountered during testing -->
Like I said above it would be a good addition to allow the agent to grab context from adjacent chunks to the ones that were returned. 
Also a tool to return more chunks after the original k were returned would allow the agent to asses if it needs more information and grab it.

