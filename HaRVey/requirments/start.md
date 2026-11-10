# Getting Started with the Vector Ingestion & Query System

This guide will help you set up and use the vector ingestion and query pipelines. For more detailed information, see `ingestion.md` and `query.md`.

## Prerequisites

- Python 3.9+
- Virtual environment (recommended)
- `.env` file with required configuration
- PDF documents for ingestion (if using ingestion pipeline)

---

## Setup

### 1. Create Virtual Environment

```bash
cd /home/michael24/4300-CS/U0-HW-09
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r src/requirements.txt
```

Dependencies include:
- `pypdf` - PDF text extraction
- `langchain` - Text splitting and embeddings
- `langgraph` - Workflow orchestration
- `chromadb` - Vector database
- `python-dotenv` - Configuration loading
- `requests` - HTTP client

### 3. Configure Environment

Create or update `.env` file in project root:

```bash
# Embedding Service Configuration
EMBEDDING_BASE_URL=http://golem:4000/v1
EMBEDDING_MODEL=Qwen3-Embedding-8B
EMBEDDING_API_KEY=your_api_key

# LLM Service Configuration
LLM_BASE_URL=http://golem:4000/v1
LLM_MODEL=Qwen3.8-Flash-Next
LLM_API_KEY=your_api_key

# Chunking Configuration
CHUNK_SIZE=512
CHUNK_OVERLAP=50

# Vector Database
CHROMA_PERSIST_DIRECTORY=./chroma_db
CHROMA_COLLECTION_NAME=vector_ingestion_collection

# Document Processing
DOCUMENT_INPUT_DIR=./documents
```

**Required fields**: All embedding and LLM variables must be filled in.

### 4. Prepare Documents (for Ingestion)

Create a `documents/` directory and add PDF files:

```bash
mkdir -p ./documents
# Copy your PDF files here
cp /path/to/your/files/*.pdf ./documents/
```

---

## Quick Reference

### Ingestion Pipeline

Process PDFs and load them into the vector database:

```bash
python main.py
```

**Output**:
- Configuration loads from `.env`
- All 6 nodes execute: load → parse → extract metadata → chunk → embed → store
- Summary shows:
  - Documents loaded
  - Chunks created
  - Embeddings generated
  - Vectors stored
  - Any errors

**Time**: Depends on document volume and embedding service performance

### Query Pipeline

Ask questions about your ingested documents:

```bash
# Single query
python3 query_main.py "What topics are covered in the documents?"

# Verbose mode (shows all retrieved documents)
python3 query_main.py "Your question here" -v

# Interactive mode (REPL)
python3 query_main.py -i
```

**Output**:
- Status of pipeline execution
- Retrieved documents with similarity scores
- LLM-synthesized answer with source citations
- Any errors encountered

**Time**: 5-30 seconds depending on network latency

---

## Pipeline Overview

### Ingestion Pipeline (Batch)

**What it does**: Processes a batch of PDF documents and converts them into searchable embeddings.

**Steps**:
1. **Load Documents** - Discover PDFs in `./documents/` directory
2. **Parse Documents** - Extract text from PDFs using pypdf
3. **Extract Metadata** - Attach source information (filename, creation date, author)
4. **Chunk Text** - Split text into 512-character overlapping chunks
5. **Generate Embeddings** - Call embedding service API to create vectors
6. **Store Vectors** - Persist embeddings to Chroma database

**When to run**: Once after adding new documents, or when re-indexing

**Output location**: `./chroma_db/` (persistent vector database)

### Query Pipeline (Interactive)

**What it does**: Answers questions by searching the vector database and using an LLM to synthesize responses.

**Steps**:
1. **Validate Query** - Ensure user input is valid
2. **Embed Query** - Convert query string to vector
3. **Search Database** - Find top 10 similar documents
4. **Generate Response** - Use LLM to synthesize answer from documents
5. **Format Output** - Display answer with source citations

**When to run**: Whenever you want to ask a question about ingested documents

**Prerequisites**: Ingestion pipeline must be run first to populate the database

---

## Common Tasks

### Add New Documents

```bash
# 1. Copy PDFs to documents directory
cp /path/to/new/files/*.pdf ./documents/

# 2. Re-run ingestion pipeline
python main.py

# 3. Verify new documents are indexed
python3 query_main.py "Your test query" -v
```

### Check Available Models

```bash
python3 check_models.py
```

Lists all embedding and LLM models available from your configured services.

### Change the LLM Model

```bash
python3 update_model.py
```

Interactive utility to select and configure a different LLM model.

### Run Query in Verbose Mode

```bash
python3 query_main.py "Your question" -v
```

Shows all retrieved documents with longer content snippets for debugging.

### Interactive Query REPL

```bash
python3 query_main.py -i
```

Commands:
- Type your question and press Enter
- Type `verbose` to toggle detailed output
- Type `clear` to clear screen
- Type `quit` to exit

---

## Expected Output

### Successful Ingestion

```
[INFO] Starting vector ingestion pipeline
[INFO] Loading configuration from .env
[INFO] Configuration loaded: embedding_model=Qwen3-Embedding-8B
[INFO] Executing pipeline...
[INFO] Starting load_documents node
[INFO] load_documents: Found 2 documents
[INFO] Starting parse_documents node
[INFO] parse_documents: 2/2 successfully parsed
[INFO] Starting extract_metadata node
[INFO] extract_metadata: Metadata extracted for 2 documents
[INFO] Starting chunk_text node
[INFO] chunk_text: Created 6 chunks from 2 documents
[INFO] Starting generate_embeddings node
[INFO] generate_embeddings: 6/6 successfully embedded
[INFO] Starting store_vectors node
[INFO] store_vectors: Stored 6 vectors in Chroma

============================================================
INGESTION PIPELINE SUMMARY
============================================================
Status: pipeline_complete
Documents Loaded: 2
Documents Parsed: 2
Chunks Created: 6
Embeddings Generated: 6
Vectors Stored: 6

No errors encountered
============================================================
```

### Successful Query

```
[INFO] Entering user_query node
[INFO] user_query: Successfully validated query (length: 25 characters)
[INFO] Entering embed_query node
[INFO] embed_query: Successfully embedded query (vector dimension: 4096)
[INFO] Entering vector_store_search node
[INFO] vector_store_search: Retrieved 10 results from vector store
[INFO] Entering generate_response node
[INFO] generate_response: Successfully generated response

Status: response_generated

Answer:
The documents cover several key topics including food safety protocols,
employee health requirements, and maintenance procedures...

Sources:
- food_safety.pdf (page 3)
- employee_guide.pdf (page 7)
```

---

## Troubleshooting

### ".env file not found"

**Solution**: Create `.env` in project root with required variables
```bash
cd /home/michael24/4300-CS/U0-HW-09
# Create/edit .env file with configuration
```

### "Missing required environment variables"

**Solution**: Verify all variables are set in `.env`
```bash
# Check that these exist and have non-empty values:
grep -E "EMBEDDING_|LLM_|CHUNK_|CHROMA_|DOCUMENT_" .env
```

### "No documents found" during ingestion

**Solution**: Ensure PDFs exist in `./documents/` directory
```bash
mkdir -p ./documents
ls ./documents/  # Should show .pdf files
```

### "Connection refused" errors

**Solution**: Verify services are running and accessible
```bash
# Check embedding service
curl http://golem:4000/v1/models

# Check LLM service
curl http://golem:4000/v1/models
```

### "Model not available" or "API key rejected"

**Solution**: Check and update model configuration
```bash
python3 check_models.py          # List available models
python3 update_model.py          # Change model interactively
```

### "Chroma database error"

**Solution**: Verify database directory is writable
```bash
# Check permissions
ls -la ./chroma_db/

# Or reset database
rm -rf ./chroma_db/
python main.py  # Recreate database
```

---

## Configuration Reference

### For Ingestion Pipeline

| Variable | Purpose | Example |
|----------|---------|---------|
| `EMBEDDING_BASE_URL` | Embedding service endpoint | `http://golem:4000/v1` |
| `EMBEDDING_MODEL` | Embedding model to use | `Qwen3-Embedding-8B` |
| `EMBEDDING_API_KEY` | API key for embedding service | `your_key_here` |
| `CHUNK_SIZE` | Characters per chunk | `512` |
| `CHUNK_OVERLAP` | Character overlap between chunks | `50` |
| `CHROMA_PERSIST_DIRECTORY` | Where to store vectors | `./chroma_db` |
| `CHROMA_COLLECTION_NAME` | Vector collection name | `vector_ingestion_collection` |
| `DOCUMENT_INPUT_DIR` | Where to find PDFs | `./documents` |

### For Query Pipeline

| Variable | Purpose | Example |
|----------|---------|---------|
| `LLM_BASE_URL` | LLM service endpoint | `http://golem:4000/v1` |
| `LLM_MODEL` | LLM model to use | `Qwen3.8-Flash-Next` |
| `LLM_API_KEY` | API key for LLM service | `your_key_here` |

---

## Performance Tips

### Ingestion Pipeline

- **Batch Size**: Embeddings are batched in groups of 32 for efficiency
- **Chunk Size**: Larger chunks (1024+) reduce number of API calls; smaller chunks (256) preserve detail
- **Parallel Processing**: Currently sequential; consider LangGraph's graph optimization for future versions

### Query Pipeline

- **Top-K Results**: Default is 10; increase for broader context, decrease for precision
- **Similarity Threshold**: Default 0.5; increase to 0.7+ for higher quality matches
- **LLM Temperature**: Default 0.7; increase for more creative answers, decrease for consistency

See configuration in `src/query/nodes.py` for tunable parameters.

---

## Next Steps

1. **Read detailed documentation**:
   - `ingestion.md` - Full ingestion pipeline details
   - `query.md` - Full query pipeline details
   - `requirements/QUICK_START.md` - Quick reference guide

2. **Explore the codebase**:
   - `src/ingestion/nodes.py` - Ingestion pipeline implementation
   - `src/query/nodes.py` - Query pipeline implementation
   - `src/data_models.py` - Data structures

3. **Experiment**:
   - Try different queries to understand search quality
   - Add new documents and re-index
   - Adjust configuration parameters in `.env`
   - Check verbose mode to see all retrieved documents

4. **Extend the system**:
   - Add more node preprocessing steps
   - Implement different chunking strategies
   - Integrate additional embedding models
   - Add multi-language support

---

## Getting Help

**Check logs** for detailed error information:
- Ingestion logs print to console during `python main.py`
- Query logs print during `python3 query_main.py "query"`
- Use verbose flag `-v` for additional debugging: `query_main.py "query" -v`

**Review configuration**:
- Verify `.env` has all required variables
- Check `src/config.py` for expected configuration structure
- Use `check_models.py` to verify service connectivity

**Review node implementations**:
- `src/ingestion/nodes.py` for ingestion steps
- `src/query/nodes.py` for query steps
- `src/ingestion/tools.py` and `src/query/tools.py` for service integrations
