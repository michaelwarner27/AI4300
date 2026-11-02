"""
LangGraph node implementations for the vector query pipeline.
Processes user queries to retrieve and generate responses from the vector database.
"""

from typing import Any
import json
from src.data_models import QueryState, QueryResult, ChunkMetadata, ToolCall
from src.query.tools import EmbeddingService, ChromaVectorStore, LLMService
from src.utils.logging import get_logger

logger = get_logger()

DEFAULT_K = 10
DEFAULT_MIN_SIMILARITY_SCORE = 0.6
DEFAULT_MAX_TOKENS = 50000#2048
DEFAULT_LLM_TEMPERATURE = 0.7
MAX_TOOL_CALL_RETRIES = 1

SYSTEM_PROMPT = """You are a helpful assistant that uses the tools give to you to answer questions based on provided documents. Only respond using a tool call!

You have two tools available:
1. respond_to_user: Use this when you can provide a well-supported answer based on the documents
2. insufficient_evidence: Use this when the documents do not adequately address the query

INSTRUCTIONS:
- Your response should include exclusively a single tool call.
- Use insufficient_evidence if the documents clearly do not address the query
- When using respond_to_user:
  * Synthesize a clear, concise answer based on the retrieved documents
  * Include relevant sources (filename, page, snippet) in your sources list
  * Set include_sources=true to show sources, or false to omit them
- When using insufficient_evidence:
  * Explain clearly why the documents don't provide sufficient evidence
  * Reference the original query

Answer ONLY based on the information in the provided documents. Do not hallucinate or infer beyond what the documents contain.
"""

# Tool definitions for LLM
TOOLS = [
    {
        "name": "respond_to_user",
        "description": "Return a well-supported answer to the user's query based on retrieved documents",
        "parameters": {
            "type": "object",
            "properties": {
                "answer": {"type": "string", "description": "The synthesized answer to the user's query"},
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "filename": {"type": "string"},
                            "page": {"type": "integer"},
                            "snippet": {"type": "string"}
                        }
                    },
                    "description": "Sources used in answer"
                },
                "include_sources": {"type": "boolean", "default": True, "description": "Include sources in output"}
            },
            "required": ["answer"]
        }
    },
    {
        "name": "insufficient_evidence",
        "description": "Indicate that documents do not provide sufficient evidence",
        "parameters": {
            "type": "object",
            "properties": {
                "explanation": {"type": "string", "description": "Why documents are insufficient"},
                "query": {"type": "string", "description": "Original user query"}
            },
            "required": ["explanation"]
        }
    }
]


def user_query(state: QueryState) -> dict[str, Any]:
    """
    Validate and prepare user query input.
    
    This is the first node in the query pipeline. It accepts a user query string,
    validates that it's not empty or malformed, and prepares it for embedding.
    The query is passed as-is without preprocessing.
    
    Args:
        state: Current query pipeline state
    
    Returns:
        Dictionary with updated state fields:
        - original_query: The validated query string
        - query_validated: Boolean indicating successful validation
        - errors: Accumulated error messages (if any)
        - status: Current pipeline status
    
    Raises:
        No exceptions; errors are accumulated in state.errors
    """
    logger.info("Entering user_query node")
    
    errors: list[str] = list(state.errors)
    
    # Validate that a query was provided
    if state.original_query is None:
        error_msg = "No query provided: original_query is None"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "original_query": None,
            "query_validated": False,
            "errors": errors,
            "status": "query_validation_failed",
        }
    
    # Validate that query is a string
    if not isinstance(state.original_query, str):
        error_msg = f"Invalid query type: expected string, got {type(state.original_query).__name__}"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "original_query": None,
            "query_validated": False,
            "errors": errors,
            "status": "query_validation_failed",
        }
    
    # Validate that query is not empty
    query_stripped = state.original_query.strip()
    if not query_stripped:
        error_msg = "Query cannot be empty or whitespace-only"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "original_query": None,
            "query_validated": False,
            "errors": errors,
            "status": "query_validation_failed",
        }
    
    # Query validation successful
    logger.info(f"user_query: Successfully validated query (length: {len(query_stripped)} characters)")
    
    return {
        "original_query": query_stripped,
        "query_validated": True,
        "errors": errors,
        "status": "query_validated",
    }


def embed_query(state: QueryState) -> dict[str, Any]:
    """
    Generate vector embedding for the user query.
    
    This node takes the validated query string and generates a vector embedding
    using the configured embedding service (as specified in .env). The embedding
    is passed to the next node for vector store search.
    
    Args:
        state: Current query pipeline state
    
    Returns:
        Dictionary with updated state fields:
        - query_embedding: Vector embedding as list of floats
        - errors: Accumulated error messages (if any)
        - status: Current pipeline status
    
    Raises:
        No exceptions; errors are accumulated in state.errors
    """
    logger.info("Entering embed_query node")
    
    errors: list[str] = list(state.errors)
    
    # Check if query was validated
    if not state.query_validated or state.original_query is None:
        error_msg = "Query not validated or empty in embed_query node"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "query_embedding": None,
            "errors": errors,
            "status": "embed_query_failed",
        }
    
    # Check if configuration is available
    if not state.config:
        error_msg = "Configuration not available in pipeline state"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "query_embedding": None,
            "errors": errors,
            "status": "embed_query_failed",
        }
    
    try:
        # Initialize embedding service with config
        embedding_service = EmbeddingService(
            base_url=state.config.embedding_base_url,
            model=state.config.embedding_model,
            api_key=state.config.embedding_api_key,
        )
        
        # Generate embedding for query
        query_embedding = embedding_service.embed_text(state.original_query)
        
        if not query_embedding:
            error_msg = "Embedding service returned empty embedding vector"
            logger.error(error_msg)
            errors.append(error_msg)
            return {
                "query_embedding": None,
                "errors": errors,
                "status": "embed_query_failed",
            }
        
        logger.info(f"embed_query: Successfully embedded query (vector dimension: {len(query_embedding)})")
        
        return {
            "query_embedding": query_embedding,
            "errors": errors,
            "status": "query_embedded",
        }
        
    except ConnectionError as e:
        error_msg = f"Failed to connect to embedding service: {str(e)}"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "query_embedding": None,
            "errors": errors,
            "status": "embed_query_failed",
        }
    except ValueError as e:
        error_msg = f"Embedding service error: {str(e)}"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "query_embedding": None,
            "errors": errors,
            "status": "embed_query_failed",
        }
    except Exception as e:
        error_msg = f"Unexpected error embedding query: {str(e)}"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "query_embedding": None,
            "errors": errors,
            "status": "embed_query_failed",
        }


def vector_store_search(state: QueryState) -> dict[str, Any]:
    """
    Search the Chroma vector database for semantically similar documents.
    
    This node takes the query embedding and performs a similarity search against
    the vector store to find the top K most relevant chunks. Results are filtered
    by a minimum similarity score to prevent irrelevant retrievals. Each result
    includes the chunk content, metadata for locating the source, and the
    similarity score.
    
    Args:
        state: Current query pipeline state
    
    Returns:
        Dictionary with updated state fields:
        - search_results: List of QueryResult objects with top K matching chunks
        - errors: Accumulated error messages (if any)
        - status: Current pipeline status
    
    Raises:
        No exceptions; errors are accumulated in state.errors
    """
    logger.info("Entering vector_store_search node")
    
    errors: list[str] = list(state.errors)
    search_results: list[QueryResult] = []
    
    # Check if query was embedded
    if state.query_embedding is None:
        error_msg = "Query embedding not available in vector_store_search node"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "search_results": search_results,
            "errors": errors,
            "status": "vector_store_search_failed",
        }
    
    # Check if configuration is available
    if not state.config:
        error_msg = "Configuration not available in pipeline state"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "search_results": search_results,
            "errors": errors,
            "status": "vector_store_search_failed",
        }
    
    try:
        # Initialize vector store connection
        vector_store = ChromaVectorStore(
            persist_directory=state.config.chroma_persist_directory,
            collection_name=state.config.chroma_collection_name,
        )
        
        # Initialize the collection (connects to existing database)
        vector_store.initialize_collection()
        
        # Query the vector store with similarity score filtering
        raw_results = vector_store.query_similarity(
            query_embedding=state.query_embedding,
            k=DEFAULT_K,
            min_similarity_score=DEFAULT_MIN_SIMILARITY_SCORE,
        )
        
        # Transform raw results into QueryResult objects
        for result in raw_results:
            try:
                # Reconstruct ChunkMetadata from stored metadata
                metadata = ChunkMetadata(
                    source_filename=result["metadata"]["source_filename"],
                    page_number=result["metadata"]["page_number"],
                    chunk_sequence=result["metadata"]["chunk_sequence"],
                    start_char_index=result["metadata"]["start_char_index"],
                    end_char_index=result["metadata"]["end_char_index"],
                )
                
                # Create QueryResult
                query_result = QueryResult(
                    content=result["content"],
                    metadata=metadata,
                    similarity_score=result["similarity_score"],
                )
                search_results.append(query_result)
                
            except (KeyError, TypeError) as e:
                error_msg = (
                    f"Failed to parse search result {result.get('id', 'unknown')}: {str(e)}"
                )
                logger.error(error_msg)
                errors.append(error_msg)
                continue
        
        if not search_results:
            logger.warning("No results returned from vector store search")
            return {
                "search_results": search_results,
                "errors": errors,
                "status": "no_results_found",
            }
        
        logger.info(f"vector_store_search: Retrieved {len(search_results)} results from vector store")
        
        return {
            "search_results": search_results,
            "errors": errors,
            "status": "search_completed",
        }
        
    except ConnectionError as e:
        error_msg = f"Failed to connect to vector store: {str(e)}"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "search_results": search_results,
            "errors": errors,
            "status": "vector_store_search_failed",
        }
    except RuntimeError as e:
        error_msg = f"Vector store error: {str(e)}"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "search_results": search_results,
            "errors": errors,
            "status": "vector_store_search_failed",
        }
    except Exception as e:
        error_msg = f"Unexpected error searching vector store: {str(e)}"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "search_results": search_results,
            "errors": errors,
            "status": "vector_store_search_failed",
        }


def generate_response(state: QueryState) -> dict[str, Any]:
    """Generate response with tool calling and retry logic."""
    logger.info("Entering generate_response node")
    
    errors: list[str] = list(state.errors)
    tool_call = None
    last_llm_response = None  # Track last response for debugging
    
    if state.original_query is None or not state.search_results or not state.config:
        error_msg = "Missing required state: query, results, or config"
        logger.error(error_msg)
        errors.append(error_msg)
        return {"tool_call": None, "errors": errors, "status": "generate_response_failed"}
    
    # Build context for LLM
    context_parts = []
    for result in state.search_results:
        context_parts.append({
            "filename": result.metadata.source_filename,
            "page": result.metadata.page_number,
            "snippet": result.content[:200],
        })
    
    user_message = f"""Answer the following question based only on the provided documents:

Question: {state.original_query}

Retrieved Documents:
{json.dumps(context_parts, indent=2)}
"""
    
    llm_service = LLMService(
        base_url=state.config.llm_base_url,
        model=state.config.llm_model,
        api_key=state.config.llm_api_key,
        max_tokens=DEFAULT_MAX_TOKENS,
    )
    
    # Retry loop for tool calling
    for attempt in range(MAX_TOOL_CALL_RETRIES):
        try:
            result = llm_service.generate_with_tools(
                system_prompt=SYSTEM_PROMPT,
                user_message=user_message,
                tools=TOOLS,
                temperature=DEFAULT_LLM_TEMPERATURE,
            )
            tool_call = ToolCall(tool_name=result["tool_name"], parameters=result["parameters"])
            logger.info(f"generate_response: Tool call succeeded on attempt {attempt + 1}")
            break
            
        except (ConnectionError, ValueError) as e:
            error_msg = f"Tool call attempt {attempt + 1} failed: {str(e)}"
            logger.error(error_msg)
            if attempt < MAX_TOOL_CALL_RETRIES - 1:
                logger.info(f"Retrying... (attempt {attempt + 1}/{MAX_TOOL_CALL_RETRIES})")
            else:
                logger.error(f"LLM tool calling failed after {MAX_TOOL_CALL_RETRIES} attempts. Last error: {str(e)}")
                errors.append(f"LLM tool calling failed after {MAX_TOOL_CALL_RETRIES} attempts: {str(e)}")
    
    if not tool_call:
        return {
            "tool_call": None,
            "errors": errors,
            "status": "generate_response_failed",
        }
    
    return {
        "tool_call": tool_call,
        "errors": errors,
        "status": "tool_call_received",
    }


def process_tool_call(state: QueryState) -> dict[str, Any]:
    """Process LLM tool call and format final response."""
    logger.info("Entering process_tool_call node")
    
    errors: list[str] = list(state.errors)
    final_response = None
    
    if not state.tool_call:
        error_msg = "No tool call available in process_tool_call node"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "final_response": _format_fallback_response(state.search_results, errors),
            "errors": errors,
            "status": "response_generated_with_errors",
        }
    
    try:
        if state.tool_call.tool_name == "respond_to_user":
            final_response = _handle_respond_to_user(
                state.tool_call.parameters,
                state.search_results
            )
            status = "response_generated"
        elif state.tool_call.tool_name == "insufficient_evidence":
            final_response = _handle_insufficient_evidence(
                state.tool_call.parameters,
                state.search_results
            )
            status = "insufficient_evidence_returned"
        else:
            error_msg = f"Unknown tool: {state.tool_call.tool_name}"
            logger.error(error_msg)
            errors.append(error_msg)
            final_response = _format_fallback_response(state.search_results, errors)
            status = "response_generated_with_errors"
        
        logger.info(f"process_tool_call: Formatted response ({len(final_response)} chars)")
        return {
            "final_response": final_response,
            "errors": errors,
            "status": status,
        }
        
    except Exception as e:
        error_msg = f"Error processing tool call: {str(e)}"
        logger.error(error_msg)
        errors.append(error_msg)
        return {
            "final_response": _format_fallback_response(state.search_results, errors),
            "errors": errors,
            "status": "response_generated_with_errors",
        }


def _handle_respond_to_user(params: dict, search_results: list[QueryResult]) -> str:
    """Handle respond_to_user tool call."""
    answer = params.get("answer", "")
    include_sources = params.get("include_sources", True)
    sources = params.get("sources", [])
    
    if not include_sources:
        return answer
    
    # Build response with sources
    parts = [answer, "\n---\n\nSources:"]
    
    # Add sources from tool call
    if sources:
        for src in sources:
            filename = src.get("filename", "unknown")
            page = src.get("page", "?")
            snippet = src.get("snippet", "")
            parts.append(f"- {filename} (page {page}): {snippet}")
    
    return "\n".join(parts)


def _handle_insufficient_evidence(params: dict, search_results: list[QueryResult]) -> str:
    """Handle insufficient_evidence tool call."""
    explanation = params.get("explanation", "No explanation provided")
    
    parts = [
        "⚠️  Insufficient Information",
        "",
        explanation,
        "",
        "However, here are the retrieved documents that may be relevant:",
        ""
    ]
    
    for i, result in enumerate(search_results, 1):
        parts.append(f"Document {i}:")
        parts.append(f"  Source: {result.metadata.source_filename} (page {result.metadata.page_number})")
        parts.append(f"  Snippet: {result.content[:150]}")
        parts.append("")
    
    return "\n".join(parts)


def _format_fallback_response(search_results: list[QueryResult], errors: list[str]) -> str:
    """Format fallback when tool calling fails."""
    parts = [
        "⚠️  Tool call generation failed",
        "",
        "Errors encountered:",
    ]
    
    # Show all LLM-related errors (usually the last error is most relevant)
    if errors:
        for error in errors:
            if "LLM" in error or "tool call" in error.lower():
                parts.append(f"  - {error}")
        # If no LLM-specific errors found, show the last few errors
        if len(parts) == 3:  # Only header, no errors added
            for error in errors[-3:]:
                parts.append(f"  - {error}")
    else:
        parts.append("  - Unknown error")
    
    # parts.extend(["", "Retrieved documents:"])
    
    # for i, result in enumerate(search_results, 1):
    #     parts.append(f"{i}. {result.metadata.source_filename} (page {result.metadata.page_number})")
    #     parts.append(f"   {result.content[:150]}")
    
    return "\n".join(parts)
