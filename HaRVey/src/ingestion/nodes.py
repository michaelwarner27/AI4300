"""
LangGraph node implementations for the vector ingestion pipeline.
Each node processes documents through one stage of the pipeline.
"""

from typing import Any
from datetime import datetime
from pathlib import Path
from src.data_models import (
    IngestionState,
    ParsedDocument,
    DocumentMetadata,
    TextChunk,
    ChunkMetadata,
)
from src.ingestion.tools import PDFLoader, PDFParser, TextChunker, EmbeddingService, ChromaVectorStore
from src.utils.logging import get_logger

logger = get_logger()


def load_documents(state: IngestionState) -> dict[str, Any]:
    """
    Load PDF documents from the input directory.
    
    This is the first node in the pipeline. It discovers PDF files
    in the configured document input directory and creates initial
    Document objects with metadata.
    
    Args:
        state: Current pipeline state
    
    Returns:
        Dictionary with updated state fields:
        - documents: List of loaded Document objects
        - documents_loaded: Count of documents loaded
        - errors: Accumulated error messages (if any)
    """
    logger.info("Starting load_documents node")
    
    errors: list[str] = list(state.errors)
    documents: list[ParsedDocument] = []
    
    if not state.config:
        error_msg = "Configuration not available in pipeline state"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "documents": documents,
            "documents_loaded": 0,
            "errors": errors,
            "status": "load_documents_failed",
        }
    
    try:
        # Use PDFLoader to discover and load documents
        loader = PDFLoader()
        documents = loader.load_documents(state.config.document_input_dir)
        
        # Add processing timestamp to each document
        now = datetime.now().isoformat()
        for doc in documents:
            doc.metadata.processing_timestamp = now
        
        logger.info(f"load_documents: Successfully loaded {len(documents)} documents")
        
    except (FileNotFoundError, ValueError) as e:
        error_msg = f"Failed to load documents: {str(e)}"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "documents": [],
            "documents_loaded": 0,
            "errors": errors,
            "status": "load_documents_failed",
        }
    except Exception as e:
        error_msg = f"Unexpected error loading documents: {str(e)}"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "documents": [],
            "documents_loaded": 0,
            "errors": errors,
            "status": "load_documents_failed",
        }
    
    return {
        "documents": documents,
        "documents_loaded": len(documents),
        "errors": errors,
        "status": "documents_loaded",
    }


def parse_documents(state: IngestionState) -> dict[str, Any]:
    """
    Parse PDF documents to extract text content.
    
    Extracts text from each PDF using pypdf, preserving page structure
    and handling errors gracefully (skip and continue pattern).
    
    Args:
        state: Current pipeline state with documents to parse
    
    Returns:
        Dictionary with updated state fields:
        - documents: Documents with content populated
        - documents_parsed: Count of successfully parsed documents
        - errors: Accumulated error messages
    """
    logger.info("Starting parse_documents node")
    
    documents: list[ParsedDocument] = state.documents
    parsed_docs: list[ParsedDocument] = []
    errors: list[str] = list(state.errors)
    parser = PDFParser()
    
    for doc in documents:
        try:
            # Construct full file path
            doc_path = Path(state.config.document_input_dir) / doc.metadata.filename
            
            # Parse document using pypdf
            full_text, per_page_texts = parser.parse_document(str(doc_path))
            
            # Update document with extracted content
            doc.content = full_text
            doc.pages = per_page_texts
            
            parsed_docs.append(doc)
            
        except (FileNotFoundError, ValueError) as e:
            error_msg = f"Failed parsing {doc.metadata.filename}: {str(e)}"
            errors.append(error_msg)
            logger.warning(error_msg)
            # Skip this document, continue with next
        except Exception as e:
            error_msg = f"Unexpected error parsing {doc.metadata.filename}: {str(e)}"
            errors.append(error_msg)
            logger.warning(error_msg)
            # Skip this document, continue with next
    
    success_count = len(parsed_docs)
    total_count = len(documents)
    logger.info(f"parse_documents: {success_count}/{total_count} successfully parsed")
    
    return {
        "documents": parsed_docs,
        "documents_parsed": success_count,
        "errors": errors,
        "status": "documents_parsed",
    }


def chunk_text(state: IngestionState) -> dict[str, Any]:
    """
    Split document text into overlapping chunks.
    
    Converts parsed documents into TextChunk objects using LangChain's
    RecursiveCharacterTextSplitter with configurable chunk size and overlap.
    Each chunk preserves metadata about its source document and page number.
    
    Args:
        state: Current pipeline state with parsed documents
    
    Returns:
        Dictionary with updated state fields:
        - chunks: List of TextChunk objects
        - chunks_created: Count of chunks created
        - errors: Accumulated error messages
    """
    logger.info("Starting chunk_text node")
    
    if not state.config:
        error_msg = "Configuration not available in pipeline state"
        logger.error(error_msg)
        errors: list[str] = list(state.errors)
        errors.append(error_msg)
        return {
            "chunks": [],
            "chunks_created": 0,
            "errors": errors,
            "status": "chunk_text_failed",
        }
    
    documents: list[ParsedDocument] = state.documents
    chunks: list[TextChunk] = []
    errors: list[str] = list(state.errors)
    chunk_count = 0
    
    # Initialize TextChunker with configured parameters
    chunker = TextChunker(
        chunk_size=state.config.chunk_size,
        overlap=state.config.chunk_overlap,
    )
    
    for doc in documents:
        try:
            # Use TextChunker to split document
            doc_chunks = chunker.chunk_document(doc)
            chunks.extend(doc_chunks)
            chunk_count += len(doc_chunks)
            
        except Exception as e:
            error_msg = f"Failed chunking {doc.metadata.filename}: {str(e)}"
            errors.append(error_msg)
            logger.warning(error_msg)
            # Continue with next document
    
    logger.info(
        f"chunk_text: Created {chunk_count} chunks from {len(documents)} documents"
    )
    
    return {
        "chunks": chunks,
        "chunks_created": chunk_count,
        "errors": errors,
        "status": "text_chunked",
    }


def generate_embeddings(state: IngestionState) -> dict[str, Any]:
    """
    Generate embedding vectors for all text chunks.
    
    Calls the configured embedding service (local or remote) to generate
    vector representations for each chunk. Uses batch API calls for efficiency
    and handles API failures gracefully (skip and continue pattern).
    
    Args:
        state: Current pipeline state with text chunks
    
    Returns:
        Dictionary with updated state fields:
        - chunks: Chunks with embedding vectors populated
        - embeddings_generated: Count of successfully embedded chunks
        - errors: Accumulated error messages
    """
    logger.info("Starting generate_embeddings node")
    
    chunks: list[TextChunk] = state.chunks
    embedded_chunks: list[TextChunk] = []
    errors: list[str] = list(state.errors)
    
    if not state.config:
        error_msg = "Configuration not available in pipeline state"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "chunks": [],
            "embeddings_generated": 0,
            "errors": errors,
            "status": "embeddings_failed",
        }
    
    if not chunks:
        logger.info("No chunks to embed")
        return {
            "chunks": [],
            "embeddings_generated": 0,
            "errors": errors,
            "status": "embeddings_generated",
        }
    
    # Initialize embedding service
    try:
        embedding_service = EmbeddingService(
            base_url=state.config.embedding_base_url,
            model=state.config.embedding_model,
            api_key=state.config.embedding_api_key,
        )
    except Exception as e:
        error_msg = f"Failed to initialize embedding service: {str(e)}"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "chunks": [],
            "embeddings_generated": 0,
            "errors": errors,
            "status": "embeddings_failed",
        }
    
    # Process chunks in batches for efficiency
    batch_size = 32  # Process multiple chunks per API call
    chunk_failures = 0
    
    for batch_start in range(0, len(chunks), batch_size):
        batch_end = min(batch_start + batch_size, len(chunks))
        batch_chunks = chunks[batch_start:batch_end]
        batch_texts = [chunk.content for chunk in batch_chunks]
        
        try:
            # Call embedding service with batch of texts
            embeddings = embedding_service.embed_batch(batch_texts)
            
            # Validate we got embeddings for all texts in batch
            if len(embeddings) != len(batch_chunks):
                error_msg = (
                    f"Batch embedding mismatch: expected {len(batch_chunks)} embeddings, "
                    f"got {len(embeddings)}"
                )
                logger.error(error_msg)
                errors.append(error_msg)
                chunk_failures += len(batch_chunks)
                continue
            
            # Attach embeddings to chunks
            for chunk, embedding in zip(batch_chunks, embeddings):
                chunk.embedding = embedding
                embedded_chunks.append(chunk)
            
            logger.debug(f"Successfully embedded batch of {len(batch_chunks)} chunks")
            
        except (ConnectionError, ValueError) as e:
            # API error or invalid response - skip this batch and continue
            error_msg = (
                f"Failed to embed batch of {len(batch_chunks)} chunks "
                f"(indices {batch_start}-{batch_end}): {str(e)}"
            )
            logger.warning(error_msg)
            errors.append(error_msg)
            chunk_failures += len(batch_chunks)
        except Exception as e:
            # Unexpected error - skip this batch and continue
            error_msg = (
                f"Unexpected error embedding batch of {len(batch_chunks)} chunks "
                f"(indices {batch_start}-{batch_end}): {str(e)}"
            )
            logger.error(error_msg)
            errors.append(error_msg)
            chunk_failures += len(batch_chunks)
    
    success_count = len(embedded_chunks)
    total_count = len(chunks)
    
    if chunk_failures > 0:
        logger.warning(
            f"generate_embeddings: {success_count}/{total_count} successfully embedded "
            f"({chunk_failures} failures)"
        )
    else:
        logger.info(f"generate_embeddings: {success_count}/{total_count} successfully embedded")
    
    return {
        "chunks": embedded_chunks,
        "embeddings_generated": success_count,
        "errors": errors,
        "status": "embeddings_generated",
    }


def store_vectors(state: IngestionState) -> dict[str, Any]:
    """
    Store vectors and metadata in Chroma vector database.
    
    Persists all embedded chunks along with their metadata to Chroma,
    making them available for similarity search and retrieval.
    Each chunk is stored with:
    - Embedding vector
    - Chunk content
    - Metadata (source_filename, page_number, chunk_sequence, char indices)
    - Unique ID: {filename}_{page}_{chunk_seq}
    
    Args:
        state: Current pipeline state with embedded chunks
    
    Returns:
        Dictionary with updated state fields:
        - vectors_stored: Count of vectors persisted to Chroma
        - status: Final pipeline status
        - errors: Accumulated error messages
    """
    logger.info("Starting store_vectors node")
    
    chunks: list[TextChunk] = state.chunks
    stored_count = 0
    errors: list[str] = list(state.errors)
    
    if not state.config:
        error_msg = "Configuration not available in pipeline state"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "vectors_stored": 0,
            "errors": errors,
            "status": "storage_failed",
        }
    
    if not chunks:
        logger.info("No chunks to store in Chroma")
        return {
            "vectors_stored": 0,
            "errors": errors,
            "status": "pipeline_complete",
        }
    
    try:
        # Initialize Chroma vector store
        vector_store = ChromaVectorStore(
            persist_directory=state.config.chroma_persist_directory,
            collection_name=state.config.chroma_collection_name,
        )
        
        # Initialize/connect to Chroma collection
        vector_store.initialize_collection()
        logger.debug(
            f"Chroma collection initialized: "
            f"{state.config.chroma_collection_name} "
            f"at {state.config.chroma_persist_directory}"
        )
        
        # Store chunks in Chroma
        stored_count = vector_store.store_chunks(chunks)
        
        logger.info(
            f"store_vectors: Successfully stored {stored_count}/{len(chunks)} "
            f"vectors in Chroma"
        )
        
        if stored_count < len(chunks):
            # Some chunks had missing embeddings or other issues
            failed_count = len(chunks) - stored_count
            warning_msg = (
                f"store_vectors: {failed_count} chunks were not stored "
                f"(missing embeddings or invalid data)"
            )
            logger.warning(warning_msg)
            errors.append(warning_msg)
    
    except (ConnectionError, RuntimeError) as e:
        # Chroma connection or storage error
        error_msg = f"Failed to store vectors in Chroma: {str(e)}"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "vectors_stored": 0,
            "errors": errors,
            "status": "storage_failed",
        }
    except Exception as e:
        # Unexpected error
        error_msg = f"Unexpected error storing vectors: {str(e)}"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "vectors_stored": 0,
            "errors": errors,
            "status": "storage_failed",
        }
    
    return {
        "vectors_stored": stored_count,
        "errors": errors,
        "status": "pipeline_complete",
    }
