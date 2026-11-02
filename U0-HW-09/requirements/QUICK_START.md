# Quick Start Guide - Vector Ingestion Pipeline

## Running the Pipeline

```bash
# Navigate to project
cd /home/michael24/4300-CS/U0-HW-09

# Activate virtual environment
source .venv/bin/activate

# Run the pipeline
python main.py
```

## Expected Output
- Configuration loads from `.env`
- All 6 nodes execute in sequence
- Summary shows statistics:
  - Documents loaded, parsed
  - Chunks created
  - Embeddings generated
  - Vectors stored
  - Any errors encountered

## File Structure

### Configuration
- `.env` - Your API keys and configuration variables
- `src/config.py` - Loads .env into IngestionConfig object

### Pipeline Code
- `src/data_models.py` - Data structures (DocumentMetadata, TextChunk, IngestionState, etc.)
- `src/ingestion/nodes.py` - The 6 node functions (skeleton implementations)
- `src/ingestion/tools.py` - Tool wrappers (PDFParser, TextChunker, EmbeddingService, ChromaVectorStore)
- `src/ingestion/graph.py` - LangGraph definition and compilation

### Entry Point
- `main.py` - Run this to execute the pipeline

### Utilities
- `src/utils/logging.py` - Logging configuration

### Documentation
- `requirements/ingestion-requirements.md` - Full requirements specification
- `requirements/langgraph-skeleton-plan.md` - Implementation plan
- `SKELETON_SUMMARY.md` - What was built and how to extend it
- `QUICK_START.md` - This file

## Current Status

### ✅ Complete
- LangGraph skeleton with 6 nodes
- State management through pipeline
- Configuration loading
- High-level progress logging
- Error tracking (skip and continue pattern)
- Full type hints throughout

### 🔲 Next (Phase 2)
- Replace mock implementations with real pypdf parsing
- Implement real text chunking
- Connect to actual embedding service
- Persist to Chroma database

## The 6 Nodes

1. **load_documents** - Discover PDFs in ./documents/
2. **parse_documents** - Extract text with pypdf
3. **extract_metadata** - Attach document metadata
4. **chunk_text** - Split into overlapping chunks
5. **generate_embeddings** - Create embedding vectors
6. **store_vectors** - Save to Chroma

## Extending the Pipeline

### To implement a node:
1. Edit `src/ingestion/tools.py` - Implement actual tool logic
2. Edit `src/ingestion/nodes.py` - Update node to call real tools
3. Test with actual data

### Example (load_documents in Phase 2):
```python
# In src/ingestion/tools.py
class PDFLoader:
    def find_documents(self, directory: str) -> list[Path]:
        return list(Path(directory).glob("*.pdf"))

# In src/ingestion/nodes.py
def load_documents(state: IngestionState) -> dict[str, Any]:
    loader = PDFLoader()
    pdf_paths = loader.find_documents(state.config.document_input_dir)
    # Create Document objects...
    return {"documents": documents, ...}
```

## Configuration Variables

Your `.env` file should contain:
```
EMBEDDING_BASE_URL=http://golem:4000/v1
EMBEDDING_MODEL=Qwen3-Embedding-8B
EMBEDDING_API_KEY=your_api_key

LLM_BASE_URL=http://golem:4000/v1
LLM_MODEL=Qwen3.8-27B
LLM_API_KEY=your_api_key

CHUNK_SIZE=512
CHUNK_OVERLAP=50

CHROMA_PERSIST_DIRECTORY=./chroma_db
CHROMA_COLLECTION_NAME=vector_ingestion_collection

DOCUMENT_INPUT_DIR=./documents
```

## Troubleshooting

**Error: ".env file not found"**
- Make sure `.env` exists in the project root
- Run from `/home/michael24/4300-CS/U0-HW-09`

**Error: "Missing required environment variables"**
- Check that all variables in config.py are set in .env
- No `<>` brackets in actual values

**Error: Import issues**
- Make sure virtual environment is activated: `source .venv/bin/activate`
- Reinstall dependencies: `pip install -r src/requirements.txt` (if created)

## Next Steps

1. Review `SKELETON_SUMMARY.md` for what was built
2. Read `requirements/langgraph-skeleton-plan.md` for Phase 2 details
3. Start implementing Phase 2 nodes in order (load_documents first)
4. Test each node with real PDF documents before moving to next

---

**Last Updated:** After skeleton implementation and testing  
**Status:** Ready for Phase 2 implementation
