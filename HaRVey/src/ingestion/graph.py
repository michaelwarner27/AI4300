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
    chunk_text,
    generate_embeddings,
    store_vectors,
)
from src.utils.logging import get_logger

logger = get_logger()


def create_ingestion_graph(config: IngestionConfig) -> StateGraph:
    """
    Create and compile the vector ingestion pipeline graph.
    
    The pipeline follows this linear flow:
    
    START
      ↓
    load_documents (discover PDFs and extract metadata)
      ↓
    parse_documents (extract text with pypdf)
      ↓
    chunk_text (split into overlapping chunks)
      ↓
    generate_embeddings (call embedding service)
      ↓
    store_vectors (persist to Chroma)
      ↓
    END
    
    Note: extract_metadata was removed as metadata is already extracted
    during load_documents (filename, page count, author, creation date).
    
    Args:
        config: IngestionConfig object with pipeline configuration
    
    Returns:
        Compiled StateGraph ready for execution
    """
    logger.info("Creating ingestion pipeline graph")
    
    # Create state graph with IngestionState as the state schema
    graph = StateGraph(IngestionState)
    
    # Add all 5 nodes to the graph
    graph.add_node("load_documents", load_documents)
    graph.add_node("parse_documents", parse_documents)
    graph.add_node("chunk_text", chunk_text)
    graph.add_node("generate_embeddings", generate_embeddings)
    graph.add_node("store_vectors", store_vectors)
    
    # Set entry point (first node)
    graph.set_entry_point("load_documents")
    
    # Connect nodes in linear sequence
    graph.add_edge("load_documents", "parse_documents")
    graph.add_edge("parse_documents", "chunk_text")
    graph.add_edge("chunk_text", "generate_embeddings")
    graph.add_edge("generate_embeddings", "store_vectors")
    
    # Set finish point (last node)
    graph.set_finish_point("store_vectors")
    
    logger.info("Graph structure created with 5 nodes and linear connections")
    
    # Compile and return the graph
    compiled_graph = graph.compile()
    logger.info("Graph compiled successfully")
    
    return compiled_graph
