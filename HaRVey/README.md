# HaRVey: Vector Ingestion & Semantic Query Pipeline

A complete vector ingestion and hybrid search pipeline (semantic + keyword + exact part-number matching) built with LangGraph for processing PDF documents, generating embeddings, and enabling intelligent querying through a ReAct-style agent. The agent loop is hand-built as LangGraph nodes (`generate_response` ⇄ `search_more`) rather than LangGraph's prebuilt `create_react_agent`: the LLM reasons, calls a search tool, observes the results and repeats until it gives a final answer.

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
│   ├── config.py                    # Configuration loader (.env), used by both pipelines
│   ├── data_models.py               # Type-safe dataclasses (ingestion records, QueryState, ChatTurn)
│   ├── ingestion/
│   │   ├── nodes.py                 # Ingestion node implementations (6 pipeline + 3 management)
│   │   ├── tools.py                 # Tool classes (PDF parsing, TextChunker, EmbeddingService, ChromaVectorStore, extractors)
│   │   ├── layout.py                # PyMuPDF layout analysis: reading order, headings, sections
│   │   └── graph.py                 # LangGraph pipeline definition
│   ├── query/
│   │   ├── nodes.py                 # Query nodes, system prompt, tool schemas, response builders
│   │   ├── tools.py                 # LLMService (OpenAI-compatible chat completions)
│   │   ├── lexical.py               # BM25 + exact part-number index for hybrid search
│   │   └── graph.py                 # Query pipeline definition
│   └── utils/
│       ├── logging.py               # Logging configuration
│       └── part_numbers.py          # Part / model / fault-code extraction (ingestion + query)
├── web/
│   ├── app.py                       # Flask app (query form, response, diagram images)
│   └── templates/index.html         # Page template
├── tests/
│   ├── test_queries.py              # Query pipeline test suite
│   ├── verify_query_loop.py         # Query agent-loop verification (no servers needed)
│   └── verify_ingestion.py          # Ingestion pipeline verification (no embedding server needed)
├── ingestion_main.py                # Ingestion pipeline entry point
├── query_main.py                    # Query pipeline entry point
├── web_main.py                      # Web app entry point
├── manage_documents.py              # CLI tool for document management
├── requirements.txt                 # Python dependencies
├── example.env.example              # Configuration template (copy to .env)
├── requirements/                    # Design specs (ingestion.md, query.md, hybrid_search.md, start.md)
├── flaws.md, TODO                   # Known issues and ingestion backlog
├── HW-Reports/                      # Course reports and the technical audit
└── README.md                        # This file
```

---

## Quick Start

### Setup

Requires Python 3.10 or newer.

```bash
cd HaRVey
python -m venv .venv            # first time only
source .venv/bin/activate
pip install -r requirements.txt
cp example.env.example .env
# Edit .env with your API keys and endpoints (LLM_* settings are required for querying)
```

Run every command from the project root; the scripts import the `src` package from the current directory.

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

### Run Web App

```bash
python web_main.py            # then open http://127.0.0.1:5000
```

Browser chat UI: ask follow-up questions in a conversation thread. Answers render as markdown (marked + DOMPurify from cdnjs; plain text if they can't load), steps are grouped by stage, diagrams show as captioned images, sources sit in a collapsible list, clarifying questions offer clickable options, and error details are collapsed. Each browser session has its own conversation (kept in server memory, cleared on restart or with **New chat**). Optionally set `FLASK_SECRET_KEY` in `.env` to keep session cookies valid across restarts.

### Run Tests

```bash
python tests/test_queries.py          # Run and grade the live cases in tests/test-queries.md (format described there)
python tests/test_queries.py --only C,RW01 --repeat 3   # Chosen cases or groups, with pass rates
python tests/test_queries.py --rewrite-only             # Grade only the follow-up query rewrite
python tests/build_corpus.py --copy   # Copy the test manuals (tests/corpus-manifest.txt) into documents/
python tests/verify_ingestion.py      # Self-contained ingestion checks (fixture PDF, temp Chroma, fake embeddings)
python tests/verify_ingestion.py --live   # ...plus a sanity check of your real Chroma database
python tests/verify_query_loop.py     # Offline checks of the query agent loop and hybrid search (part numbers, BM25 blend)
```

---

## Ingestion Pipeline (6 Nodes)

1. **load_documents** - Discover all PDFs, calculate hashes, detect changes
2. **parse_documents** - Extract text (pypdf), tables (pdfplumber) and diagrams
3. **extract_metadata** - PyMuPDF layout analysis: column-aware reading order, font repair, headings from font styling, text split into sections; heading path and context for each diagram and table
4. **chunk_text** - Split each section separately into chunks of up to 1000 characters with 100-character overlap (`CHUNK_SIZE`, `CHUNK_OVERLAP`). Every chunk starts with a `Section: A > B` line. If layout analysis finds no sections, each page becomes one section. Tables and diagrams get one chunk each. Every chunk gets `part_numbers` metadata: model / part / fault codes from its text and its manual's filename
5. **generate_embeddings** - Generate vectors via embedding service with retry logic
6. **store_vectors** - Persist to Chroma with metadata indexing

**Features:**
- PDF parsing with PyMuPDF layout text (pypdf text is used only when layout analysis finds no text); PyMuPDF is required
- Table extraction (pdfplumber) and diagram extraction (embedded images and vector figures rendered via PyMuPDF, with text captions)
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

## Query Pipeline (8 Nodes)

1. **user_query** - Validate and prepare user input
2. **rewrite_query** - With chat history, one LLM call rewrites a follow-up ("what about step 3?") into a standalone search query; skipped for the first question, falls back to the original on failure
3. **embed_query** - Generate embedding for the (rewritten) query
4. **vector_store_search** - Hybrid search for the top-K documents (K=10): dense similarity blended with BM25, plus a boost and a guaranteed place for chunks containing a specific part number from the query (`457915.70X`, `8330-733`, `E1`); see [requirements/hybrid_search.md](./requirements/hybrid_search.md). A chunk is kept when its cosine or blended score reaches `SEARCH_MIN_SIMILARITY` (default 0.5). Also adds neighbor chunks and the diagrams on the top hits' pages (max 8, same section first) so the agent has figures to show. If nothing passes the cutoff and there are no earlier-turn documents, the run ends without an LLM call (`response` is `None`; the web app shows "No relevant documents found.")
5. **add_history_documents** - Carry over the documents cited in the last 2 answers (max 30, marked `earlier_turn`). "Cited" means chunks matching a `sources` entry's filename + page, plus diagrams shown via `diagram_refs`; `insufficient_evidence` turns carry none
6. **generate_response** - One LLM turn with tool calling (temperature 0.7, `max_tokens` 50000 for reasoning models); the last 5 Q&A turns are sent before the current question
7. **search_more** - Agent loop: runs the LLM's `search_documents`, `read_section` or `read_page` call, adds new chunks and returns to `generate_response`. Searching stops after 3 rounds or 300 s, then the LLM must give a final answer
8. **process_tool_call** - Format and return response with sources

**Conversation:** `execute_query_pipeline(query, config, graph, chat_history=[...])` accepts earlier turns as `ChatTurn(question, answer, kind, documents)` (see `src/data_models.py`); build each with `chat_turn_from_result(question, result)` from `src/query/graph.py`, which keeps clean answer text (no sources or paths) and the cited documents, and returns `None` for failed or empty turns so they are not remembered. Past answers are replayed to the LLM as tool calls (`respond_to_user` / `insufficient_evidence` / `ask_clarifying_question`) with a tool result, not as plain assistant text. Limits are the `MAX_HISTORY_TURNS`, `HISTORY_DOC_TURNS` and `MAX_HISTORY_DOCS` constants in `src/query/nodes.py`. The CLI (`query_main.py`) still answers each query independently.

**Features:**
- Hybrid search: semantic similarity + BM25 keywords + exact part-number matching (`rank_bm25`), used by the first retrieval and `search_documents`
- LLM-powered response synthesis
- Tool calling for structured output
- Source attribution and evidence-based answers
- Fallback handling for edge cases

**Available Tools:**
- `search_documents` - Fetch more context from the vector store (non-terminal; the agent continues afterwards)
- `read_section` - Read every passage of a document section (and its sub-sections) in reading order (non-terminal; shares the 3-round budget)
- `read_page` - Read every passage on a page range in reading order (non-terminal; shares the 3-round budget with `search_documents`)
- `respond_to_user` - Final answer: `answer` (markdown allowed), `sources` (always shown; snippets cut to 200 chars, duplicates dropped), optional `steps` (each may carry a `stage`, grouped in the output) and required `diagram_refs` (may be empty). The agent is told to include relevant diagrams with every answer (max 4, `MAX_DIAGRAMS_PER_ANSWER`); if it picks none, diagrams on the cited pages are attached automatically, same section first
- `insufficient_evidence` - Final reply when the documents don't cover the question (after searching)
- `ask_clarifying_question` - Final reply asking which model/series/document is meant, with up to 4 `options`, when the documents give materially different answers

**Response:** `process_tool_call` stores a structured `QueryState.response` (`kind`, `answer`, `steps`, `sources`, `diagrams`, `options`); `final_response` is its text rendering. Replies without a tool call, and tool calls the server can't parse (e.g. LiteLLM HTTP 500 "unrecoverable tool call"), are retried once with a matching reminder at a higher temperature (0.9 vs 0.7); plain text is used as the answer if a plain-text retry also fails. The web app logs agent details to `logs/harvey.log`.

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

# Similarity cutoff for the first retrieval AND the agent's search_documents tool
# (optional, default 0.5)
SEARCH_MIN_SIMILARITY=0.5

# Hybrid search (optional, defaults shown; see requirements/hybrid_search.md)
HYBRID_ALPHA=0.6        # weight of cosine vs BM25 in the blended score
HYBRID_CANDIDATES=30    # dense and BM25 hits each considered before blending
PART_BOOST=0.3          # boost for chunks containing a specific query part number

# LLM Service (required for the query pipeline; ingestion does not use it)
LLM_BASE_URL=http://golem:4000/v1
LLM_MODEL=Gemma-4-26B-A4B
LLM_API_KEY=your_api_key
LLM_REASONING_EFFORT=low   # low (default), medium or high

# Chunking in characters, including the "Section:" line (optional, defaults shown)
CHUNK_SIZE=1000
CHUNK_OVERLAP=100

# Vector Database (optional, defaults shown)
CHROMA_PERSIST_DIRECTORY=./chroma_db
CHROMA_COLLECTION_NAME=vector_ingestion_collection

# Document Processing (optional, defaults shown)
DOCUMENT_INPUT_DIR=./documents
```

**Security:** Never commit `.env` or API keys. Use only on institutional network with HTTP endpoints.

**Changing chunk settings:** `.env` values override the defaults, and change detection skips PDFs whose file hash is unchanged. After changing `CHUNK_SIZE` or `CHUNK_OVERLAP`, re-ingest with `ENABLE_CHANGE_DETECTION=false` (or delete each document with `manage_documents.py --delete` first) so every document is re-chunked.

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

For detailed usage and options, see [Post-Ingestion Document Management](./requirements/ingestion.md#post-ingestion-document-management) in ingestion.md.

---


## Architecture Overview

### Data Flow

```
PDFs → Load → Parse → Metadata → Chunk → Embed → Store (Chroma)
                                           ↓
Query (+ chat history) → Rewrite → Embed → Hybrid search (Chroma + BM25 + part numbers) + earlier docs → LLM (Tool Calling) → Format Response
```

### State Management

Both pipelines use LangGraph's state management to flow data through nodes with error tracking and progress logging.

### Error Handling

- **Ingestion:** Skip-and-continue pattern (individual failures don't stop pipeline)
- **Query:** Graceful degradation with fallback responses and error details

---

## Dependencies

- `pypdf` - PDF metadata and fallback text extraction
- `pdfplumber` - Table extraction
- `pymupdf` - Layout analysis, embedded images and vector figure rendering (required; AGPL-3.0 licensed)
- `langchain`, `langchain_text_splitters` - Text splitting (embeddings and LLM calls are plain HTTP via `requests`)
- `langgraph` - Agent workflow orchestration
- `chromadb` - Vector database
- `requests` - HTTP calls to the embedding and LLM services
- `python-dotenv` - Environment configuration
- `tqdm` - Ingestion progress bars
- `flask` - Web app

Install with: `pip install -r requirements.txt`. There is no `setup.py`; run the scripts from the project root.

---

