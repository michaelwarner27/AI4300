# U0-HW-09: Vector Ingestion & Semantic Query Pipeline

A complete vector ingestion and semantic search pipeline built with LangGraph for processing PDF documents, generating embeddings, and enabling intelligent querying through a ReAct agent.

## Overview

**Ingestion Pipeline** - Converts PDF documents into a searchable vector database  
**Query Pipeline** - Accepts user queries and returns evidence-based answers with source citations

---

## Project Structure

```
/U0-HW-09/
├── documents/                       # Source PDFs
├── chroma_db/                       # Vector database storage
├── src/
│   ├── config.py                    # Configuration loader (.env)
│   ├── data_models.py               # Type-safe dataclasses
│   ├── ingestion/
│   │   ├── nodes.py                 # Ingestion node implementations
│   │   ├── tools.py                 # PDFParser, TextChunker, EmbeddingService, ChromaVectorStore
│   │   └── graph.py                 # LangGraph pipeline definition
│   ├── query/
│   │   ├── nodes.py                 # Query node implementations
│   │   ├── tools.py                 # Query-specific tools
│   │   └── graph.py                 # Query pipeline definition
│   └── utils/
│       └── logging.py               # Logging configuration
├── tests/
│   ├── test_queries.py              # Query pipeline test suite
│   ├── verify_ingestion.py          # Ingestion verification
│   └── check_models.py              # Model availability check
├── main.py                          # Ingestion pipeline entry point
├── query_main.py                    # Query pipeline entry point
├── .env.example                     # Configuration template
└── README.md                        # This file
```

---

## Quick Start

### Setup

```bash
cd /home/michael24/4300-CS/U0-HW-09
source .venv/bin/activate
cp .env.example .env
# Edit .env with your API keys and endpoints
```

### Run Ingestion

```bash
python ingestion_main.py
```

Processes all PDFs in `documents/`, generates embeddings, and stores in Chroma.

### Run Queries

```bash
python query_main.py
```

Interactive query interface using the ingested vector database.

### Run Tests

```bash
python tests/test_queries.py          # Execute all required queries
python tests/verify_ingestion.py      # Verify ingestion pipeline
python tests/check_models.py          # Check model availability
```

---

## Ingestion Pipeline (6 Nodes)

1. **load_documents** - Discover all PDFs in `documents/`
2. **parse_documents** - Extract text using pypdf
3. **extract_metadata** - Attach document metadata (filename, author, date)
4. **chunk_text** - Split into 512-char chunks with 50-char overlap
5. **generate_embeddings** - Generate vectors via embedding service API
6. **store_vectors** - Persist to Chroma with metadata indexing

**Features:**
- Real PDF parsing with pypdf
- Batch embedding API calls (32 chunks per request)
- Persistent vector storage in Chroma
- Skip-and-continue error handling
- Comprehensive logging

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

Create `.env` from `.env.example`:

```env
# Embedding Service
EMBEDDING_BASE_URL=http://golem:4000/v1
EMBEDDING_MODEL=Qwen3-Embedding-8B
EMBEDDING_API_KEY=your_api_key

# LLM Service
LLM_BASE_URL=http://golem:4000/v1
LLM_MODEL=Qwen3.8-27B
LLM_API_KEY=your_api_key

# Chunking
CHUNK_SIZE=512
CHUNK_OVERLAP=50

# Vector Database
CHROMA_PERSIST_DIRECTORY=./chroma_db
CHROMA_COLLECTION_NAME=vector_ingestion_collection

# Document Processing
DOCUMENT_INPUT_DIR=./documents
```

**Security:** Never commit `.env` or API keys. Use only on institutional network with HTTP endpoints.

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

- `pypdf` - PDF text extraction
- `langchain` - Text splitting and embeddings
- `langgraph` - Agent workflow orchestration
- `chromadb` - Vector database
- `requests` - HTTP API calls
- `python-dotenv` - Environment configuration

Install with: `pip install -r src/requirements.txt` (if file exists)

---

