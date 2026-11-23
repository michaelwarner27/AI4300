"""
LangGraph definition for the vector query pipeline.
Wires together all query nodes with conditional routing logic.
"""

from langgraph.graph import StateGraph
from src.data_models import ChatTurn, QueryState
from src.config import IngestionConfig
from src.query.nodes import (
    response_history_text,
    user_query,
    rewrite_query,
    embed_query,
    vector_store_search,
    add_history_documents,
    generate_response,
    search_more,
    route_after_generate,
    process_tool_call,
)
from src.utils.logging import get_logger

logger = get_logger()


def _should_generate_response(state: QueryState) -> str:
    """
    Conditional routing function: determines if response generation should proceed.
    
    Decision Logic:
    - If search_results is not empty: route to "generate" (generate_response node)
    - If search_results is empty: route to "skip" (END, no LLM call)

    This prevents unnecessary LLM calls when no relevant documents were found.
    On the skip path the run ends with response=None and final_response=None;
    callers show their own "no documents found" message.

    Args:
        state: Current query pipeline state

    Returns:
        "generate" if search results exist and should be synthesized
        "skip" if there are no search results (ends the run)
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
    rewrite_query (follow-up → standalone query; no-op without chat history)
      ↓
    embed_query (generate embedding)
      ↓
    vector_store_search (query Chroma with K=10, cutoff SEARCH_MIN_SIMILARITY)
      ↓
    add_history_documents (carry documents from recent turns)
      ↓
    [Conditional: Results found?]
      ├─ YES → generate_response (LLM turn with tools) ◄──────────────┐
      │          ├─ search_documents / read_section / read_page       │
      │          │    → search_more ──────────────────────────────────┘ (max 3 rounds / 300 s)
      │          └─ final answer → process_tool_call
      └─ NO → END (no LLM call, response=None)
      ↓
    END

    generate_response ⇄ search_more is a hand-built ReAct-style loop: the LLM
    reasons, calls a tool, observes the result, and repeats until it answers.

    Error Handling:
    - Any node can fail and accumulate errors in state.errors
    - Pipeline continues through remaining nodes
    - Final state contains all errors and available results
    
    Timeouts:
    - embed_query: 30 seconds (via EmbeddingService)
    - vector_store_search: 30 seconds (via Chroma)
    - generate_response: 120 seconds per LLM call (via LLMService); the agent
      loop as a whole stops searching after AGENT_DEADLINE_SECONDS
    
    Args:
        config: IngestionConfig object with pipeline configuration
    
    Returns:
        Compiled StateGraph ready for execution
    """
    logger.info("Creating query pipeline graph")
    
    # Create state graph with QueryState as the state schema
    graph = StateGraph(QueryState)
    
    # Add all 8 nodes to the graph (the "skip" route goes straight to END)
    graph.add_node("user_query", user_query)
    graph.add_node("rewrite_query", rewrite_query)
    graph.add_node("embed_query", embed_query)
    graph.add_node("vector_store_search", vector_store_search)
    graph.add_node("add_history_documents", add_history_documents)
    graph.add_node("generate_response", generate_response)
    graph.add_node("search_more", search_more)
    graph.add_node("process_tool_call", process_tool_call)
    
    # Set entry point (first node)
    graph.set_entry_point("user_query")
    
    # Connect nodes in sequence with conditional routing
    graph.add_edge("user_query", "rewrite_query")
    graph.add_edge("rewrite_query", "embed_query")
    graph.add_edge("embed_query", "vector_store_search")
    graph.add_edge("vector_store_search", "add_history_documents")

    # Conditional routing once this turn's (and carried-over) documents are known
    graph.add_conditional_edges(
        "add_history_documents",
        _should_generate_response,
        {
            "generate": "generate_response",
            "skip": "__end__",
        }
    )
    
    # Agent loop: a search_documents call runs search_more and returns to the LLM;
    # any other outcome (final answer, or no tool call) goes to the formatter.
    graph.add_conditional_edges(
        "generate_response",
        route_after_generate,
        {
            "search": "search_more",
            "finish": "process_tool_call",
        }
    )
    graph.add_edge("search_more", "generate_response")
    
    # Connect tool processor to end
    graph.add_edge("process_tool_call", "__end__")
    
    logger.info("Graph structure created with 8 nodes and conditional routing")
    
    # Compile and return the graph
    compiled_graph = graph.compile()
    logger.info("Graph compiled successfully")
    
    return compiled_graph


def execute_query_pipeline(
    query: str,
    config: IngestionConfig,
    graph: StateGraph,
    chat_history: list[ChatTurn] | None = None,
) -> QueryState:
    """
    Execute the query pipeline with the given query and configuration.

    Initializes the pipeline state with the user query and configuration,
    then invokes the compiled graph to process the query through all nodes.

    Args:
        query: User's query string
        config: IngestionConfig with embedding/LLM/Chroma settings
        graph: Compiled StateGraph from create_query_graph()
        chat_history: Earlier turns of the conversation, oldest first. Build the
            next turn from the returned state with chat_turn_from_result(); it
            keeps clean answer text and only the documents the answer cited, and
            returns None for failed turns (they are not remembered).
    
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
        chat_history=list(chat_history or []),
    )
    
    # Execute the pipeline
    try:
        result_dict = graph.invoke(initial_state, config={"recursion_limit": 40})
        
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
            messages=result_dict.get("messages", []),
            search_round=result_dict.get("search_round", 0),
            started_at=result_dict.get("started_at"),
            chat_history=result_dict.get("chat_history", initial_state.chat_history),
            retrieval_query=result_dict.get("retrieval_query"),
            source_documents=result_dict.get("source_documents", []),
            response=result_dict.get("response"),
        )
        
        logger.info(f"Query pipeline completed with status: {final_state.status}")
        return final_state
    except Exception as e:
        error_msg = f"Unexpected error during pipeline execution: {str(e)}"
        logger.error(error_msg)
        initial_state.errors.append(error_msg)
        initial_state.status = "pipeline_execution_failed"
        return initial_state


def chat_turn_from_result(question: str, result: QueryState) -> ChatTurn | None:
    """
    The conversation-history entry for a finished turn, or None if it should not
    be remembered (no response, e.g. no documents found, or a failed turn).

    The answer is stored as clean text (answer + steps, no sources or paths) and
    only the documents the answer cited carry into later turns.
    """
    response = result.response
    if not response or response.get("kind") not in ("answer", "insufficient", "clarify"):
        return None
    return ChatTurn(
        question=question,
        answer=response_history_text(response),
        kind=response["kind"],
        documents=list(result.source_documents),
    )
