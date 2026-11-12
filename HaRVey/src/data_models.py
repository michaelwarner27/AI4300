"""
Data models for the vector ingestion pipeline.
Defines all dataclasses and state structures used throughout the pipeline.
"""

from dataclasses import dataclass, field
from typing import Any
from datetime import datetime
from src.config import IngestionConfig


@dataclass
class TableData:
    """Structured table extracted from PDF."""
    
    content: str  # Markdown-formatted table
    page: int
    page_position: int  # Order on page
    row_count: int
    col_count: int
    raw_data: list[list[str]]  # For future structured access


@dataclass
class DiagramMetadata:
    """Metadata about a diagram/image extracted from PDF."""
    
    page: int
    position_index: int  # Order on page
    image_path: str  # Path to extracted image
    caption: str = ""  # Adjacent text
    type: str = "unknown"  # Image file format: png, jpg, ...
    kind: str = "raster"  # "raster" (embedded image) or "vector_figure" (rendered drawing)


@dataclass
class DocumentMetadata:
    """Metadata about a PDF document."""
    
    filename: str
    total_pages: int
    processing_timestamp: str
    author: str | None = None
    creation_date: str | None = None
    file_hash: str = ""  # SHA256 hash for change detection
    file_size_bytes: int = 0
    loaded_at: str = ""  # ISO format timestamp
    extraction_timestamp: str = ""  # ISO format timestamp
    section_headings: list[str] = field(default_factory=list)
    table_count: int = 0
    diagram_count: int = 0
    heading_count: int = 0
    page_count: int = 0


@dataclass
class ParsedDocument:
    """Document after text extraction."""
    
    content: str  # Full document text
    metadata: DocumentMetadata
    pages: list[str]  # Per-page text content
    filename: str = ""
    page_contents: dict[int, str] = field(default_factory=dict)  # Page number -> text
    tables: list[TableData] = field(default_factory=list)
    diagrams: list[DiagramMetadata] = field(default_factory=list)


@dataclass
class ChunkMetadata:
    """Metadata attached to each text chunk."""
    
    source_filename: str
    page_number: int
    chunk_sequence: int  # Order within document
    start_char_index: int = 0
    end_char_index: int = 0
    chunk_type: str = "text"  # text, table, diagram_reference
    document: str = ""
    source: str = ""
    section_headings: list[str] = field(default_factory=list)
    # Table-specific
    table_rows: int = 0
    table_cols: int = 0
    # Diagram-specific
    diagram_path: str = ""
    diagram_kind: str = ""  # "raster" or "vector_figure"
    # Change detection / ingestion bookkeeping (persisted to Chroma)
    file_hash: str = ""  # SHA256 of the source PDF
    ingested_at: str = ""  # ISO timestamp of ingestion
    total_chunks: int = 0  # Chunks the document produced (for completeness check)


@dataclass
class TextChunk:
    """A single chunk of text with optional embedding."""
    
    content: str
    metadata: ChunkMetadata | dict[str, Any]  # Support both dataclass and dict
    embedding: list[float] | None = None
    
    def __post_init__(self):
        """Convert dict metadata to ChunkMetadata if needed."""
        if isinstance(self.metadata, dict):
            # Keep as dict for flexibility during pipeline
            pass


@dataclass
class IngestionState:
    """
    The state passed through all nodes in the LangGraph pipeline.
    
    Accumulates results as documents flow through the pipeline:
    - load_documents: populates documents with hashes
    - parse_documents: populates document.content, tables, diagrams
    - extract_metadata: populates document.metadata with headings
    - chunk_text: creates chunks (text/table/diagram types)
    - generate_embeddings: populates chunk.embedding with retry logic
    - store_vectors: final status update
    """
    
    documents: list[ParsedDocument] = field(default_factory=list)
    chunks: list[TextChunk] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    config: IngestionConfig | None = None
    status: str = "initialized"
    
    # Progress tracking fields
    documents_loaded: int = 0
    documents_parsed: int = 0
    chunks_created: int = 0
    embeddings_generated: int = 0
    vectors_stored: int = 0
    
    # Enhanced tracking
    documents_skipped_duplicate: int = 0  # Unchanged documents
    tables_extracted: int = 0
    diagrams_found: int = 0
    batches_re_embedded: int = 0
    
    # Configuration flags
    show_progress_bar: bool = True


@dataclass
class QueryResult:
    """A single retrieved document result from vector search."""
    
    content: str
    metadata: ChunkMetadata
    similarity_score: float


@dataclass
class ToolCall:
    """Tool call made by LLM."""
    
    tool_name: str  # "respond_to_user" or "insufficient_evidence"
    parameters: dict  # Tool parameters


@dataclass
class QueryState:
    """
    The state passed through all nodes in the LangGraph query pipeline.
    
    Accumulates results as user queries flow through the pipeline:
    - user_query: receives and validates user query input
    - embed_query: creates vector embedding from query
    - vector_store_search: retrieves similar documents from Chroma
    - generate_response: uses LLM to synthesize response from query + results
    - process_tool_call: processes tool call and formats final response
    """
    
    original_query: str | None = None  # User's original question
    query_validated: bool = False  # Whether query passed validation
    query_embedding: list[float] | None = None  # Vector embedding of query
    search_results: list[QueryResult] = field(default_factory=list)  # Top K results from vector store
    tool_call: ToolCall | None = None  # LLM tool call result
    final_response: str | None = None  # Formatted final response
    errors: list[str] = field(default_factory=list)  # Accumulated error messages
    config: IngestionConfig | None = None  # Configuration
    status: str = "initialized"  # Current pipeline status
