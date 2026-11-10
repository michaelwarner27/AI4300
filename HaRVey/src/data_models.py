"""
Data models for the vector ingestion pipeline.
Defines all dataclasses and state structures used throughout the pipeline.
"""

from dataclasses import dataclass, field
from typing import Any
from src.config import IngestionConfig


@dataclass
class DocumentMetadata:
    """Metadata about a PDF document."""
    
    filename: str
    total_pages: int
    processing_timestamp: str
    author: str | None = None
    creation_date: str | None = None


@dataclass
class ParsedDocument:
    """Document after text extraction."""
    
    content: str  # Full document text
    metadata: DocumentMetadata
    pages: list[str]  # Per-page text content


@dataclass
class ChunkMetadata:
    """Metadata attached to each text chunk."""
    
    source_filename: str
    page_number: int
    chunk_sequence: int  # Order within document
    start_char_index: int
    end_char_index: int


@dataclass
class TextChunk:
    """A single chunk of text with optional embedding."""
    
    content: str
    metadata: ChunkMetadata
    embedding: list[float] | None = None


@dataclass
class IngestionState:
    """
    The state passed through all nodes in the LangGraph pipeline.
    
    Accumulates results as documents flow through the pipeline:
    - load_documents: populates documents
    - parse_documents: populates document.content
    - extract_metadata: populates document.metadata
    - chunk_text: creates chunks
    - generate_embeddings: populates chunk.embedding
    - store_vectors: final status update
    """
    
    documents: list[ParsedDocument] = field(default_factory=list)
    chunks: list[TextChunk] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    config: IngestionConfig | None = None
    status: str = "initialized"
    
    # Optional tracking fields for reporting
    documents_loaded: int = 0
    documents_parsed: int = 0
    chunks_created: int = 0
    embeddings_generated: int = 0
    vectors_stored: int = 0


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
