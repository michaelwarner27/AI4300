# Tool Calling Implementation Decisions

## Summary of All Decisions

### 1. Response Format - DECIDED ✓
**Decision**: Optional source inclusion. Sources trail the synthesized response.

**Details**:
- `respond_to_user` tool has `include_sources` parameter (boolean, default: true)
- **If include_sources=true**: Answer followed by sources section
  ```
  [Synthesized Answer]
  
  ---
  
  Sources:
  - filename.pdf (page 5): [snippet of content]
  - filename2.pdf (page 12): [snippet of content]
  ```
- **If include_sources=false**: Only the answer text is returned
- User can control this per query

---

### 2. Insufficient Evidence Response - DECIDED ✓
**Decision**: Message + Retrieved documents always shown

**Format**:
```
⚠️ Insufficient Information

[LLM explanation of why evidence is insufficient]

However, here are the retrieved documents that may be relevant:

Document 1:
  Source: filename.pdf (page X)
  Snippet: [content preview]
  
Document 2:
  Source: filename.pdf (page Y)
  Snippet: [content preview]
```

**Behavior**:
- Show LLM's explanation
- Always display retrieved documents even when marked insufficient
- No source filtering - show all documents returned by vector search

---

### 3. Source Information - DECIDED ✓
**Decision**: Filename, page, and snippet only

**Included in sources**:
- `filename` - PDF filename
- `page` - Page number
- `snippet` - Content preview (max 200 chars)

**NOT included**:
- chunk_sequence
- similarity_score
- Character indices
- Full document content

---

### 4. LLM Behavior - DECIDED ✓
**Decision**: Prefer `respond_to_user`

**System Prompt Guidance**:
- "Prefer to provide an answer if the documents contain relevant information"
- "Only use insufficient_evidence if the documents clearly do not address the query"
- "Err on the side of providing an answer rather than saying there's insufficient evidence"

**Effect**:
- LLM biased toward answering questions
- Only chooses insufficient_evidence when truly no relevant docs
- Reduces false "no answer found" responses

---

### 5. Error Handling - DECIDED ✓
**Decision**: Retry up to 3 times, then fallback

**Retry Logic**:
- If LLM tool call fails to parse or has invalid structure
- Retry up to 3 times with same query
- Log each retry with error details

**After 3 failures**:
```
[ERROR] LLM tool call generation failed after 3 retries

Here are the retrieved documents that may be relevant:

[Display all retrieved documents with snippets]
```

**Status**: `response_generated_with_errors`

---

### 6. Parameter Validation - DECIDED ✓
**Decision**: Trust LLM output as-is

**Validation Strategy**:
- Only validate JSON parsing of tool call
- Accept any string value for `answer`
- Accept any array for `sources` (even empty)
- Accept any string for `snippet`
- Accept any value for `explanation`

**No validation of**:
- Empty answers
- Empty sources list
- Blank snippets
- Missing optional fields

**Rationale**:
- Trust LLM to return sensible data
- Failing fast on validation errors could degrade user experience
- Better to return what LLM provides than retry unnecessarily

---

## Updated Tool Schemas

### Tool 1: respond_to_user

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

### Tool 2: insufficient_evidence

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

---

## Implementation Checklist

- [ ] Update `generate_response` node to use tool calling
- [ ] Implement retry logic (up to 3 attempts)
- [ ] Create `process_tool_call` node
- [ ] Implement `respond_to_user` handler
- [ ] Implement `insufficient_evidence` handler
- [ ] Update QueryState to track tool calls
- [ ] Update response formatters
- [ ] Test with actual queries

---

## System Prompt (Final Version)

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

---

## Key Benefits of This Approach

1. **Explicit Control**: LLM explicitly chooses between answering and deferring
2. **Structured Output**: Tool calls provide reliable, parseable responses
3. **User Flexibility**: Can request answers without sources
4. **Resilience**: Retry logic handles transient LLM failures
5. **Transparency**: Sources always available, fallback shows all retrieved docs
6. **Trust**: No over-validation of LLM output
7. **Better UX**: Prefer answering over saying "no evidence"

---

## Status

✅ All 6 decisions documented
✅ Tool schemas finalized
✅ System prompt updated
✅ Error handling strategy defined
✅ Ready for implementation
