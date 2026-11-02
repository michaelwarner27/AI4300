# Vector Ingestion Requirements Document

## Overview
This document specifies the requirements for the vector ingestion pipeline component of a ReAct agent built with LangGraph. The ingestion system processes PDF documents, converts them into embeddings, and stores them in a vector database for retrieval and querying.

## System Architecture

The ingestion pipeline follows this data flow:

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

## Detailed Requirements

### 1. Document Input

**Source Specification:**
- **Format:** PDF 
- **Location:** `./documents/` directory
- **Volume:** Small batch (1-10 documents for initial implementation)
- **Processing Scope:** All PDF files in `./documents/` directory will be processed

**Input Validation:**
- Verify files have `.pdf` extension
- Confirm file exists and is readable
- Handle missing document directory gracefully (Skip missing documents)
- Log document discovery and processing initiation

---

### 2. Document Parsing

**Tool & Approach:**
- **Primary Library:** `pypdf` (PyPDF2 equivalent)
- **Complexity Handling:** Rely entirely on pypdf capabilities for handling complex PDF layouts
  - Multi-column layouts
  - Embedded tables and images
  - Various PDF encoding formats
  - Corrupted or malformed PDFs (with error handling)

**Parsing Requirements:**
- Extract all text content from each PDF page
- Preserve page boundaries and ordering
- Maintain readable text structure where possible
- Handle special characters and encoding issues

**Metadata Extraction:**
- **Required Metadata:**
  - Source filename
  - Page numbers (per chunk)
  - Total pages in document
  - Document processing timestamp
- **Optional Metadata:**
  - PDF creation date (if available)
  - PDF author (if available)
  - Any document-level properties extractable by pypdf

**Error Handling:**
- Log parsing failures without stopping pipeline
- Skip problematic pages and continue processing
- Provide warnings for partial extraction (e.g., image-only pages)

---

### 3. Text Chunking

**Chunking Strategy:**
- **Method:** Fixed-size chunking with overlap (character-based)
- **Recommended Parameters (defaults):**
  - **Chunk Size:** 512 characters (approximately 256-512 tokens depending on content)
  - **Overlap:** 50 characters (approximately 10-20% of chunk size)
  - **Implementation:** Use LangChain's `RecursiveCharacterTextSplitter` 
  
**Chunking Configuration:**
- Chunk size should be tunable via configuration (.env or constants)
- Overlap should be tunable independently
- Preserve chunk-to-page mapping in metadata
- Maintain document context through overlap

**Chunk Metadata:**
- **Attached to Each Chunk:**
  - Source document filename
  - Original page number (determined by position in per-page texts)
  - Chunk sequence number within document
  - Chunk position (start/end character indices in original text)

**Validation:**
- Verify all chunks meet minimum content threshold (avoid empty/near-empty chunks)
- Log chunks that exceed token limits
- Track total chunks generated per document

---

### 4. Embedding Generation

**Embedding Model Specification:**
- **Provider:** Configurable embedding service (can be local or remote)
- **Library:** HTTP requests to embedding service endpoint
- **Configuration Source:** `.env` file
  - `EMBEDDING_BASE_URL` - Base URL of embedding service (e.g., http://localhost:4000/v1)
  - `EMBEDDING_MODEL` - Model identifier (e.g., Qwen3-Embedding-8B)
  - `EMBEDDING_API_KEY` - API key for authentication

**Embedding Requirements:**
- Generate vector representation for each chunk
- Vector dimension: determined by selected embedding model
- Handle API rate limiting gracefully (implement backoff if needed)
- Log embedding generation progress and any API errors

**Error Handling:**
- Validate API credentials before processing
- Handle API failures with retry logic
- Skip chunks with embedding failures (with logging)
- Continue pipeline even if individual chunk embedding fails

---

### 5. Vector Storage

**Vector Database:**
- **Primary Technology:** Chroma
- **Storage Mode:** Configurable (persistent in filesystem)
- **Collection Management:** Create/use document collection for ingested vectors

**Storage Requirements:**
- **Vector Storage:**
  - Store embedding vectors
  - Index for efficient retrieval
  - Support similarity search operations

- **Metadata Storage:**
  - Store all chunk metadata alongside vectors
  - Enable filtering by source document
  - Enable filtering by page number
  - Support metadata-based queries

**Collection Schema:**
- Collection name: configurable, default `"vector_ingestion_collection"`
- Unique ID per chunk: format `"{filename}_{page}_{chunk_seq}"`
- Distance metric: cosine similarity (default)

**Persistence:**
- **Location:** Configurable path (e.g., `./chroma_db/`)
- **Durability:** Persist across sessions

---

## LangGraph Integration

**Node Structure:**
The ingestion pipeline consists of 5 LangGraph nodes in linear sequence:

1. **Node: `load_documents`**
   - Input: None (reads from `./documents/`)
   - Output: List of ParsedDocument objects with metadata
   - Tool: PDFLoader (discovers PDFs, extracts document-level metadata)
   - Responsibilities: Discover PDF files, extract author/creation_date, add processing timestamp

2. **Node: `parse_documents`**
   - Input: List of ParsedDocument objects (empty content)
   - Output: ParsedDocument objects with extracted text
   - Tool: PDFParser (uses pypdf)
   - Responsibilities: Extract all text from each page, preserve page structure

3. **Node: `chunk_text`**
   - Input: List of ParsedDocument objects with content
   - Output: List of TextChunk objects with metadata
   - Tool: TextChunker (uses LangChain RecursiveCharacterTextSplitter)
   - Responsibilities: Split text into overlapping chunks, map chunks to pages

4. **Node: `generate_embeddings`**
   - Input: List of TextChunk objects
   - Output: TextChunk objects with embedding vectors
   - Tool: EmbeddingService (HTTP client to embedding API)
   - Responsibilities: Generate vectors for each chunk, handle API errors

5. **Node: `store_vectors`**
   - Input: List of TextChunk objects with embeddings
   - Output: Confirmation of storage completion
   - Tool: ChromaVectorStore (Chroma client)
   - Responsibilities: Persist vectors and metadata to Chroma collection

**State Management:**
- Maintain ingestion state across nodes (documents, chunks, embeddings)
- Track processing status and errors
- Log progress at each pipeline stage

---

## Configuration & Environment

**Environment Variables (.env):**
```
# Embedding Service Configuration
EMBEDDING_BASE_URL=http://localhost:4000/v1
EMBEDDING_MODEL=Qwen3-Embedding-8B
EMBEDDING_API_KEY=<your-api-key>

# LLM Service Configuration
LLM_BASE_URL=http://localhost:4000/v1
LLM_MODEL=Qwen3.8-27B
LLM_API_KEY=<your-api-key>

# Chunking Configuration
CHUNK_SIZE=512
CHUNK_OVERLAP=50

# Vector Database
CHROMA_PERSIST_DIRECTORY=./chroma_db
CHROMA_COLLECTION_NAME=vector_ingestion_collection

# Document Processing
DOCUMENT_INPUT_DIR=./documents
```

**Configurable Parameters:**
- Chunk size and overlap percentages
- Embedding model selection
- Vector store location and collection naming
- Input/output directory paths
- Batch processing size for API calls (if applicable)

---

## Success Criteria

✓ All PDF files in `./documents/` are discovered and processed  
✓ Document metadata (filename, pages, author, creation_date) extracted via pypdf  
✓ Text is successfully extracted from each PDF page with pypdf  
✓ Page numbers and source filenames are preserved in chunk metadata  
✓ Text is chunked into ~512 character segments with 50 character overlap using LangChain  
✓ Each chunk receives an embedding vector from configured embedding service  
✓ All vectors and metadata are stored in Chroma with proper indexing  
✓ Pipeline completes without crashing on edge cases (image-only pages, encoding issues)  
✓ Processing is logged at key stages with high-level progress  
✓ Vector storage is persistent and queryable across sessions  
✓ Skip and continue error handling: pipeline continues despite individual failures  

---

## Future Considerations

- Incremental ingestion (adding new documents without re-processing all)
- Document update detection and re-processing
- Batch embedding optimization for large-scale deployments
- Vector database optimization and indexing strategies
- Multi-format support (docx, txt, markdown, etc.)
- Scaling to larger document volumes

---

## Dependencies

**Python Libraries:**
- `pypdf` - PDF text extraction
- `langchain` - Core LangChain functionality
- `langchain-text-splitters` - RecursiveCharacterTextSplitter for text chunking
- `langchain-chroma` - Chroma vector store integration
- `langgraph` - Agent workflow orchestration and state management
- `python-dotenv` - Environment configuration loading
- `chromadb` - Chroma vector database
- `requests` - HTTP client for embedding service API calls

**External Services:**
- Embedding Service (local or remote, HTTP-accessible)
- LLM Service (local or remote, HTTP-accessible)
- Chroma DB (vector storage, file-based persistence)
