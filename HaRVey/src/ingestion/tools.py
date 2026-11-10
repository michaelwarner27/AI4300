"""
Tool implementations for the vector ingestion pipeline.
Encapsulates external tool interactions in reusable classes.
"""

from typing import Any
from pathlib import Path
from pypdf import PdfReader
from langchain_text_splitters import RecursiveCharacterTextSplitter
import requests
import chromadb
from chromadb.config import Settings
from src.data_models import TextChunk, ChunkMetadata, ParsedDocument, DocumentMetadata
from src.utils.logging import get_logger

logger = get_logger()


class PDFLoader:
    """
    Discovers and loads PDF documents from a directory.
    
    Handles file discovery, metadata extraction, and error handling.
    """
    
    def load_documents(self, directory: str) -> list[ParsedDocument]:
        """
        Load all PDF documents from a directory.
        
        Args:
            directory: Path to directory containing PDF files
        
        Returns:
            List of ParsedDocument objects with metadata loaded
        
        Raises:
            FileNotFoundError: If directory doesn't exist
            ValueError: If directory is empty (no PDFs found)
        """
        dir_path = Path(directory)
        
        # Check if directory exists
        if not dir_path.exists():
            raise FileNotFoundError(f"Document directory not found: {dir_path.absolute()}")
        
        if not dir_path.is_dir():
            raise ValueError(f"Path is not a directory: {dir_path.absolute()}")
        
        # Find all PDF files (case-insensitive)
        pdf_files = list(dir_path.glob("*.pdf")) + list(dir_path.glob("*.PDF"))
        
        # Raise error if no PDFs found
        if not pdf_files:
            raise ValueError(
                f"No PDF files found in {dir_path.absolute()}. "
                f"Directory is empty or contains no .pdf files."
            )
        
        # Load documents
        documents: list[ParsedDocument] = []
        for pdf_path in pdf_files:
            try:
                doc = self._load_single_document(pdf_path)
                documents.append(doc)
            except Exception as e:
                logger.warning(f"Failed to load metadata from {pdf_path.name}: {str(e)}")
                # Still create document with basic metadata
                doc = ParsedDocument(
                    content="",
                    metadata=DocumentMetadata(
                        filename=pdf_path.name,
                        total_pages=0,  # Will be determined during parsing
                        processing_timestamp="",
                    ),
                    pages=[],
                )
                documents.append(doc)
        
        logger.info(f"Loaded {len(documents)} PDF documents from {dir_path}")
        return documents
    
    def _load_single_document(self, pdf_path: Path) -> ParsedDocument:
        """
        Load metadata for a single PDF document.
        
        Args:
            pdf_path: Path to PDF file
        
        Returns:
            ParsedDocument with metadata populated
        """
        # Get basic file info
        filename = pdf_path.name
        
        # Open PDF and extract metadata
        try:
            reader = PdfReader(pdf_path)
            total_pages = len(reader.pages)
            
            # Try to extract document metadata
            metadata = reader.metadata
            author = metadata.author if metadata and hasattr(metadata, 'author') else None
            creation_date = metadata.creation_date if metadata and hasattr(metadata, 'creation_date') else None
        except Exception as e:
            logger.debug(f"Could not extract PDF metadata from {filename}: {str(e)}")
            total_pages = 0
            author = None
            creation_date = None
        
        # Create document with metadata
        doc = ParsedDocument(
            content="",  # Will be populated in parse_documents node
            metadata=DocumentMetadata(
                filename=filename,
                total_pages=total_pages,
                processing_timestamp="",  # Will be set during processing
                author=author,
                creation_date=str(creation_date) if creation_date else None,
            ),
            pages=[],  # Will be populated in parse_documents node
        )
        
        return doc


class PDFParser:
    """
    Wrapper around pypdf for text extraction from PDF documents.
    
    Extracts text from each page while preserving page boundaries and
    handling errors gracefully.
    """
    
    def parse_document(self, filepath: str) -> tuple[str, list[str]]:
        """
        Extract text from a PDF document.
        
        Extracts all text content from each PDF page, preserves page
        boundaries and ordering, and handles special characters/encoding.
        
        Args:
            filepath: Path to the PDF file
        
        Returns:
            Tuple of (full_text, per_page_texts)
            - full_text: Concatenated text from all pages
            - per_page_texts: List of text strings, one per page
        
        Raises:
            FileNotFoundError: If PDF file doesn't exist
            ValueError: If PDF cannot be parsed
        """
        pdf_path = Path(filepath)
        
        # Check file exists
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {filepath}")
        
        # Check file is readable
        if not pdf_path.is_file():
            raise ValueError(f"Path is not a file: {filepath}")
        
        try:
            reader = PdfReader(pdf_path)
            total_pages = len(reader.pages)
            
            if total_pages == 0:
                logger.warning(f"PDF has no pages: {pdf_path.name}")
                return "", []
            
            # Extract text from each page
            per_page_texts: list[str] = []
            full_text_parts: list[str] = []
            
            for page_num, page in enumerate(reader.pages, 1):
                try:
                    # Extract text from page
                    page_text = page.extract_text()
                    
                    if not page_text or not page_text.strip():
                        # Handle image-only or empty pages
                        logger.warning(
                            f"No text extracted from page {page_num} "
                            f"in {pdf_path.name} (possibly image-only)"
                        )
                        page_text = ""
                    
                    per_page_texts.append(page_text)
                    full_text_parts.append(page_text)
                    
                except Exception as e:
                    error_msg = (
                        f"Failed to extract text from page {page_num} "
                        f"in {pdf_path.name}: {str(e)}"
                    )
                    logger.warning(error_msg)
                    # Still add empty page to maintain page numbering
                    per_page_texts.append("")
                    # Continue with next page
            
            # Combine all page texts
            full_text = "\n\n".join(full_text_parts)
            
            logger.debug(
                f"Parsed {pdf_path.name}: {total_pages} pages, "
                f"{len(full_text)} characters extracted"
            )
            
            return full_text, per_page_texts
            
        except PdfReader.PdfReadError as e:
            raise ValueError(f"Invalid PDF file {pdf_path.name}: {str(e)}")
        except Exception as e:
            raise ValueError(f"Error parsing PDF {pdf_path.name}: {str(e)}")


class TextChunker:
    """
    Split text into overlapping chunks for embedding using LangChain.
    
    Uses RecursiveCharacterTextSplitter for intelligent text splitting that
    respects document structure (paragraphs, sentences, words).
    """
    
    def __init__(self, chunk_size: int, overlap: int) -> None:
        """
        Initialize the text chunker.
        
        Args:
            chunk_size: Target size for each chunk (in characters)
            overlap: Overlap between consecutive chunks (in characters)
        """
        self.chunk_size = chunk_size
        self.overlap = overlap
        
        # Initialize LangChain's RecursiveCharacterTextSplitter
        # Splits recursively on: "\n\n", "\n", " ", ""
        # This respects document structure better than simple character splitting
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=overlap,
            length_function=len,
            separators=["\n\n", "\n", " ", ""],
        )
        
        logger.debug(
            f"TextChunker initialized with LangChain RecursiveCharacterTextSplitter: "
            f"chunk_size={chunk_size}, overlap={overlap}"
        )
    
    def chunk_document(
        self, 
        document: ParsedDocument,
    ) -> list[TextChunk]:
        """
        Split document text into overlapping chunks with metadata.
        
        Args:
            document: ParsedDocument object with content and metadata
        
        Returns:
            List of TextChunk objects with chunk-level metadata
        """
        filename = document.metadata.filename
        logger.debug(f"TextChunker.chunk_document() called for {filename}")
        
        if not document.content or not document.content.strip():
            logger.warning(f"Document {filename} has no content to chunk")
            return []
        
        chunks: list[TextChunk] = []
        
        try:
            # Split text using LangChain splitter
            split_texts = self.splitter.split_text(document.content)
            
            if not split_texts:
                logger.warning(f"No chunks created from {filename}")
                return []
            
            # Create TextChunk objects with metadata
            for chunk_idx, chunk_text in enumerate(split_texts):
                # Determine which page(s) this chunk came from
                # by finding its position in the original text
                char_index = document.content.find(chunk_text)
                
                # Determine page number based on position in per-page texts
                page_number = self._find_page_number(
                    document.pages, 
                    char_index
                )
                
                chunk = TextChunk(
                    content=chunk_text,
                    metadata=ChunkMetadata(
                        source_filename=filename,
                        page_number=page_number,
                        chunk_sequence=chunk_idx,
                        start_char_index=char_index,
                        end_char_index=char_index + len(chunk_text),
                    ),
                )
                chunks.append(chunk)
            
            logger.debug(
                f"Created {len(chunks)} chunks from {filename} "
                f"({len(document.content)} characters)"
            )
            
        except Exception as e:
            logger.error(f"Error chunking {filename}: {str(e)}")
            raise
        
        return chunks
    
    def _find_page_number(
        self, 
        per_page_texts: list[str], 
        char_index: int
    ) -> int:
        """
        Determine which page a character position belongs to.
        
        Args:
            per_page_texts: List of per-page text strings
            char_index: Character index in concatenated text
        
        Returns:
            Page number (1-indexed)
        """
        if not per_page_texts:
            return 1
        
        cumulative_chars = 0
        for page_num, page_text in enumerate(per_page_texts, 1):
            cumulative_chars += len(page_text) + 2  # +2 for "\n\n" separator
            if char_index < cumulative_chars:
                return page_num
        
        # Default to last page if not found
        return len(per_page_texts)


class EmbeddingService:
    """
    Interface to embedding service (local or remote).
    
    Configured to use the embedding service specified in .env.
    Makes HTTP calls to embedding API endpoint for vector generation.
    """
    
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
    ) -> None:
        """
        Initialize embedding service client.
        
        Args:
            base_url: Base URL of embedding service (e.g., http://localhost:4000/v1)
            model: Model identifier (e.g., Qwen3-Embedding-8B)
            api_key: API key for authentication
        """
        self.base_url = base_url
        self.model = model
        self.api_key = api_key
        logger.debug(f"EmbeddingService initialized: {base_url}, model={model}")
    
    def embed_text(self, text: str) -> list[float]:
        """
        Generate embedding vector for a single text.
        
        Args:
            text: Text to embed
        
        Returns:
            Embedding vector as list of floats
        
        Raises:
            ConnectionError: If unable to reach embedding service
            ValueError: If embedding fails
        """
        logger.debug(f"EmbeddingService.embed_text() called for {len(text)} chars")
        
        # Use embed_batch for single text
        embeddings = self.embed_batch([text])
        return embeddings[0] if embeddings else []
    
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Generate embeddings for multiple texts efficiently.
        
        Makes a single HTTP request to the embedding service API with multiple texts.
        
        Args:
            texts: List of texts to embed
        
        Returns:
            List of embedding vectors, one per input text
            
        Raises:
            ConnectionError: If unable to reach embedding service
            ValueError: If API returns invalid response
        """
        if not texts:
            logger.warning("EmbeddingService.embed_batch() called with empty list")
            return []
        
        logger.debug(f"EmbeddingService.embed_batch() called for {len(texts)} texts")
        
        # Prepare API request
        endpoint = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "input": texts,
        }
        
        try:
            # Make HTTP request to embedding service
            response = requests.post(endpoint, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            
            # Parse response
            response_data = response.json()
            
            # Extract embeddings from response
            # Expected format: {"data": [{"embedding": [...]}, ...]}
            if "data" not in response_data:
                raise ValueError(f"Invalid API response: missing 'data' field")
            
            embeddings_data = response_data["data"]
            
            # Validate we got embeddings for all texts
            if len(embeddings_data) != len(texts):
                logger.warning(
                    f"API returned {len(embeddings_data)} embeddings for {len(texts)} texts"
                )
            
            # Extract embedding vectors
            embeddings = []
            for item in embeddings_data:
                if "embedding" not in item:
                    raise ValueError(f"Invalid API response: missing 'embedding' field in data item")
                embeddings.append(item["embedding"])
            
            logger.debug(f"Successfully generated {len(embeddings)} embeddings")
            return embeddings
            
        except requests.exceptions.ConnectionError as e:
            error_msg = f"Failed to connect to embedding service at {endpoint}: {str(e)}"
            logger.error(error_msg)
            raise ConnectionError(error_msg) from e
        except requests.exceptions.Timeout as e:
            error_msg = f"Embedding service request timed out: {str(e)}"
            logger.error(error_msg)
            raise ConnectionError(error_msg) from e
        except requests.exceptions.HTTPError as e:
            error_msg = f"Embedding service returned HTTP {response.status_code}: {response.text}"
            logger.error(error_msg)
            raise ValueError(error_msg) from e
        except ValueError as e:
            # Re-raise ValueError for invalid response format
            logger.error(f"Invalid embedding service response: {str(e)}")
            raise
        except Exception as e:
            error_msg = f"Unexpected error calling embedding service: {str(e)}"
            logger.error(error_msg)
            raise ValueError(error_msg) from e


class ChromaVectorStore:
    """
    Interface to Chroma vector database.
    
    Manages persistent storage of embedding vectors and metadata.
    Supports similarity search operations and metadata-based filtering.
    
    **Storage Features:**
    - Vector storage with indexing for efficient similarity search
    - Metadata storage alongside vectors (source_filename, page_number, chunk_sequence)
    - Filtering by source document filename
    - Filtering by page number
    - Support for metadata-based queries
    - Cosine similarity distance metric (default)
    - Persistent filesystem-based storage
    """
    
    def __init__(
        self,
        persist_directory: str,
        collection_name: str,
    ) -> None:
        """
        Initialize Chroma vector store client.
        
        Args:
            persist_directory: Directory path for persistent storage (e.g., ./chroma_db)
            collection_name: Name of the collection to store vectors in
                (default: "vector_ingestion_collection")
        """
        self.persist_directory = persist_directory
        self.collection_name = collection_name
        self.client = None
        self.collection = None
        logger.debug(
            f"ChromaVectorStore initialized: dir={persist_directory}, "
            f"collection={collection_name}"
        )
    
    def initialize_collection(self) -> None:
        """
        Connect to or create the Chroma collection.
        
        Creates persistent connection to Chroma database at the configured
        persist_directory and initializes/accesses the collection with cosine
        similarity distance metric.
        
        Raises:
            ConnectionError: If unable to connect to Chroma database
        """
        try:
            # Create persist directory if it doesn't exist
            persist_path = Path(self.persist_directory)
            persist_path.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Chroma persist directory: {persist_path.absolute()}")
            
            # Create Chroma client with persistent storage
            settings = Settings(
                is_persistent=True,
                persist_directory=str(persist_path.absolute()),
                allow_reset=True,
            )
            
            self.client = chromadb.Client(settings)
            logger.debug("Chroma client created with persistent storage")
            
            # Get or create collection with cosine similarity distance metric
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            
            logger.info(
                f"Chroma collection '{self.collection_name}' initialized at "
                f"{persist_path.absolute()}"
            )
            
        except Exception as e:
            error_msg = f"Failed to initialize Chroma collection: {str(e)}"
            logger.error(error_msg)
            raise ConnectionError(error_msg) from e
    
    def store_chunks(self, chunks: list[TextChunk]) -> int:
        """
        Store text chunks with embeddings and metadata in Chroma collection.
        
        Persists all chunks with their embedding vectors and metadata to enable
        similarity search and metadata-based filtering. Each chunk is stored with
        a unique ID in format: "{filename}_{page}_{chunk_seq}".
        
        Uses batch processing to respect Chroma's maximum batch size limit (5461).
        Chunks are stored in batches of 5000 to ensure reliable storage.
        
        **Stored Metadata per chunk:**
        - source_filename: Original PDF filename
        - page_number: Page number in original document
        - chunk_sequence: Chunk order within document
        - start_char_index: Starting character position in source text
        - end_char_index: Ending character position in source text
        
        **Indexing:**
        - Vectors indexed for cosine similarity search
        - Metadata indexed to support filtering queries
        
        **Batch Processing:**
        - Maximum 5000 chunks per batch (Chroma limit: 5461)
        - Partial failures handled: failed batches logged, pipeline continues
        - Returns count of successfully stored chunks
        
        Args:
            chunks: List of TextChunk objects with embedding vectors populated
        
        Returns:
            Number of chunks successfully stored in Chroma
        
        Raises:
            RuntimeError: If storage operation fails
            ValueError: If chunks missing embeddings or metadata
        """
        if not chunks:
            logger.warning("No chunks to store in Chroma")
            return 0
        
        if not self.collection:
            raise RuntimeError("Collection not initialized. Call initialize_collection() first.")
        
        try:
            # First pass: validate and prepare all chunks
            validated_chunks = []
            for chunk in chunks:
                # Validate chunk has embedding
                if chunk.embedding is None:
                    logger.warning(
                        f"Skipping chunk {chunk.metadata.chunk_sequence} "
                        f"from {chunk.metadata.source_filename}: no embedding"
                    )
                    continue
                validated_chunks.append(chunk)
            
            if not validated_chunks:
                logger.warning("No valid chunks with embeddings to store")
                return 0
            
            logger.info(f"Storing {len(validated_chunks)} chunks in Chroma (validated, {len(chunks) - len(validated_chunks)} skipped)")
            
            # Second pass: batch store chunks (5000 per batch to respect Chroma limit)
            batch_size = 5000
            total_stored = 0
            batch_failures = 0
            
            for batch_start in range(0, len(validated_chunks), batch_size):
                batch_end = min(batch_start + batch_size, len(validated_chunks))
                batch_chunks = validated_chunks[batch_start:batch_end]
                
                try:
                    # Prepare batch data for Chroma
                    ids = []
                    embeddings = []
                    documents = []
                    metadatas = []
                    
                    for chunk in batch_chunks:
                        # Create unique ID in format: {filename}_{page}_{chunk_seq}
                        chunk_id = (
                            f"{chunk.metadata.source_filename}_"
                            f"{chunk.metadata.page_number}_"
                            f"{chunk.metadata.chunk_sequence}"
                        )
                        
                        # Prepare metadata dict
                        metadata_dict = {
                            "source_filename": chunk.metadata.source_filename,
                            "page_number": chunk.metadata.page_number,
                            "chunk_sequence": chunk.metadata.chunk_sequence,
                            "start_char_index": chunk.metadata.start_char_index,
                            "end_char_index": chunk.metadata.end_char_index,
                        }
                        
                        ids.append(chunk_id)
                        embeddings.append(chunk.embedding)
                        documents.append(chunk.content)
                        metadatas.append(metadata_dict)
                    
                    # Store batch in Chroma
                    self.collection.add(
                        ids=ids,
                        embeddings=embeddings,
                        documents=documents,
                        metadatas=metadatas,
                    )
                    
                    batch_stored = len(ids)
                    total_stored += batch_stored
                    logger.debug(
                        f"Stored batch {batch_start // batch_size + 1}: "
                        f"{batch_stored} chunks (indices {batch_start}-{batch_end})"
                    )
                    
                except Exception as e:
                    batch_failures += 1
                    error_msg = (
                        f"Failed to store batch of {len(batch_chunks)} chunks "
                        f"(indices {batch_start}-{batch_end}): {str(e)}"
                    )
                    logger.error(error_msg)
                    # Continue with next batch instead of failing
            
            logger.info(
                f"Successfully stored {total_stored}/{len(validated_chunks)} chunks in Chroma collection"
            )
            
            if batch_failures > 0:
                logger.warning(f"{batch_failures} batch(es) failed to store")
            
            return total_stored
            
        except Exception as e:
            error_msg = f"Failed to store chunks in Chroma: {str(e)}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e
    
    def query_similarity(
        self,
        query_embedding: list[float],
        k: int = 10,
        min_similarity_score: float | None = None,
    ) -> list[dict[str, Any]]:
        """
        Query the vector store for semantically similar documents.
        
        Performs a similarity search on the stored embeddings using the provided
        query embedding vector. Returns the top K most similar documents along
        with their metadata and similarity scores.
        
        Supports optional similarity score filtering to prevent retrieval of
        irrelevant results (e.g., when similarity is below a threshold).
        
        **Returned Result Format:**
        Each result is a dictionary containing:
        - "id": Unique identifier for the chunk (format: "{filename}_{page}_{chunk_seq}")
        - "content": The text content of the chunk
        - "metadata": Dictionary with:
          - source_filename: Original PDF filename
          - page_number: Page number in original document
          - chunk_sequence: Chunk order within document
          - start_char_index: Starting character position in source text
          - end_char_index: Ending character position in source text
        - "similarity_score": Cosine similarity score (0.0 to 1.0)
        
        Args:
            query_embedding: Vector embedding of the query (list of floats)
            k: Number of top results to return (default: 10)
            min_similarity_score: Minimum similarity score threshold for filtering.
                Results below this score will be excluded. If None, all top K results
                are returned regardless of score. Typical range: 0.0 to 1.0
        
        Returns:
            List of dictionaries with search results, sorted by similarity score
            (highest first). Returns empty list if no results found or all results
            filtered below threshold.
        
        Raises:
            RuntimeError: If collection not initialized
            ValueError: If query_embedding is empty or invalid
        """
        if not self.collection:
            raise RuntimeError("Collection not initialized. Call initialize_collection() first.")
        
        if not query_embedding:
            raise ValueError("Query embedding cannot be empty")
        
        try:
            logger.debug(f"Querying vector store for top {k} results (min_similarity={min_similarity_score})")
            
            # Query the collection
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=k,
            )
            
            # Transform Chroma results into our format
            # Chroma returns results in format:
            # {"ids": [[...]], "documents": [[...]], "metadatas": [[...]], "distances": [[...]]}
            # where distances are cosine distances (we convert to similarity scores)
            
            if not results or not results["ids"] or not results["ids"][0]:
                logger.info("No results found in vector store")
                return []
            
            # Extract single query results (we only sent one query)
            query_results = []
            ids = results["ids"][0]
            documents = results["documents"][0]
            metadatas = results["metadatas"][0]
            distances = results["distances"][0]  # Cosine distances
            
            for chunk_id, document, metadata, distance in zip(ids, documents, metadatas, distances):
                # Convert cosine distance to similarity score (1 - distance)
                similarity_score = 1 - distance
                
                # Filter by minimum similarity threshold if specified
                if min_similarity_score is not None:
                    if similarity_score < min_similarity_score:
                        logger.debug(
                            f"Filtered result {chunk_id} (similarity={similarity_score:.4f} "
                            f"below threshold={min_similarity_score})"
                        )
                        continue
                
                # Build result dictionary
                result = {
                    "id": chunk_id,
                    "content": document,
                    "metadata": metadata,
                    "similarity_score": similarity_score,
                }
                query_results.append(result)
            
            logger.info(
                f"Query returned {len(query_results)} results "
                f"(retrieved {len(ids)}, after filtering)"
            )
            
            return query_results
            
        except Exception as e:
            error_msg = f"Failed to query vector store: {str(e)}"
            logger.error(error_msg)
            raise RuntimeError(error_msg) from e
