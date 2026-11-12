# HaRVey: Vector Ingestion & Semantic Query Pipeline

A complete vector ingestion and semantic search pipeline built with LangGraph for processing PDF documents, generating embeddings, and enabling intelligent querying through a ReAct agent.

## Overview

**Ingestion Pipeline** - Converts PDF documents into a searchable vector database  
**Query Pipeline** - Accepts user queries and returns evidence-based answers with source citations

---

## Project Structure

```
/HaRVey/
├── documents/                       # Source PDFs
├── chroma_db/                       # Vector database storage
├── extracted_diagrams/              # Extracted images from PDFs
├── src/
│   ├── config.py                    # Configuration loader (.env)
│   ├── data_models.py               # Type-safe dataclasses
│   ├── ingestion/
│   │   ├── nodes.py                 # Ingestion node implementations (6 pipeline + 3 management)
│   │   ├── tools.py                 # Tool classes (DocumentHasher, TableExtractor, etc.)
│   │   └── graph.py                 # LangGraph pipeline definition
│   ├── query/
│   │   ├── nodes.py                 # Query node implementations
│   │   ├── tools.py                 # Query-specific tools
│   │   └── graph.py                 # Query pipeline definition
│   └── utils/
│       └── logging.py               # Logging configuration
├── tests/
│   ├── test_queries.py              # Query pipeline test suite
│   ├── verify_ingestion.py          # Ingestion pipeline verification (no embedding server needed)
│   └── check_models.py              # Model availability check
├── ingestion_main.py                # Ingestion pipeline entry point
├── query_main.py                    # Query pipeline entry point
├── manage_documents.py              # CLI tool for document management
├── example.env.example              # Configuration template (copy to .env)
├── requirements.txt                 # Python dependencies
└── README.md                        # This file
```

---

## Quick Start

### Setup

```bash
cd /home/michael24/4300-CS/HaRVey
source .venv/bin/activate
pip install -r requirements.txt
cp example.env.example .env
# Edit .env with your API keys and endpoints
```

### Run Ingestion

```bash
python ingestion_main.py
```

Processes all PDFs in `documents/`, generates embeddings, and stores in Chroma.

**Automatic change detection:**
- First run: All documents processed and stored with their file hash
- Subsequent runs: Unchanged documents automatically skipped
- Disable with: `ENABLE_CHANGE_DETECTION=false`

### Manage Ingested Documents

After ingestion, use the CLI tool to list, delete, or update documents:

```bash
# List all documents
python manage_documents.py --list

# Delete a document by ID (shown in --list) or by filename
python manage_documents.py --delete a1b2c3d4
python manage_documents.py --delete outdated.pdf

# Prepare document for re-ingestion
python manage_documents.py --update ./documents/report.pdf
```

See [Document Management](#document-management) section for details.

### Run Queries

```bash
python query_main.py
```

Interactive query interface using the ingested vector database.

### Run Tests

```bash
python tests/test_queries.py          # Execute all required queries
python tests/verify_ingestion.py      # Self-contained ingestion checks (fixture PDF, temp Chroma, fake embeddings)
python tests/verify_ingestion.py --live   # ...plus a sanity check of your real Chroma database
python tests/check_models.py          # Check model availability
```

---

## Ingestion Pipeline (6 Nodes)

1. **load_documents** - Discover all PDFs, calculate hashes, detect changes
2. **parse_documents** - Extract text, tables, and diagrams
3. **extract_metadata** - Extract headings, timestamps, PDF metadata
4. **chunk_text** - Split into 512-char chunks with 50-char overlap
5. **generate_embeddings** - Generate vectors via embedding service with retry logic
6. **store_vectors** - Persist to Chroma with metadata indexing

**Features:**
- Real PDF parsing with pypdf + pdfplumber
- Table extraction (pdfplumber) and diagram extraction (embedded images via pypdf, vector figures rendered via PyMuPDF with text captions)
- File hash-based change detection (skips unchanged documents)
- Automatic retry with exponential backoff (3 attempts)
- Batch embedding API calls (32 chunks per request)
- Real-time progress bars
- Persistent vector storage in Chroma
- Skip-and-continue error handling
- Comprehensive logging with detailed metrics

**Change Detection:**
- First run: Processes all PDFs; each chunk is stored in Chroma with its file's SHA256 hash
- Subsequent runs: Automatically skips unchanged documents
- Can be disabled with `ENABLE_CHANGE_DETECTION=false`

---

## Query Pipeline (5 Nodes)

1. **user_query** - Validate and prepare user input
2. **embed_query** - Generate embedding for query
3. **vector_store_search** - Retrieve top-K similar documents (K=10)
4. **generate_response** - LLM with tool calling to synthesize answer
5. **process_tool_call** - Format and return response with sources

**Features:**
- Semantic similarity search
- LLM-powered response synthesis
- Tool calling for structured output
- Source attribution and evidence-based answers
- Fallback handling for edge cases

**Available Tools:**
- `respond_to_user` - Return answer with sources
- `insufficient_evidence` - Indicate lack of supporting evidence

---

## Configuration

Create `.env` from `example.env.example`:

```env
# Embedding Service (required)
EMBEDDING_BASE_URL=http://golem:4000/v1
EMBEDDING_MODEL=Qwen3-Embedding-8B
EMBEDDING_API_KEY=your_api_key

# Retry Logic (optional, defaults shown)
EMBEDDING_MAX_RETRIES=3
EMBEDDING_RETRY_BACKOFF_BASE=2.0

# Table Extraction (optional, defaults shown)
ENABLE_TABLE_EXTRACTION=true

# Change Detection (optional, defaults shown)
ENABLE_CHANGE_DETECTION=true
CHANGE_DETECTION_LOG_LEVEL=critical

# Progress Tracking (optional, defaults shown)
SHOW_PROGRESS_BAR=true

# LLM Service (optional, for query pipeline)
LLM_BASE_URL=http://golem:4000/v1
LLM_MODEL=Qwen3.8-27B
LLM_API_KEY=your_api_key

# Chunking (optional, defaults shown)
CHUNK_SIZE=512
CHUNK_OVERLAP=50

# Vector Database (optional, defaults shown)
CHROMA_PERSIST_DIRECTORY=./chroma_db
CHROMA_COLLECTION_NAME=vector_ingestion_collection

# Document Processing (optional, defaults shown)
DOCUMENT_INPUT_DIR=./documents
```

**Security:** Never commit `.env` or API keys. Use only on institutional network with HTTP endpoints.

**Change Detection Details:**
- First run: Processes all PDFs; the SHA256 hash is stored on each chunk in Chroma (no separate hash file)
- Subsequent runs: Compares the file hash against Chroma and skips unchanged documents
- Documents are stored all-or-nothing: if any chunk fails embedding or storage, nothing is saved for that file, so it is retried next run
- Unchanged files logged as warnings (configurable with `CHANGE_DETECTION_LOG_LEVEL`)

---

## Document Management

After documents are ingested, use the CLI tool to manage them:

### List Documents

View all documents currently in the vector database:

```bash
python manage_documents.py --list
```

Output:
```
====================================================================================================
ID         Document Name                                Chunks   Ingested At                   
====================================================================================================
a1b2c3d4   report.pdf                                   245      2026-11-12T10:30:45.123456
e5f6a7b8   whitepaper.pdf                               512      2026-11-12T10:35:12.789012
====================================================================================================

Total: 2 document(s) in vector database
Delete with: python manage_documents.py --delete <ID or filename>
```

### Delete Document

Remove a document and all its vectors from Chroma. Use the ID from `--list` (handy for long filenames) or the filename itself:

```bash
python manage_documents.py --delete a1b2c3d4
python manage_documents.py --delete report.pdf
```

Output:
```
Document: report.pdf
ID:       a1b2c3d4
Chunks:   245
Delete this document and all its vectors? This cannot be undone. (y/n): y

Success! Deleted 245 vectors for 'report.pdf'
```

### Update Document

Delete old vectors and prepare for re-ingestion:

```bash
python manage_documents.py --update ./documents/report.pdf
```

Output:
```
Preparing to update document: report.pdf
Delete old vectors and prepare for re-ingestion? (y/n): y

Success! Deleted 245 old vectors for 'report.pdf'

Next steps:
  1. Modify report.pdf if needed
  2. Ensure the file is in ./documents/
  3. Run: python ingestion_main.py
```

**Workflow for updating a document**:
1. Run the delete/prepare command above
2. Modify the PDF file (optional)
3. Run `python ingestion_main.py` to re-ingest
4. Change detection automatically detects the modification

For detailed usage and options, see [Post-Ingestion Document Management](./requirments/ingestion.md#post-ingestion-document-management) in ingestion.md.

---


## Architecture Overview

### Data Flow

```
PDFs → Load → Parse → Metadata → Chunk → Embed → Store (Chroma)
                                           ↓
Query → Embed → Search (Chroma) → LLM (Tool Calling) → Format Response
```

### State Management

Both pipelines use LangGraph's state management to flow data through nodes with error tracking and progress logging.

### Error Handling

- **Ingestion:** Skip-and-continue pattern (individual failures don't stop pipeline)
- **Query:** Graceful degradation with fallback responses and error details

---

## Dependencies

- `pypdf` - PDF text extraction and embedded image extraction
- `pdfplumber` - Table extraction
- `pymupdf` - Vector figure detection and rendering (AGPL-3.0 licensed)
- `langchain` - Text splitting and embeddings
- `langgraph` - Agent workflow orchestration
- `chromadb` - Vector database
- `requests` - HTTP API calls
- `python-dotenv` - Environment configuration

Install with: `pip install -r requirements.txt`

---

