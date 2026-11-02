# Query Pipeline Setup and Testing Guide

## Overview

The query pipeline is now fully functional and ready for manual testing. This document summarizes the setup, fixes applied, and how to use the pipeline.

## Issues Fixed

### 1. Model Unavailability (HTTP 403)

**Problem**: The LLM model `Qwen3.8-27B` was not available for the API key.

**Error**:
```
[ERROR] LLM service returned HTTP 403: {"error":{"message":"The requested model 'Qwen3.8-27B' is not available for this API key..."}}
```

**Solution**: Updated `.env` to use `Qwen3.8-Flash-Next` (verified as available)

**Tool Created**: `check_models.py` - Displays all available models from both embedding and LLM services.

**Utility Created**: `update_model.py` - Interactive utility to change the LLM model.

### 2. State Handling Bug

**Problem**: `graph.invoke()` returns a dict, not a QueryState object. This caused:
```
[ERROR] Unexpected error during pipeline execution: 'dict' object has no attribute 'status'
```

**Solution**: Updated `execute_query_pipeline()` to reconstruct QueryState from returned dict.

**File Modified**: `src/query/graph.py` (lines 162-171)

## Available Models

### Embedding Models
- `Qwen3-Embedding-8B` ✓ (currently configured - recommended)
- `Qwen3-Embedding-4B`
- `Qwen3-Embedding-0.6B`
- `Gemma-4-26B-A4B`
- `Qwen3.8-Flash-Next`
- Others

### LLM Models
- `Qwen3.8-Flash-Next` ✓ (currently configured - recommended)
- `Gemma-4-26B-A4B`
- `Clef`
- `Clef-Flash`

## Testing the Pipeline

### Check Available Models

```bash
# Activate virtual environment
source .venv/bin/activate

# Check available models
python3 check_models.py
```

### Update LLM Model (if needed)

```bash
# Run interactive model selection
python3 update_model.py
```

### Single Query Test

```bash
# Standard output
python3 query_main.py "What is in the documents?"

# Verbose output with full details
python3 query_main.py "What is in the documents?" --verbose
# or
python3 query_main.py "What is in the documents?" -v
```

### Interactive Mode

```bash
# Start interactive REPL
python3 query_main.py --interactive
# or
python3 query_main.py -i

# Inside interactive mode, commands:
#   - Type your query and press Enter to execute
#   - Type 'verbose' to toggle verbose output
#   - Type 'clear' to clear screen
#   - Type 'quit' to exit
```

## Successful Test Output

A successful query produces:

```
[INFO] Entering user_query node
[INFO] user_query: Successfully validated query (length: 25 characters)
[INFO] Entering embed_query node
[INFO] embed_query: Successfully embedded query (vector dimension: 4096)
[INFO] Entering vector_store_search node
[INFO] vector_store_search: Retrieved 10 results from vector store
[INFO] Entering generate_response node
[INFO] generate_response: Successfully generated response (1439 characters)
[INFO] Query pipeline completed with status: response_generated
```

### Result Display

Results are displayed in a formatted table showing:

1. **Status**: Pipeline completion status
2. **Query Info**: Original query, validation status, embedding info
3. **Search Results**: Number of documents, source filenames, page numbers, similarity scores
4. **LLM Response**: Generated answer based on retrieved documents
5. **Errors**: Any errors encountered (or "NO ERRORS ENCOUNTERED")

## Pipeline Architecture

### Node Execution Order

```
1. user_query         → Validate input
2. embed_query        → Generate embedding (4096 dims)
3. vector_store_search → Retrieve top 10 similar documents
4. generate_response   → LLM synthesis with source attribution
```

### Key Configuration

| Parameter | Value |
|---|---|
| K (results) | 10 |
| Min Similarity Score | 0.5 |
| Max LLM Tokens | 2048 |
| LLM Temperature | 0.7 |
| Embedding Model | Qwen3-Embedding-8B |
| LLM Model | Qwen3.8-Flash-Next |

## Query Pipeline Features

✅ **Multi-node pipeline**: 4 sequential nodes with conditional routing
✅ **Vector search**: Returns top 10 semantically similar documents
✅ **Similarity filtering**: Filters results by minimum similarity (0.5)
✅ **LLM integration**: Generates responses based on retrieved documents
✅ **Source attribution**: All responses include source filenames and page numbers
✅ **Error resilience**: Accumulates errors without stopping pipeline
✅ **Fallback handling**: Returns documents + error message if LLM fails
✅ **Comprehensive logging**: Tracks each node execution
✅ **Multiple output formats**: Standard and verbose modes

## Troubleshooting

### Model Not Available Error

**Problem**: HTTP 403 error about model not available

**Solution**:
```bash
python3 check_models.py  # See what's available
python3 update_model.py  # Change the model
```

### State Reconstruction Error

**Status**: ✓ Fixed in `src/query/graph.py`

### No Results Found

**Status**: Expected behavior when query doesn't match any documents

**Fallback**: Returns status `"no_results_found"` without calling LLM

### Connection Timeout

**Timeout Settings**:
- Embedding service: 30 seconds
- Vector store: 30 seconds
- LLM service: 120 seconds
- Total pipeline: 2 minutes (120 seconds)

## Files Added/Modified

### New Files
- `query_main.py` - Main entry point for testing
- `check_models.py` - Check available models
- `update_model.py` - Update LLM model
- `src/query/graph.py` - LangGraph definition
- `src/query/nodes.py` - Query pipeline nodes
- `src/query/tools.py` - Service integrations
- `src/query/__init__.py` - Module exports
- `requirements/query-requirements.md` - Detailed specifications

### Modified Files
- `src/data_models.py` - Added QueryState, QueryResult
- `src/ingestion/tools.py` - Added query_similarity() method to ChromaVectorStore
- `.env` - Updated LLM_MODEL to Qwen3.8-Flash-Next

## Next Steps

1. **Test different queries** to see how the pipeline responds
2. **Experiment with parameters** in `src/query/nodes.py`:
   - `DEFAULT_K` (number of results)
   - `DEFAULT_MIN_SIMILARITY_SCORE` (similarity threshold)
   - `SYSTEM_PROMPT` (LLM behavior)
3. **Monitor logs** to see pipeline execution details
4. **Tune similarity threshold** based on document quality
5. **Try verbose mode** to see all retrieved documents

## Example Queries to Test

```bash
python3 query_main.py "What food safety topics are covered?"
python3 query_main.py "Tell me about employee health guidelines"
python3 query_main.py "What are the appendices about?"
python3 query_main.py "How should employees maintain hygiene?"
```

## Interactive Testing Session Example

```bash
$ python3 query_main.py -i
================================================================================
QUERY PIPELINE - INTERACTIVE MODE
================================================================================

Enter query (or command): What is food safety?
[Pipeline executes, results displayed]

Enter query (or command): verbose
Verbose mode: ON

Enter query (or command): Tell me about employee health
[Detailed results displayed]

Enter query (or command): clear
[Screen cleared]

Enter query (or command): quit
Exiting interactive mode...
```
