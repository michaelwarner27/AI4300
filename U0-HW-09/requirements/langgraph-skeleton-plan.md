# LangGraph Skeleton Implementation Plan

## Overview
This document outlines the high-level architecture and implementation strategy for building the LangGraph-based vector ingestion pipeline skeleton. The skeleton will establish the complete node structure with mock implementations before adding real business logic incrementally.

---

## Project Structure

```
/home/michael24/4300-CS/U0-HW-09/
├── .env                          # Configuration (API keys, paths, params)
├── .venv/                         # Virtual environment
├── documents/                     # Input PDFs
├── chroma_db/                     # Vector store (created at runtime)
├── requirements/                  # Documentation
│   ├── ingestion-requirements.md
│   └── langgraph-skeleton-plan.md
├── src/                           # Main source code
│   ├── __init__.py
│   ├── config.py                  # .env configuration loader
│   ├── data_models.py             # Pydantic/dataclass models
│   ├── ingestion/                 # Ingestion pipeline module
│   │   ├── __init__.py
│   │   ├── graph.py               # LangGraph definition and compilation
│   │   ├── nodes.py               # The 6 node function implementations
│   │   └── tools.py               # Tool implementations (pypdf, embeddings, chroma)
│   └── utils/                     # Utility modules
│       ├── __init__.py
│       └── logging.py             # Logging configuration
├── main.py                        # Entry point for pipeline execution
└── tests/                         # Test suite (future)
```

---

## Design Decisions

### 1. Execution Model: **Synchronous**
- **Rationale:** Simpler to understand and debug during development
- **Implementation:** All node functions are sync, no async/await
- **Future:** Can be upgraded to async if performance becomes a concern

### 2. Error Handling: **Skip and Continue**
- **Rationale:** Resilient pipeline that processes maximum data despite individual failures
- **Implementation:**
  - Each node logs failures with document/chunk identifiers
  - Failed items are added to `state.errors` list
  - Pipeline continues with successfully processed items
  - Final report shows what succeeded and what failed
- **Example:** If document 3 of 10 fails parsing, documents 1,2,4-10 still get processed

### 3. Logging Strategy: **High-Level Progress Only**
- **Rationale:** Clear visibility into pipeline execution without verbose noise
- **Implementation:**
  - Log node entry/exit with item counts
  - Log final statistics per node (e.g., "Parsed 9/10 documents successfully")
  - Log errors by document/chunk ID for debugging
  - No per-item debug logs unless explicitly requested
- **Example Output:**
  ```
  [INFO] Starting ingestion pipeline
  [INFO] load_documents: Found 10 documents
  [INFO] parse_documents: Parsed 9/10 successfully (1 error)
  [INFO] chunk_text: Created 342 chunks from 9 documents
  [INFO] generate_embeddings: Embedded 342/342 chunks
  [INFO] store_vectors: Stored 342 vectors in Chroma
  [INFO] Pipeline complete: 9/10 documents processed
  ```

### 4. Type Hints: **Full Type Coverage**
- **Rationale:** Enables IDE support, catches errors early, documents intent clearly
- **Implementation:**
  - All function parameters typed
  - All return types explicit
  - Dataclass/Pydantic models for complex types
  - Type hints on class attributes
- **Example:**
  ```python
  def parse_documents(state: IngestionState) -> dict[str, Any]:
      documents: list[ParsedDocument] = []
      errors: list[str] = []
      # ...
      return {"documents": documents, "errors": errors}
  ```

---

## Core Components

### 1. Configuration (config.py)
**Responsibility:** Load and provide type-safe access to .env variables

**Functionality:**
- Read from `.env` file
- Parse into structured `IngestionConfig` object
- Provide sensible defaults where applicable
- Validate required variables

**Key Variables:**
```python
class IngestionConfig:
    embedding_base_url: str         # e.g., "http://golem:4000/v1"
    embedding_model: str            # e.g., "Qwen3-Embedding-8B"
    embedding_api_key: str          # API key for embedding service
    
    llm_base_url: str               # e.g., "http://golem:4000/v1"
    llm_model: str                  # e.g., "Qwen3.8-27B"
    llm_api_key: str                # API key for LLM service
    
    chunk_size: int                 # e.g., 512 (tokens)
    chunk_overlap: int              # e.g., 50 (tokens)
    
    chroma_persist_directory: str   # e.g., "./chroma_db"
    chroma_collection_name: str     # e.g., "vector_ingestion_collection"
    
    document_input_dir: str         # e.g., "./documents"
```

### 2. Data Models (data_models.py)
**Responsibility:** Define all data structures used throughout the pipeline

**Core Classes:**

```python
@dataclass
class DocumentMetadata:
    """Metadata about a PDF document"""
    filename: str
    total_pages: int
    processing_timestamp: str
    # Optional fields that may be extracted by pypdf
    author: str | None = None
    creation_date: str | None = None

@dataclass
class ParsedDocument:
    """Document after text extraction"""
    content: str                           # Full document text
    metadata: DocumentMetadata
    pages: list[str]                       # Per-page text content

@dataclass
class ChunkMetadata:
    """Metadata attached to each text chunk"""
    source_filename: str
    page_number: int
    chunk_sequence: int                    # Order within document
    start_char_index: int
    end_char_index: int

@dataclass
class TextChunk:
    """A single chunk of text with embedding"""
    content: str
    metadata: ChunkMetadata
    embedding: list[float] | None = None  # Added by embedding node

class IngestionState:
    """The state passed through all nodes in the LangGraph pipeline"""
    documents: list[ParsedDocument]        # Accumulates through pipeline
    chunks: list[TextChunk]                # Created at chunk node
    errors: list[str]                      # Accumulated error messages
    config: IngestionConfig                # Pipeline configuration
    status: str                            # Current pipeline status
```

### 3. LangGraph Nodes (nodes.py)
**Responsibility:** Implement the 6 pipeline nodes as sync functions

**Node Signatures (all follow this pattern):**
```python
def node_name(state: IngestionState) -> dict[str, Any]:
    """
    Description of what this node does.
    
    Args:
        state: Current pipeline state
    
    Returns:
        Dictionary with updated state fields
    """
    # Implementation
    return {"field_name": updated_value, ...}
```

**The 6 Nodes:**

| Node | Input | Processing | Output |
|------|-------|-----------|--------|
| `load_documents` | DOCUMENT_INPUT_DIR | Discover PDF files, create Document objects | `documents: list[ParsedDocument]` |
| `parse_documents` | `state.documents` | Extract text using pypdf, preserve page structure | `documents: list[ParsedDocument]` (with content) |
| `extract_metadata` | `state.documents` | Attach filename, page count, timestamp metadata | `documents: list[ParsedDocument]` (with full metadata) |
| `chunk_text` | `state.documents` | Split text into overlapping chunks with chunk metadata | `chunks: list[TextChunk]` |
| `generate_embeddings` | `state.chunks` | Call embedding API for each chunk | `chunks: list[TextChunk]` (with embeddings) |
| `store_vectors` | `state.chunks` | Persist chunks + embeddings + metadata to Chroma | `status: str`, vector count stats |

**Node Implementation Pattern:**

Each node follows this structure:
```python
def node_name(state: IngestionState) -> dict[str, Any]:
    logger.info(f"Starting {node_name}")
    
    input_items: list[...] = state.some_field
    output_items: list[...] = []
    errors: list[str] = list(state.errors)
    
    for item in input_items:
        try:
            # Process item
            result = process(item)
            output_items.append(result)
        except Exception as e:
            # Log error, skip item, continue
            error_msg = f"Failed processing {item.id}: {str(e)}"
            errors.append(error_msg)
            logger.warning(error_msg)
    
    success_count = len(output_items)
    total_count = len(input_items)
    logger.info(f"{node_name}: {success_count}/{total_count} successful")
    
    return {
        "output_field": output_items,
        "errors": errors
    }
```

### 4. Tools (tools.py)
**Responsibility:** Encapsulate external tool interactions in reusable classes

**Tool Classes:**

```python
class PDFParser:
    """Wrapper around pypdf for text extraction"""
    
    def parse_document(self, filepath: str) -> tuple[str, list[str], dict]:
        """
        Extract text from PDF.
        Returns: (full_text, per_page_texts, metadata_dict)
        """

class TextChunker:
    """Split text into overlapping chunks"""
    
    def chunk_document(
        self, 
        text: str, 
        chunk_size: int, 
        overlap: int, 
        metadata: dict
    ) -> list[TextChunk]:
        """Create overlapping chunks with metadata"""

class EmbeddingService:
    """Call embedding API (local service at golem:4000)"""
    
    def embed_text(self, text: str) -> list[float]:
        """Generate embedding vector for text"""
    
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts (for optimization)"""

class ChromaVectorStore:
    """Persist vectors and metadata to Chroma"""
    
    def initialize_collection(
        self, 
        collection_name: str, 
        persist_dir: str
    ) -> None:
        """Connect to/create Chroma collection"""
    
    def store_chunks(
        self, 
        chunks: list[TextChunk]
    ) -> int:
        """Store chunks with embeddings and metadata, return count stored"""
```

### 5. LangGraph Definition (graph.py)
**Responsibility:** Wire nodes together into a linear pipeline

**Structure:**
```python
def create_ingestion_graph(config: IngestionConfig) -> StateGraph:
    """
    Create and compile the ingestion pipeline graph.
    
    Pipeline flow:
    START → load_documents → parse_documents → extract_metadata
          → chunk_text → generate_embeddings → store_vectors → END
    """
    graph = StateGraph(IngestionState)
    
    # Add all 6 nodes
    graph.add_node("load_documents", load_documents)
    graph.add_node("parse_documents", parse_documents)
    # ... etc
    
    # Connect in linear sequence
    graph.add_edge("START", "load_documents")
    graph.add_edge("load_documents", "parse_documents")
    # ... etc
    graph.add_edge("store_vectors", "END")
    
    # Compile and return
    return graph.compile()
```

### 6. Entry Point (main.py)
**Responsibility:** Initialize and execute the full pipeline

**Functionality:**
```python
async def main() -> None:
    # Load configuration
    config = load_config()
    
    # Create initial state
    initial_state = IngestionState(
        documents=[],
        chunks=[],
        errors=[],
        config=config,
        status="initialized"
    )
    
    # Create and run graph
    graph = create_ingestion_graph(config)
    result_state = graph.invoke(initial_state)
    
    # Report results
    print_summary(result_state)

if __name__ == "__main__":
    main()
```

---

## Implementation Phases

### Phase 1: Skeleton with Mocks (Current)
**Goal:** Build complete structure that runs end-to-end with placeholder logic

**Tasks:**
1. Create project structure (directories)
2. Implement config.py with real .env loading
3. Implement data_models.py with all dataclasses
4. Implement nodes.py with skeleton functions (return mock data)
5. Implement tools.py with stub classes (logging only)
6. Implement graph.py to wire everything together
7. Create main.py to test pipeline execution
8. Verify pipeline runs without errors

**Expected Output:** Pipeline executes, shows progress logging, but doesn't actually parse PDFs or create embeddings yet

### Phase 2: Node Implementation (Sequential)
**Goal:** Replace mock implementations with real logic, one node at a time

**Order:**
1. `load_documents` - Read PDFs from directory
2. `parse_documents` - Extract text with pypdf
3. `extract_metadata` - Attach document metadata
4. `chunk_text` - Split into overlapping chunks
5. `generate_embeddings` - Call embedding service
6. `store_vectors` - Persist to Chroma

**For Each Node:**
- Implement real logic in tools.py
- Update node function to call tools
- Test with sample documents
- Verify error handling (skip and continue)

### Phase 3: Integration & Testing
**Goal:** Full end-to-end pipeline validation

**Tasks:**
- Test with actual PDF documents
- Verify Chroma storage persistence
- Query vectors to confirm correctness
- Performance optimization if needed

---

## Skeleton Characteristics

### What the skeleton includes:
✓ Complete LangGraph node structure  
✓ State management and data flow  
✓ Configuration loading from .env  
✓ Type-safe data models  
✓ High-level logging at each node  
✓ Error accumulation (skip and continue pattern)  
✓ Compilable, runnable code  

### What the skeleton does NOT include (yet):
✗ Actual PDF parsing (pypdf)  
✗ Real text chunking  
✗ Real embedding generation  
✗ Real Chroma storage  
✗ Error recovery strategies  
✗ Performance optimization  

---

## Testing Strategy

**Skeleton Testing (Phase 1):**
```bash
python main.py
# Expected: Pipeline runs to completion, logs progress, returns summary
```

**After Each Node Implementation (Phase 2):**
```python
# Manual test with real data
state = IngestionState(...)
result = specific_node(state)
assert len(result[output_field]) > 0
```

**Full Pipeline (Phase 3):**
```bash
python main.py
# Verify: Chroma contains vectors, queries work correctly
```

---

## Dependencies (to be installed)

```
pypdf
langchain
langgraph
python-dotenv
chromadb
requests  # For API calls to embedding service
```

---

## Next Steps

1. Create directory structure: `src/ingestion/`, `src/utils/`
2. Implement `config.py` with .env loading
3. Implement `data_models.py` with all dataclasses
4. Implement `nodes.py` skeleton
5. Implement `tools.py` stubs
6. Implement `graph.py` wiring
7. Implement `main.py` entry point
8. Run and verify pipeline executes
