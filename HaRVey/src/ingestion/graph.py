"""
LangGraph definition for the vector ingestion pipeline.
Wires together all nodes into a linear pipeline workflow.
"""

from langgraph.graph import StateGraph
from src.data_models import IngestionState
from src.config import IngestionConfig
from src.ingestion.nodes import (
    load_documents,
    parse_documents,
    extract_metadata,
    chunk_text,
    generate_embeddings,
    store_vectors,
    list_documents,
    delete_document,
    update_document,
)
from src.utils.logging import get_logger

logger = get_logger()


def create_ingestion_graph(config: IngestionConfig) -> StateGraph:
    """
    Create and compile the vector ingestion pipeline graph.
    
    The pipeline follows this linear flow:
    
    START
      ↓
    load_documents (discover PDFs, calculate hashes, detect changes)
      ↓
    parse_documents (extract text, tables, diagrams)
      ↓
    extract_metadata (extract headings, timestamps, PDF metadata)
      ↓
    chunk_text (split into overlapping chunks)
      ↓
    generate_embeddings (call embedding service with retry logic)
      ↓
    store_vectors (persist to Chroma)
      ↓
    END
    
    Additionally provides document management operations:
    - list_documents: Query all documents in vector store
    - delete_document: Remove a document and its vectors
    - update_document: Delete old vectors, prepare for re-ingestion
    
    Args:
        config: IngestionConfig object with pipeline configuration
    
    Returns:
        Compiled StateGraph ready for execution
    """
    logger.info("Creating ingestion pipeline graph")
    
    # Create state graph with IngestionState as the state schema
    graph = StateGraph(IngestionState)
    
    # Add all 6 pipeline nodes
    graph.add_node("load_documents", load_documents)
    graph.add_node("parse_documents", parse_documents)
    graph.add_node("extract_metadata", extract_metadata)
    graph.add_node("chunk_text", chunk_text)
    graph.add_node("generate_embeddings", generate_embeddings)
    graph.add_node("store_vectors", store_vectors)
    
    # Add document management nodes (optional, called separately)
    graph.add_node("list_documents", list_documents)
    graph.add_node("delete_document", delete_document)
    graph.add_node("update_document", update_document)
    
    # Set entry point (first node)
    graph.set_entry_point("load_documents")
    
    # Connect nodes in linear sequence
    graph.add_edge("load_documents", "parse_documents")
    graph.add_edge("parse_documents", "extract_metadata")
    graph.add_edge("extract_metadata", "chunk_text")
    graph.add_edge("chunk_text", "generate_embeddings")
    graph.add_edge("generate_embeddings", "store_vectors")
    
    # Set finish point (last node)
    graph.set_finish_point("store_vectors")
    
    logger.info("Graph structure created with 6 pipeline nodes and 3 management nodes")
    
    # Compile and return the graph
    compiled_graph = graph.compile()
    logger.info("Graph compiled successfully")
    
    return compiled_graph
