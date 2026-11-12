"""
LangGraph node implementations for the vector ingestion pipeline.
Each node processes documents through one stage of the pipeline.
"""

import hashlib
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
from src.ingestion.tools import (
    PDFLoader, PDFParser, TextChunker, EmbeddingService, ChromaVectorStore,
    DocumentHasher, DiagramExtractor, TableExtractor, HeadingExtractor
)
from src.utils.logging import get_logger

logger = get_logger()


def load_documents(state: IngestionState) -> dict[str, Any]:
    """
    Load PDF documents from the input directory with change detection.
    
    This is the first node in the pipeline. It discovers PDF files
    in the configured document input directory and creates initial
    Document objects with metadata. Calculates file hashes for change
    detection and skips unchanged documents if configured.
    
    Args:
        state: Current pipeline state
    
    Returns:
        Dictionary with updated state fields:
        - documents: List of loaded Document objects (filtered by change detection)
        - documents_loaded: Count of documents loaded
        - documents_skipped_duplicate: Count of unchanged documents skipped
        - errors: Accumulated error messages (if any)
    """
    logger.info("Starting load_documents node")
    
    errors: list[dict] = list(state.errors)
    documents: list[ParsedDocument] = []
    skipped_duplicates = 0
    
    if not state.config:
        error_msg = "Configuration not available in pipeline state"
        logger.error(error_msg)
        errors.append({"stage": "load", "error": error_msg})
        return {
            "documents": documents,
            "documents_loaded": 0,
            "documents_skipped_duplicate": 0,
            "errors": errors,
            "status": "load_documents_failed",
        }
    
    try:
        # Use PDFLoader to discover and load documents
        loader = PDFLoader()
        pdf_files = list(Path(state.config.document_input_dir).glob("*.pdf"))
        pdf_files += list(Path(state.config.document_input_dir).glob("*.PDF"))
        
        if not pdf_files:
            raise ValueError(f"No PDF files found in {state.config.document_input_dir}")
        
        hasher = DocumentHasher()
        
        # Chroma is the source of truth for what has already been ingested
        hash_lookup: ChromaVectorStore | None = None
        if state.config.enable_change_detection:
            hash_lookup = ChromaVectorStore(
                persist_directory=state.config.chroma_persist_directory,
                collection_name=state.config.chroma_collection_name,
            )
            hash_lookup.initialize_collection()
        now = datetime.now().isoformat()
        
        # Show progress bar if enabled
        if state.show_progress_bar:
            try:
                from tqdm import tqdm
                pdf_iter = tqdm(pdf_files, desc="Loading documents")
            except ImportError:
                pdf_iter = pdf_files
        else:
            pdf_iter = pdf_files
        
        for pdf_path in pdf_iter:
            try:
                # Calculate file hash
                file_hash = hasher.calculate_file_hash(pdf_path)
                
                # Check for changes if enabled
                if hash_lookup is not None:
                    if hash_lookup.get_stored_hash(pdf_path.name) == file_hash:
                        # Same hash already stored in Chroma, skip it
                        log_msg = f"Skipping unchanged document: {pdf_path.name}"
                        if state.config.change_detection_log_level == "critical":
                            logger.warning(log_msg)
                        skipped_duplicates += 1
                        continue
                
                # Load document using PDFLoader
                doc = loader._load_single_document(pdf_path)
                
                # Add new metadata fields
                doc.filename = pdf_path.name
                doc.metadata.file_hash = file_hash
                doc.metadata.file_size_bytes = pdf_path.stat().st_size
                doc.metadata.loaded_at = now
                doc.metadata.processing_timestamp = now
                
                documents.append(doc)
            
            except Exception as e:
                error_msg = f"Error loading {pdf_path.name}: {str(e)}"
                logger.error(error_msg)
                errors.append({"stage": "load", "file": pdf_path.name, "error": str(e)})
        
        logger.info(
            f"load_documents: Successfully loaded {len(documents)} documents"
            f"{f', skipped {skipped_duplicates} unchanged' if skipped_duplicates > 0 else ''}"
        )
        
    except (FileNotFoundError, ValueError) as e:
        error_msg = f"Failed to load documents: {str(e)}"
        logger.error(error_msg)
        errors.append({"stage": "load", "error": error_msg})
        return {
            "documents": [],
            "documents_loaded": 0,
            "documents_skipped_duplicate": 0,
            "errors": errors,
            "status": "load_documents_failed",
        }
    except Exception as e:
        error_msg = f"Unexpected error loading documents: {str(e)}"
        logger.error(error_msg)
        errors.append({"stage": "load", "error": error_msg})
        return {
            "documents": [],
            "documents_loaded": 0,
            "documents_skipped_duplicate": 0,
            "errors": errors,
            "status": "load_documents_failed",
        }
    
    return {
        "documents": documents,
        "documents_loaded": len(documents),
        "documents_skipped_duplicate": skipped_duplicates,
        "errors": errors,
        "status": "documents_loaded",
    }


def parse_documents(state: IngestionState) -> dict[str, Any]:
    """
    Parse PDF documents to extract text, tables, and diagrams.
    
    Extracts text from each PDF using pypdf, preserving page structure.
    Additionally extracts tables using pdfplumber and diagrams using pypdf.
    Handles errors gracefully (skip and continue pattern).
    
    Args:
        state: Current pipeline state with documents to parse
    
    Returns:
        Dictionary with updated state fields:
        - documents: Documents with content, tables, and diagrams populated
        - documents_parsed: Count of successfully parsed documents
        - tables_extracted: Total number of tables found
        - diagrams_found: Total number of diagrams found
        - errors: Accumulated error messages
    """
    logger.info("Starting parse_documents node")
    
    documents: list[ParsedDocument] = state.documents
    parsed_docs: list[ParsedDocument] = []
    errors: list[dict] = list(state.errors)
    parser = PDFParser()
    
    # Initialize extractors
    table_extractor = None
    diagram_extractor = None
    
    if state.config and state.config.enable_table_extraction:
        try:
            table_extractor = TableExtractor()
        except ImportError as e:
            logger.warning(f"TableExtractor unavailable: {e}")
    
    try:
        diagram_extractor = DiagramExtractor()
    except Exception as e:
        logger.warning(f"DiagramExtractor initialization warning: {e}")
    
    # Show progress bar if enabled
    if state.show_progress_bar:
        try:
            from tqdm import tqdm
            doc_iter = tqdm(documents, desc="Parsing documents")
        except ImportError:
            doc_iter = documents
    else:
        doc_iter = documents
    
    tables_extracted = 0
    diagrams_found = 0
    
    for doc in doc_iter:
        try:
            # Construct full file path
            doc_path = Path(state.config.document_input_dir) / doc.metadata.filename
            
            # Parse document using pypdf for text
            full_text, per_page_texts = parser.parse_document(str(doc_path))
            
            # Update document with extracted content
            doc.content = full_text
            doc.pages = per_page_texts
            doc.page_contents = {i: text for i, text in enumerate(per_page_texts)}
            
            # Extract tables if enabled
            if table_extractor:
                try:
                    tables = table_extractor.extract_tables_from_pdf(doc_path)
                    doc.tables = tables
                    tables_extracted += len(tables)
                except Exception as e:
                    logger.debug(f"Table extraction failed for {doc.metadata.filename}: {e}")
                    doc.tables = []
            
            # Extract diagrams
            if diagram_extractor:
                try:
                    diagrams = diagram_extractor.extract_all_diagrams(doc_path)
                    doc.diagrams = diagrams
                    diagrams_found += len(diagrams)
                except Exception as e:
                    logger.debug(f"Diagram extraction failed for {doc.metadata.filename}: {e}")
                    doc.diagrams = []
            
            # Update metadata counts
            doc.metadata.table_count = len(doc.tables)
            doc.metadata.diagram_count = len(doc.diagrams)
            
            parsed_docs.append(doc)
            
        except (FileNotFoundError, ValueError) as e:
            error_msg = f"Failed parsing {doc.metadata.filename}: {str(e)}"
            logger.warning(error_msg)
            errors.append({"stage": "parse", "file": doc.metadata.filename, "error": str(e)})
            # Skip this document, continue with next
        except Exception as e:
            error_msg = f"Unexpected error parsing {doc.metadata.filename}: {str(e)}"
            logger.warning(error_msg)
            errors.append({"stage": "parse", "file": doc.metadata.filename, "error": str(e)})
            # Skip this document, continue with next
    
    success_count = len(parsed_docs)
    total_count = len(documents)
    logger.info(
        f"parse_documents: {success_count}/{total_count} successfully parsed, "
        f"{tables_extracted} tables extracted, {diagrams_found} diagrams found"
    )
    
    return {
        "documents": parsed_docs,
        "documents_parsed": success_count,
        "tables_extracted": tables_extracted,
        "diagrams_found": diagrams_found,
        "errors": errors,
        "status": "documents_parsed",
    }


def extract_metadata(state: IngestionState) -> dict[str, Any]:
    """
    Extract enhanced metadata including document structure and timestamps.
    
    Enhances document metadata with:
    - Section heading extraction (from document text structure)
    - Extraction timestamp
    - Page count
    - Heading count
    
    Args:
        state: Current pipeline state with parsed documents
    
    Returns:
        Dictionary with updated state fields:
        - documents: Documents with enhanced metadata
        - errors: Accumulated error messages
    """
    logger.info("Starting extract_metadata node")
    
    documents: list[ParsedDocument] = state.documents
    errors: list[dict] = list(state.errors)
    heading_extractor = HeadingExtractor()
    now = datetime.now().isoformat()
    
    # Show progress bar if enabled
    if state.show_progress_bar:
        try:
            from tqdm import tqdm
            doc_iter = tqdm(documents, desc="Extracting metadata")
        except ImportError:
            doc_iter = documents
    else:
        doc_iter = documents
    
    for doc in doc_iter:
        try:
            # Extract section headings from full text
            full_text = doc.content if doc.content else "\n".join(doc.page_contents.values())
            section_headings = heading_extractor.extract_headings(full_text)
            
            # Try to extract PDF metadata
            try:
                from pypdf import PdfReader
                doc_path = Path(state.config.document_input_dir) / doc.metadata.filename
                reader = PdfReader(doc_path)
                pdf_metadata = reader.metadata
                
                # Extract author and creation date from PDF
                author = pdf_metadata.author if pdf_metadata and hasattr(pdf_metadata, 'author') else "Unknown"
                created_date = pdf_metadata.creation_date if pdf_metadata and hasattr(pdf_metadata, 'creation_date') else None
                
                doc.metadata.author = author
                doc.metadata.creation_date = str(created_date) if created_date else None
                doc.metadata.page_count = len(reader.pages)
            
            except Exception as e:
                logger.debug(f"Could not extract PDF metadata for {doc.metadata.filename}: {e}")
                # Continue without PDF metadata, not critical
            
            # Update metadata with extracted information
            doc.metadata.section_headings = section_headings
            doc.metadata.heading_count = len(section_headings)
            doc.metadata.extraction_timestamp = now
            
        except Exception as e:
            error_msg = f"Error extracting metadata for {doc.metadata.filename}: {str(e)}"
            logger.warning(error_msg)
            errors.append({"stage": "extract_metadata", "file": doc.metadata.filename, "error": str(e)})
            # Don't fail the pipeline, continue with next document
    
    logger.info(f"extract_metadata: Extracted metadata for {len(documents)} documents")
    
    return {
        "documents": documents,
        "errors": errors,
        "status": "metadata_extracted",
    }


def chunk_text(state: IngestionState) -> dict[str, Any]:
    """
    Split document text into overlapping chunks.
    
    Converts parsed documents into TextChunk objects using LangChain's
    RecursiveCharacterTextSplitter with configurable chunk size and overlap.
    Each chunk preserves metadata about its source document and page number.
    Creates separate chunks for tables and diagram references.
    
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
        errors: list[dict] = list(state.errors)
        errors.append({"stage": "chunk", "error": error_msg})
        return {
            "chunks": [],
            "chunks_created": 0,
            "errors": errors,
            "status": "chunk_text_failed",
        }
    
    documents: list[ParsedDocument] = state.documents
    chunks: list[TextChunk] = []
    errors: list[dict] = list(state.errors)
    chunk_count = 0
    
    # Initialize TextChunker with configured parameters
    chunker = TextChunker(
        chunk_size=state.config.chunk_size,
        overlap=state.config.chunk_overlap,
    )
    
    # Show progress bar if enabled
    if state.show_progress_bar:
        try:
            from tqdm import tqdm
            doc_iter = tqdm(documents, desc="Chunking documents")
        except ImportError:
            doc_iter = documents
    else:
        doc_iter = documents
    
    for doc in doc_iter:
        try:
            # Use TextChunker to split document text
            doc_chunks = chunker.chunk_all(doc)
            chunks.extend(doc_chunks)
            chunk_count += len(doc_chunks)
            
        except Exception as e:
            error_msg = f"Failed chunking {doc.metadata.filename}: {str(e)}"
            logger.warning(error_msg)
            errors.append({"stage": "chunk", "file": doc.metadata.filename, "error": str(e)})
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
    Generate embedding vectors for all text chunks with retry logic.
    
    Calls the configured embedding service (local or remote) to generate
    vector representations for each chunk. Uses batch API calls for efficiency
    and implements exponential backoff retry logic for failed batches.
    Handles API failures gracefully (skip and continue pattern).
    
    Args:
        state: Current pipeline state with text chunks
    
    Returns:
        Dictionary with updated state fields:
        - chunks: Chunks with embedding vectors populated
        - embeddings_generated: Count of successfully embedded chunks
        - batches_re_embedded: Count of batches processed with retries
        - errors: Accumulated error messages
    """
    logger.info("Starting generate_embeddings node")
    
    chunks: list[TextChunk] = state.chunks
    embedded_chunks: list[TextChunk] = []
    errors: list[dict] = list(state.errors)
    
    if not state.config:
        error_msg = "Configuration not available in pipeline state"
        logger.error(error_msg)
        errors.append({"stage": "embedding", "error": error_msg})
        return {
            "chunks": [],
            "embeddings_generated": 0,
            "batches_re_embedded": 0,
            "errors": errors,
            "status": "embeddings_failed",
        }
    
    if not chunks:
        logger.info("No chunks to embed")
        return {
            "chunks": [],
            "embeddings_generated": 0,
            "batches_re_embedded": 0,
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
        errors.append({"stage": "embedding", "error": error_msg})
        return {
            "chunks": [],
            "embeddings_generated": 0,
            "batches_re_embedded": 0,
            "errors": errors,
            "status": "embeddings_failed",
        }
    
    # Process chunks in batches for efficiency
    batch_size = 32
    chunk_failures = 0
    batches_re_embedded = 0
    
    # Show progress bar if enabled
    if state.show_progress_bar:
        try:
            from tqdm import tqdm
            total_batches = (len(chunks) + batch_size - 1) // batch_size
            batch_iter = tqdm(range(0, len(chunks), batch_size), total=total_batches, desc="Embedding")
        except ImportError:
            batch_iter = range(0, len(chunks), batch_size)
    else:
        batch_iter = range(0, len(chunks), batch_size)
    
    for batch_start in batch_iter:
        batch_end = min(batch_start + batch_size, len(chunks))
        batch_chunks = chunks[batch_start:batch_end]
        batch_texts = [chunk.content for chunk in batch_chunks]
        
        # Retry logic with exponential backoff
        max_retries = state.config.embedding_max_retries
        backoff_base = state.config.embedding_retry_backoff_base
        success = False
        
        for attempt in range(max_retries):
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
                    errors.append({"stage": "embedding", "batch": f"{batch_start}-{batch_end}", "error": error_msg})
                    chunk_failures += len(batch_chunks)
                    success = False
                    break
                
                # Attach embeddings to chunks
                for chunk, embedding in zip(batch_chunks, embeddings):
                    chunk.embedding = embedding
                    embedded_chunks.append(chunk)
                
                batches_re_embedded += 1
                success = True
                break  # Success, exit retry loop
                
            except (ConnectionError, ValueError) as e:
                # API error or invalid response
                if attempt < max_retries - 1:
                    wait_time = backoff_base ** attempt
                    logger.warning(
                        f"Embedding batch {batch_start}-{batch_end} attempt {attempt + 1} failed. "
                        f"Retrying in {wait_time}s... ({str(e)})"
                    )
                    import time
                    time.sleep(wait_time)
                else:
                    error_msg = (
                        f"Failed to embed batch {batch_start}-{batch_end} after {max_retries} retries: {str(e)}"
                    )
                    logger.error(error_msg)
                    errors.append({"stage": "embedding", "batch": f"{batch_start}-{batch_end}", "error": error_msg})
                    chunk_failures += len(batch_chunks)
                    success = False
            
            except Exception as e:
                # Unexpected error - skip this batch and continue
                if attempt == max_retries - 1:
                    error_msg = (
                        f"Unexpected error embedding batch {batch_start}-{batch_end}: {str(e)}"
                    )
                    logger.error(error_msg)
                    errors.append({"stage": "embedding", "batch": f"{batch_start}-{batch_end}", "error": error_msg})
                    chunk_failures += len(batch_chunks)
                    success = False
    
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
        "batches_re_embedded": batches_re_embedded,
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
    errors: list[dict] = list(state.errors)
    
    if not state.config:
        error_msg = "Configuration not available in pipeline state"
        logger.error(error_msg)
        errors.append({"stage": "store", "error": error_msg})
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
        
        # Store one document at a time, all-or-nothing. A document's file_hash
        # lives on its chunks in Chroma, so it must only be written when every
        # chunk made it through embedding AND storage. Otherwise change detection
        # would skip a half-ingested document forever.
        by_document: dict[str, list[TextChunk]] = {}
        for chunk in chunks:
            by_document.setdefault(chunk.metadata.source_filename, []).append(chunk)
        
        incomplete_docs = 0
        for filename, doc_chunks in by_document.items():
            expected = doc_chunks[0].metadata.total_chunks
            
            if expected and len(doc_chunks) != expected:
                incomplete_docs += 1
                msg = (
                    f"store_vectors: skipping {filename}: only {len(doc_chunks)}/{expected} "
                    f"chunks were embedded. It will be retried on the next run."
                )
                logger.warning(msg)
                errors.append({"stage": "store", "file": filename, "error": msg})
                continue
            
            # Replace, don't merge: drop any previous version of this document so
            # stale chunks (e.g. from a longer old version) can't linger.
            replaced = vector_store.delete_document(filename)
            if replaced:
                logger.info(f"store_vectors: replaced {replaced} existing chunks for {filename}")
            
            doc_stored = vector_store.store_chunks(doc_chunks)
            if doc_stored != len(doc_chunks):
                # Roll back so no partial document (and no hash) is left behind
                vector_store.delete_document(filename)
                incomplete_docs += 1
                msg = (
                    f"store_vectors: rolled back {filename}: stored {doc_stored}/{len(doc_chunks)} "
                    f"chunks. It will be retried on the next run."
                )
                logger.warning(msg)
                errors.append({"stage": "store", "file": filename, "error": msg})
                continue
            
            stored_count += doc_stored
        
        logger.info(
            f"store_vectors: Stored {stored_count}/{len(chunks)} vectors "
            f"({len(by_document) - incomplete_docs}/{len(by_document)} documents complete)"
        )
    
    except (ConnectionError, RuntimeError) as e:
        # Chroma connection or storage error
        error_msg = f"Failed to store vectors in Chroma: {str(e)}"
        logger.error(error_msg)
        errors.append({"stage": "store", "error": error_msg})
        return {
            "vectors_stored": 0,
            "errors": errors,
            "status": "storage_failed",
        }
    except Exception as e:
        # Unexpected error
        error_msg = f"Unexpected error storing vectors: {str(e)}"
        logger.error(error_msg)
        errors.append({"stage": "store", "error": error_msg})
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


def document_id(filename: str) -> str:
    """
    Short, stable ID for a document, derived from its filename.
    
    Chroma has no document-level ID (chunks are keyed by source filename), so
    the ID is the first 8 hex characters of the filename's SHA256. The same
    filename always gives the same ID, including after re-ingestion.
    """
    return hashlib.sha256(filename.encode("utf-8")).hexdigest()[:8]


def list_documents(state: IngestionState) -> dict[str, Any]:
    """
    List all documents currently stored in the vector database.
    
    Queries the Chroma collection to retrieve all unique documents
    and their metadata.
    
    Args:
        state: Current pipeline state
    
    Returns:
        Dictionary with:
        - documents_in_store: List of document metadata dicts
        - errors: Accumulated error messages
    """
    logger.info("Starting list_documents operation")
    
    errors: list[dict] = list(state.errors)
    documents_in_store = []
    
    if not state.config:
        error_msg = "Configuration not available"
        logger.error(error_msg)
        errors.append({"operation": "list_documents", "error": error_msg})
        return {
            "documents_in_store": [],
            "errors": errors,
        }
    
    try:
        # Initialize Chroma vector store
        vector_store = ChromaVectorStore(
            persist_directory=state.config.chroma_persist_directory,
            collection_name=state.config.chroma_collection_name,
        )
        
        # Initialize the connection to Chroma
        vector_store.initialize_collection()
        
        # Get all documents from collection
        collection = vector_store.client.get_collection(state.config.chroma_collection_name)
        
        # Query all metadata
        all_data = collection.get()
        
        # Extract unique documents
        unique_docs = {}
        for metadata in all_data.get("metadatas", []):
            if metadata and "source_filename" in metadata:
                doc_name = metadata["source_filename"]
                if doc_name not in unique_docs:
                    unique_docs[doc_name] = {
                        "id": document_id(doc_name),
                        "name": doc_name,
                        "chunk_count": 0,
                        "ingested_at": metadata.get("ingested_at", "unknown"),
                        "file_hash": metadata.get("file_hash", "unknown"),
                    }
                unique_docs[doc_name]["chunk_count"] += 1
        
        documents_in_store = sorted(unique_docs.values(), key=lambda d: d["name"].lower())
        
        logger.info(f"list_documents: Found {len(documents_in_store)} documents in store")
    
    except Exception as e:
        error_msg = f"Failed to list documents: {str(e)}"
        logger.error(error_msg)
        errors.append({"operation": "list_documents", "error": error_msg})
    
    return {
        "documents_in_store": documents_in_store,
        "errors": errors,
    }


def delete_document(state: IngestionState, document_name: str) -> dict[str, Any]:
    """
    Delete a document and all its associated vectors from Chroma.
    
    Removes all chunks belonging to the specified document from the
    vector database.
    
    Args:
        state: Current pipeline state
        document_name: Name of document to delete (e.g., "example.pdf")
    
    Returns:
        Dictionary with:
        - deleted_vectors: Number of vectors removed
        - errors: Accumulated error messages
    """
    logger.info(f"Starting delete_document operation for: {document_name}")
    
    errors: list[dict] = list(state.errors)
    deleted_count = 0
    
    if not state.config:
        error_msg = "Configuration not available"
        logger.error(error_msg)
        errors.append({"operation": "delete_document", "error": error_msg})
        return {
            "deleted_vectors": 0,
            "errors": errors,
        }
    
    try:
        # Initialize Chroma vector store
        vector_store = ChromaVectorStore(
            persist_directory=state.config.chroma_persist_directory,
            collection_name=state.config.chroma_collection_name,
        )
        
        # Initialize the connection to Chroma
        vector_store.initialize_collection()
        
        # Get collection
        collection = vector_store.client.get_collection(state.config.chroma_collection_name)
        
        # Find and delete all vectors for this document
        where_filter = {"source_filename": {"$eq": document_name}}
        
        # Get all IDs matching the filter before deletion
        results = collection.get(where=where_filter)
        deleted_count = len(results["ids"]) if results and "ids" in results else 0
        
        # Delete matching vectors
        if deleted_count > 0:
            collection.delete(where=where_filter)
            logger.info(f"delete_document: Deleted {deleted_count} vectors for {document_name}")
        else:
            logger.warning(f"delete_document: No vectors found for document {document_name}")
    
    except Exception as e:
        error_msg = f"Failed to delete document {document_name}: {str(e)}"
        logger.error(error_msg)
        errors.append({"operation": "delete_document", "document": document_name, "error": error_msg})
    
    return {
        "deleted_vectors": deleted_count,
        "errors": errors,
    }


def update_document(state: IngestionState, document_path: str) -> dict[str, Any]:
    """
    Update a document by deleting old vectors and re-ingesting.
    
    First deletes all vectors associated with the document, then
    re-processes it through the pipeline.
    
    Args:
        state: Current pipeline state
        document_path: Path to updated PDF file
    
    Returns:
        Dictionary with:
        - deleted_vectors: Number of old vectors removed
        - newly_ingested: Number of new vectors created
        - errors: Accumulated error messages
    """
    logger.info(f"Starting update_document operation for: {document_path}")
    
    errors: list[dict] = list(state.errors)
    deleted_vectors = 0
    newly_ingested = 0
    
    if not state.config:
        error_msg = "Configuration not available"
        logger.error(error_msg)
        errors.append({"operation": "update_document", "error": error_msg})
        return {
            "deleted_vectors": 0,
            "newly_ingested": 0,
            "errors": errors,
        }
    
    try:
        doc_path = Path(document_path)
        document_name = doc_path.name
        
        # Initialize Chroma vector store
        vector_store = ChromaVectorStore(
            persist_directory=state.config.chroma_persist_directory,
            collection_name=state.config.chroma_collection_name,
        )
        
        # Initialize the connection to Chroma
        vector_store.initialize_collection()
        
        # Get collection
        collection = vector_store.client.get_collection(state.config.chroma_collection_name)
        
        # Delete old vectors
        where_filter = {"source_filename": {"$eq": document_name}}
        results = collection.get(where=where_filter)
        deleted_vectors = len(results["ids"]) if results and "ids" in results else 0
        
        if deleted_vectors > 0:
            collection.delete(where=where_filter)
            logger.info(f"update_document: Deleted {deleted_vectors} old vectors for {document_name}")
        
        # Note: To actually re-ingest, the document needs to be re-added to the documents
        # list and run through the pipeline again. This function removes the old vectors.
        # The caller should handle re-loading the document into the pipeline.
        
        logger.info(
            f"update_document: Deleted {deleted_vectors} vectors. "
            f"Document is ready for re-ingestion (add to documents folder and run pipeline)."
        )
    
    except Exception as e:
        error_msg = f"Failed to update document {document_path}: {str(e)}"
        logger.error(error_msg)
        errors.append({"operation": "update_document", "document": document_name, "error": error_msg})
    
    return {
        "deleted_vectors": deleted_vectors,
        "newly_ingested": 0,  # Caller must re-run pipeline to ingest
        "errors": errors,
    }
