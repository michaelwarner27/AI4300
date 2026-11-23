"""
Data models for the ingestion and query pipelines.
Defines all dataclasses and state structures used by both graphs.
"""

from dataclasses import dataclass, field
from typing import Any
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
    bbox: tuple[float, float, float, float] | None = None  # (x0, top, x1, bottom) in PDF points
    heading_path: list[str] = field(default_factory=list)  # Filled in by layout analysis


@dataclass
class SectionHeading:
    """A section heading found by layout analysis."""

    text: str
    level: int  # 1 = top-level section, 2 = sub-section, ...
    page: int  # 0-indexed
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


@dataclass
class DocumentSection:
    """
    Text between one heading and the next, in reading order.

    The first section of a document (text before any heading) has an empty path.
    """

    path: list[str]  # Heading path, outermost first
    text: str  # Section text, starting with its own heading line
    page_offsets: list[tuple[int, int]] = field(default_factory=list)  # (char offset, 0-indexed page)

    def page_at(self, offset: int) -> int:
        """0-indexed page of the character at offset within this section's text."""
        page = self.page_offsets[0][1] if self.page_offsets else 0
        for start, p in self.page_offsets:
            if start > offset:
                break
            page = p
        return page


@dataclass
class DiagramMetadata:
    """Metadata about a diagram/image extracted from PDF."""

    page: int
    position_index: int  # Order on page
    image_path: str  # Path to extracted image
    caption: str = ""  # Text inside the figure region
    type: str = "unknown"  # Image file format: png, jpg, ...
    kind: str = "raster"  # "raster" (embedded image) or "vector_figure" (rendered drawing)
    bbox: tuple[float, float, float, float] | None = None  # Position on the page (PDF points)
    # Filled in by layout analysis (extract_metadata)
    heading_path: list[str] = field(default_factory=list)  # e.g. ["MAINTENANCE", "A. Air Filter"]
    label: str = ""  # Figure label next to the figure, e.g. "FIG. 12 Thermostat Wiring"
    context: str = ""  # Text between the previous heading and the figure (capped)


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
    headings: list[SectionHeading] = field(default_factory=list)  # Positioned headings, reading order
    sections: list[DocumentSection] = field(default_factory=list)  # Text split at headings (layout analysis)


@dataclass
class ChunkMetadata:
    """Metadata attached to each text chunk."""
    
    source_filename: str
    page_number: int
    chunk_sequence: int  # Order within document
    start_char_index: int = 0
    end_char_index: int = 0
    chunk_type: str = "text"  # text, table, diagram_reference
    section_headings: list[str] = field(default_factory=list)  # Heading path, outermost first
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
    is_neighbor: bool = False  # Fetched as context next to a hit, not matched itself
    from_history: bool = False  # Carried over from an earlier turn of the conversation


@dataclass
class ChatTurn:
    """One completed question/answer exchange, kept as conversation history."""

    question: str  # The user's message as typed
    answer: str  # Clean answer text (answer + steps; no sources or file paths)
    kind: str = "answer"  # "answer" | "insufficient" | "clarify" (QueryState.response["kind"])
    documents: list[QueryResult] = field(default_factory=list)  # Chunks the answer cited (QueryState.source_documents)


@dataclass
class ToolCall:
    """Tool call made by LLM."""
    
    tool_name: str  # "respond_to_user", "insufficient_evidence" or "search_documents"
    parameters: dict  # Tool parameters
    tool_call_id: str | None = None  # Provider id, echoed back in the tool result message


@dataclass
class QueryState:
    """
    The state passed through all nodes in the LangGraph query pipeline.
    
    Accumulates results as user queries flow through the pipeline:
    - user_query: receives and validates user query input
    - rewrite_query: turns a follow-up into a standalone retrieval_query (needs chat history)
    - embed_query: creates vector embedding from the (rewritten) query
    - vector_store_search: retrieves similar documents from Chroma, plus neighbors and page diagrams
    - add_history_documents: carries documents cited in recent turns into search_results
    - generate_response: one LLM turn with tools (messages, search_round, started_at)
    - search_more: runs search_documents / read_section / read_page and loops back
    - process_tool_call: turns the final tool call into response / final_response
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
    messages: list[dict] = field(default_factory=list)  # LLM conversation (agent loop)
    search_round: int = 0  # Number of search_documents calls executed
    started_at: float | None = None  # Wall-clock start of the agent loop (time.time())
    chat_history: list[ChatTurn] = field(default_factory=list)  # Earlier turns, oldest first
    retrieval_query: str | None = None  # Standalone query used for embedding (rewritten follow-up)
    source_documents: list[QueryResult] = field(default_factory=list)  # Chunks the final answer cited
    # Structured final answer: {"kind", "answer", "steps", "sources", "diagrams", "options"}
    # (see process_tool_call); final_response is its text rendering
    response: dict | None = None
