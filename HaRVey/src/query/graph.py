"""
LangGraph definition for the vector query pipeline.
Wires together all query nodes with conditional routing logic.
"""

from langgraph.graph import StateGraph
from src.data_models import QueryState
from src.config import IngestionConfig
from src.query.nodes import (
    user_query,
    embed_query,
    vector_store_search,
    generate_response,
    process_tool_call,
)
from src.utils.logging import get_logger

logger = get_logger()


def _should_generate_response(state: QueryState) -> str:
    """
    Conditional routing function: determines if response generation should proceed.
    
    Decision Logic:
    - If search_results is not empty: route to "generate" (generate_response node)
    - If search_results is empty: route to "skip" (output node, no LLM call)
    
    This prevents unnecessary LLM calls when no relevant documents were found.
    Both paths eventually lead to the output node which returns the final state.
    
    Args:
        state: Current query pipeline state
    
    Returns:
        "generate" if search results exist and should be synthesized
        "skip" if no search results (fallback to raw documents)
    """
    if state.search_results and len(state.search_results) > 0:
        logger.debug(f"Routing to generate_response ({len(state.search_results)} results found)")
        return "generate"
    else:
        logger.debug("No search results found, skipping LLM response generation")
        return "skip"


def create_query_graph(config: IngestionConfig) -> StateGraph:
    """
    Create and compile the vector query pipeline graph.
    
    The pipeline follows this flow with conditional routing:
    
    START
      ↓
    user_query (validate input)
      ↓
    embed_query (generate embedding)
      ↓
    vector_store_search (query Chroma with K=10 and similarity filtering)
      ↓
    [Conditional: Results found?]
      ├─ YES → generate_response (LLM synthesis with sources)
      └─ NO → output (skip LLM, return status)
      ↓
    END
    
    Error Handling:
    - Any node can fail and accumulate errors in state.errors
    - Pipeline continues through remaining nodes
    - Final state contains all errors and available results
    
    Timeouts:
    - embed_query: 30 seconds (via EmbeddingService)
    - vector_store_search: 30 seconds (via Chroma)
    - generate_response: 120 seconds (via LLMService)
    
    Args:
        config: IngestionConfig object with pipeline configuration
    
    Returns:
        Compiled StateGraph ready for execution
    """
    logger.info("Creating query pipeline graph")
    
    # Create state graph with QueryState as the state schema
    graph = StateGraph(QueryState)
    
    # Add all 5 nodes to the graph
    graph.add_node("user_query", user_query)
    graph.add_node("embed_query", embed_query)
    graph.add_node("vector_store_search", vector_store_search)
    graph.add_node("generate_response", generate_response)
    graph.add_node("process_tool_call", process_tool_call)
    
    # Set entry point (first node)
    graph.set_entry_point("user_query")
    
    # Connect nodes in sequence with conditional routing
    graph.add_edge("user_query", "embed_query")
    graph.add_edge("embed_query", "vector_store_search")
    
    # Conditional routing from vector_store_search
    graph.add_conditional_edges(
        "vector_store_search",
        _should_generate_response,
        {
            "generate": "generate_response",
            "skip": "__end__",
        }
    )
    
    # Connect generate_response to tool call processor
    graph.add_edge("generate_response", "process_tool_call")
    
    # Connect tool processor to end
    graph.add_edge("process_tool_call", "__end__")
    
    logger.info("Graph structure created with 4 nodes and conditional routing")
    
    # Compile and return the graph
    compiled_graph = graph.compile()
    logger.info("Graph compiled successfully")
    
    return compiled_graph


def execute_query_pipeline(
    query: str,
    config: IngestionConfig,
    graph: StateGraph,
) -> QueryState:
    """
    Execute the query pipeline with the given query and configuration.
    
    Initializes the pipeline state with the user query and configuration,
    then invokes the compiled graph to process the query through all nodes.
    
    Args:
        query: User's query string
        config: IngestionConfig with embedding/LLM/Chroma settings
        graph: Compiled StateGraph from create_query_graph()
    
    Returns:
        Final QueryState with all fields populated:
        - original_query: The user's query
        - query_validated: Whether validation passed
        - query_embedding: Vector embedding (if successful)
        - search_results: Retrieved QueryResult objects (if found)
        - final_response: LLM response or fallback (if generated)
        - errors: List of accumulated error messages
        - status: Final pipeline status
    
    Raises:
        No exceptions; all errors are accumulated in returned state.errors
    """
    logger.info("Starting query pipeline execution")
    
    # Initialize pipeline state
    initial_state = QueryState(
        original_query=query,
        config=config,
        status="initialized",
    )
    
    # Execute the pipeline
    try:
        result_dict = graph.invoke(initial_state)
        
        # Reconstruct QueryState from result dict
        # graph.invoke() returns a dict with updated fields
        final_state = QueryState(
            original_query=result_dict.get("original_query", initial_state.original_query),
            query_validated=result_dict.get("query_validated", False),
            query_embedding=result_dict.get("query_embedding"),
            search_results=result_dict.get("search_results", []),
            final_response=result_dict.get("final_response"),
            errors=result_dict.get("errors", []),
            config=result_dict.get("config", config),
            status=result_dict.get("status", "unknown"),
        )
        
        logger.info(f"Query pipeline completed with status: {final_state.status}")
        return final_state
    except Exception as e:
        error_msg = f"Unexpected error during pipeline execution: {str(e)}"
        logger.error(error_msg)
        initial_state.errors.append(error_msg)
        initial_state.status = "pipeline_execution_failed"
        return initial_state
