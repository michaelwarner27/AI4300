"""
LangGraph node implementations for the vector query pipeline.
Processes user queries to retrieve and generate responses from the vector database.
"""

from pathlib import Path
from typing import Any
import json
import os
import time
from src.data_models import QueryState, QueryResult, ChunkMetadata, ToolCall
from src.query.tools import EmbeddingService, ChromaVectorStore, LLMService
from src.utils.logging import get_logger

logger = get_logger()

DEFAULT_K = 10
DEFAULT_MIN_SIMILARITY_SCORE = 0.6
DEFAULT_MAX_TOKENS = 50000#2048
DEFAULT_LLM_TEMPERATURE = 0.7
MAX_TOOL_CALL_RETRIES = 1
MAX_SEARCH_ROUNDS = 3
AGENT_DEADLINE_SECONDS = 300
SEARCH_DEFAULT_K = 5
SEARCH_MAX_K = 10
NEIGHBOR_RADIUS = 1  # chunks fetched on each side of a text hit
NEIGHBOR_TOP_HITS = 5  # only the best-scoring hits get neighbors
READ_PAGE_MAX_PAGES = 3
DEFAULT_SEARCH_MIN_SIMILARITY = 0.0  # search_documents has no cutoff unless SEARCH_MIN_SIMILARITY is set
AGENT_LOOP_TOOLS = {"search_documents", "read_page"}  # non-terminal tools handled by search_more
DIAGRAM_CHUNK_TYPE = "diagram_reference"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DIAGRAM_DIR_NAME = "extracted_diagrams"

SYSTEM_PROMPT = """You are a helpful assistant that uses the tools given to you to answer questions based on provided documents. Only respond using a tool call!

You have four tools available:
1. respond_to_user: Use this when you can provide a well-supported answer based on the documents (final answer)
2. insufficient_evidence: Use this when the documents do not adequately address the query (final answer)
3. search_documents: Search the document database for more context. The results come back to you and you may then call another tool.
4. read_page: Read every passage on a page or short range of pages of a document, in reading order. Use it when you found a heading or the start of a section but not its content.

INSTRUCTIONS:
- Each turn you must make exactly one tool call. Finish with respond_to_user or insufficient_evidence.
- Use insufficient_evidence if the documents clearly do not address the query
- Each retrieved document has a numeric "ref", a filename, a page and its full text. Documents are listed in reading order; ones marked "neighbor": true were not matched directly but sit next to a match.
- "page" is the PDF page number, which can differ from the page number printed in the document (e.g. PDF page 16 may be printed as page 13). Always use the "page" value for citations and for read_page / search filters.
- Page text is extracted from the PDF and can be out of reading order, especially with two columns: a section heading may appear at the end of a page while its content appears earlier, or on the next page.
- When the question asks how to do something (install, set up, configure, troubleshoot, operate), or is otherwise procedural:
  * Give step-by-step instructions using the "steps" field: one action per step, in the correct order, each citing its source_filename and page
  * Put a short introduction or important warnings in "answer"; do not repeat the steps there
  * Do not skip or invent steps; include prerequisites, warnings and tools mentioned in the documents
- If you do not have enough context to answer the question sufficiently, do NOT answer with partial information and do NOT give up with insufficient_evidence yet. First make a new database query with search_documents (different wording or filters) or call read_page for the relevant pages, then answer from the new context.
- Signs the documents are incomplete: missing steps, "see section X", missing prerequisites, warnings or specifications, a procedure that starts or stops abruptly, or a heading without its content. In these cases call search_documents or read_page first:
  * Use a short, targeted query for what is missing
  * Optionally restrict it with filename and page_start/page_end, e.g. the pages around a partial procedure
  * If you found a heading (e.g. "MAINTENANCE") or the beginning of a procedure but not the content that belongs to it, call read_page for that page and the next one instead of searching again
  * Do not repeat a search or page read you already made
  * You have a limited number of searches and page reads in total; when told to stop, answer with what you have
- When using respond_to_user:
  * Synthesize a clear, concise answer based on the retrieved documents
  * Include relevant sources (filename, page, snippet) in your sources list
  * Set include_sources=true to show sources, or false to omit them
  * Documents with chunk_type "diagram_reference" are figures/diagrams. When the question asks to see, find or show a diagram or figure, put the "ref" of each relevant diagram document in diagram_refs. Only use ref values that appear in the documents; never invent refs or file paths.
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
                "include_sources": {"type": "boolean", "default": True, "description": "Include sources in output"},
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "instruction": {"type": "string", "description": "One action to perform"},
                            "source_filename": {"type": "string"},
                            "page": {"type": "integer"}
                        },
                        "required": ["instruction"]
                    },
                    "description": "Ordered step-by-step instructions for procedural questions"
                },
                "diagram_refs": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "Optional. 'ref' values of retrieved documents with chunk_type 'diagram_reference' whose images should be shown to the user"
                }
            },
            "required": ["answer"]
        }
    },
    {
        "name": "search_documents",
        "description": "Search the document database for additional context (e.g. missing steps, prerequisites, warnings). Returns new matching passages; then continue with another tool call.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for, phrased as a short search query"},
                "filename": {"type": "string", "description": "Optional: only search within this source filename"},
                "page_start": {"type": "integer", "description": "Optional: first page to include"},
                "page_end": {"type": "integer", "description": "Optional: last page to include"},
                "k": {"type": "integer", "description": f"Optional: number of results (default {SEARCH_DEFAULT_K}, max {SEARCH_MAX_K})"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "read_page",
        "description": "Read all passages on a page or range of pages of a document in reading order. Use to get the content around a heading or partial procedure that search did not return.",
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Source filename as shown in the documents"},
                "page_start": {"type": "integer", "description": "First PDF page to read (the 'page' value shown in documents)"},
                "page_end": {"type": "integer", "description": f"Optional: last page to read (default: same as page_start, max {READ_PAGE_MAX_PAGES} pages per call)"}
            },
            "required": ["filename", "page_start"]
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
                query_result = _build_query_result(result)
                search_results.append(query_result)
                
            except (KeyError, TypeError) as e:
                error_msg = (
                    f"Failed to parse search result {result.get('id', 'unknown')}: {str(e)}"
                )
                logger.error(error_msg)
                errors.append(error_msg)
                continue
        
        if search_results:
            search_results = _add_neighbors(vector_store, search_results)
        
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


def _build_query_result(result: dict) -> QueryResult:
    """Convert a raw ChromaVectorStore result into a QueryResult."""
    meta = result["metadata"]
    metadata = ChunkMetadata(
        source_filename=meta["source_filename"],
        page_number=meta["page_number"],
        chunk_sequence=meta["chunk_sequence"],
        start_char_index=meta["start_char_index"],
        end_char_index=meta["end_char_index"],
        # Optional keys: absent on records ingested before diagram support
        chunk_type=meta.get("chunk_type", "text"),
        diagram_path=meta.get("diagram_path", ""),
        diagram_kind=meta.get("diagram_kind", ""),
    )
    return QueryResult(
        content=result["content"],
        metadata=metadata,
        similarity_score=result["similarity_score"],
    )


def _chunk_key(result: QueryResult) -> tuple[str, int]:
    """Identity of a chunk, used to avoid showing the same passage twice."""
    return (result.metadata.source_filename, result.metadata.chunk_sequence)


def _format_result_for_llm(ref: int, result: QueryResult) -> dict:
    """Describe one retrieved chunk (full text) for the LLM, keyed by a stable ref."""
    part = {
        "ref": ref,
        "filename": result.metadata.source_filename,
        "page": result.metadata.page_number,
        "chunk_type": result.metadata.chunk_type,
        "text": result.content,
    }
    if result.is_neighbor:
        part["neighbor"] = True
    if result.metadata.chunk_type == DIAGRAM_CHUNK_TYPE:
        part["diagram_kind"] = result.metadata.diagram_kind
        part["diagram_path"] = result.metadata.diagram_path
    return part


def _build_where(filename: Any, page_start: Any, page_end: Any) -> dict | None:
    """Build a Chroma metadata filter from the search tool's optional filters."""
    conditions: list[dict] = []
    if isinstance(filename, str) and filename.strip():
        conditions.append({"source_filename": {"$eq": filename.strip()}})
    if isinstance(page_start, int) and not isinstance(page_start, bool):
        conditions.append({"page_number": {"$gte": page_start}})
    if isinstance(page_end, int) and not isinstance(page_end, bool):
        conditions.append({"page_number": {"$lte": page_end}})
    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


def _reading_order_key(result: QueryResult) -> tuple[str, int, int]:
    return (result.metadata.source_filename, result.metadata.page_number, result.metadata.chunk_sequence)


def _add_neighbors(vector_store: Any, results: list[QueryResult]) -> list[QueryResult]:
    """
    Add the chunks next to each of the top NEIGHBOR_TOP_HITS text hits
    (±NEIGHBOR_RADIUS in chunk_sequence). Results arrive best-score first.
    
    Headings often end up in a different chunk than the text they introduce, so
    a hit alone can be misleading. Neighbors are marked is_neighbor and the
    combined list is returned in reading order. Failures are logged and the
    original results are returned unchanged.
    """
    if NEIGHBOR_RADIUS <= 0 or NEIGHBOR_TOP_HITS <= 0:
        return results
    have = {_chunk_key(r) for r in results}
    wanted: dict[str, set[int]] = {}
    for r in results[:NEIGHBOR_TOP_HITS]:
        if r.metadata.chunk_type != "text":
            continue
        for offset in range(-NEIGHBOR_RADIUS, NEIGHBOR_RADIUS + 1):
            seq = r.metadata.chunk_sequence + offset
            if offset and seq >= 0 and (r.metadata.source_filename, seq) not in have:
                wanted.setdefault(r.metadata.source_filename, set()).add(seq)
    
    added: list[QueryResult] = []
    try:
        for filename, seqs in wanted.items():
            where = {"$and": [
                {"source_filename": {"$eq": filename}},
                {"chunk_sequence": {"$in": sorted(seqs)}},
            ]}
            for raw in vector_store.get_chunks(where):
                try:
                    candidate = _build_query_result(raw)
                except (KeyError, TypeError):
                    continue
                if candidate.metadata.chunk_type != "text" or _chunk_key(candidate) in have:
                    continue
                candidate.is_neighbor = True
                have.add(_chunk_key(candidate))
                added.append(candidate)
    except Exception as e:
        logger.warning(f"Neighbor expansion failed, continuing with direct hits only: {str(e)}")
        return results
    
    logger.info(f"vector_store_search: added {len(added)} neighbor chunk(s) as context")
    return sorted(results + added, key=_reading_order_key)


def _search_min_similarity() -> float:
    """Similarity cutoff for search_documents (SEARCH_MIN_SIMILARITY in .env, default none)."""
    raw = os.getenv("SEARCH_MIN_SIMILARITY", "").strip()
    if not raw:
        return DEFAULT_SEARCH_MIN_SIMILARITY
    try:
        return float(raw)
    except ValueError:
        logger.warning(f"Invalid SEARCH_MIN_SIMILARITY={raw!r}; using {DEFAULT_SEARCH_MIN_SIMILARITY}")
        return DEFAULT_SEARCH_MIN_SIMILARITY


def _resolve_filename(name: Any, existing: list[QueryResult]) -> str | None:
    """
    Map the LLM's filename to a stored one. Accepts an exact match, a unique
    case-insensitive substring of a filename already in context, or no name when
    only one document is in context. Returns None when no filter was given.
    
    Raises:
        ValueError: if a name was given but cannot be matched
    """
    known = sorted({r.metadata.source_filename for r in existing})
    if not isinstance(name, str) or not name.strip():
        return known[0] if len(known) == 1 else None
    name = name.strip()
    if name in known:
        return name
    matches = [k for k in known if name.lower() in k.lower() or k.lower() in name.lower()]
    if len(matches) == 1:
        return matches[0]
    raise ValueError(f"Unknown filename {name!r}. Known filenames: {', '.join(known) or 'none'}")


def generate_response(state: QueryState) -> dict[str, Any]:
    """
    Run one agent turn: call the LLM with tools.
    
    The first turn builds the conversation from the question and retrieved
    documents; later turns (after search_more) continue from state.messages.
    Returns the tool call, which the graph routes to search_more (non-terminal)
    or process_tool_call (final answer).
    """
    logger.info("Entering generate_response node")
    
    errors: list[str] = list(state.errors)
    tool_call = None
    
    if state.original_query is None or not state.search_results or not state.config:
        error_msg = "Missing required state: query, results, or config"
        logger.error(error_msg)
        errors.append(error_msg)
        return {"tool_call": None, "errors": errors, "status": "generate_response_failed"}
    
    now = time.time()
    started_at = state.started_at or now
    messages = list(state.messages)
    
    if not messages:
        context_parts = [
            _format_result_for_llm(ref, result)
            for ref, result in enumerate(state.search_results)
        ]
        user_message = f"""Answer the following question based only on the provided documents:

Question: {state.original_query}

Retrieved Documents:
{json.dumps(context_parts, indent=2)}
"""
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]
    
    # Searching is limited by round count and by wall-clock time
    search_allowed = (
        state.search_round < MAX_SEARCH_ROUNDS
        and (now - started_at) < AGENT_DEADLINE_SECONDS
    )
    if search_allowed:
        tools = TOOLS
    else:
        tools = [t for t in TOOLS if t["name"] not in AGENT_LOOP_TOOLS]
        if messages[-1].get("role") == "tool":
            messages[-1] = {
                **messages[-1],
                "content": messages[-1]["content"]
                + "\n\n[No further searches or page reads are available. Answer now with respond_to_user or insufficient_evidence.]",
            }
    
    llm_service = LLMService(
        base_url=state.config.llm_base_url,
        model=state.config.llm_model,
        api_key=state.config.llm_api_key,
        max_tokens=DEFAULT_MAX_TOKENS,
    )
    
    assistant_message = None
    # Retry loop for tool calling
    for attempt in range(MAX_TOOL_CALL_RETRIES):
        try:
            result = llm_service.chat_with_tools(
                messages=messages,
                tools=tools,
                temperature=DEFAULT_LLM_TEMPERATURE,
            )
            tool_call = ToolCall(
                tool_name=result["tool_name"],
                parameters=result["parameters"],
                tool_call_id=result.get("tool_call_id"),
            )
            assistant_message = result.get("assistant_message")
            logger.info(f"generate_response: Tool call '{tool_call.tool_name}' succeeded on attempt {attempt + 1}")
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
            "started_at": started_at,
            "status": "generate_response_failed",
        }
    
    if assistant_message is not None:
        messages.append(assistant_message)
    
    return {
        "tool_call": tool_call,
        "messages": messages,
        "errors": errors,
        "started_at": started_at,
        "status": "tool_call_received",
    }


def route_after_generate(state: QueryState) -> str:
    """Graph routing: 'search' for a search_documents / read_page call, else 'finish'."""
    if state.tool_call and state.tool_call.tool_name in AGENT_LOOP_TOOLS:
        return "search"
    return "finish"


def _run_search(config: Any, params: dict, existing: list[QueryResult]) -> tuple[list[QueryResult], str]:
    """
    Execute a search_documents call.
    
    Returns (new_results, tool_message_text). Chunks already in `existing` are
    skipped; new chunks get refs continuing after the existing ones.
    
    Raises:
        ValueError, ConnectionError, RuntimeError on bad input or service errors
    """
    query_text = params.get("query")
    if not isinstance(query_text, str) or not query_text.strip():
        raise ValueError("search_documents requires a non-empty 'query'")
    
    k = params.get("k", SEARCH_DEFAULT_K)
    if isinstance(k, bool) or not isinstance(k, int):
        k = SEARCH_DEFAULT_K
    k = max(1, min(k, SEARCH_MAX_K))
    
    filename = _resolve_filename(params.get("filename"), existing) if params.get("filename") else None
    where = _build_where(filename, params.get("page_start"), params.get("page_end"))
    
    embedding_service = EmbeddingService(
        base_url=config.embedding_base_url,
        model=config.embedding_model,
        api_key=config.embedding_api_key,
    )
    embedding = embedding_service.embed_text(query_text.strip())
    if not embedding:
        raise ValueError("Embedding service returned an empty vector")
    
    vector_store = ChromaVectorStore(
        persist_directory=config.chroma_persist_directory,
        collection_name=config.chroma_collection_name,
    )
    vector_store.initialize_collection()
    raw_results = vector_store.query_similarity(
        query_embedding=embedding,
        k=k,
        min_similarity_score=_search_min_similarity(),
        where=where,
    )
    
    seen = {_chunk_key(r) for r in existing}
    new_results: list[QueryResult] = []
    for raw in raw_results:
        try:
            result = _build_query_result(raw)
        except (KeyError, TypeError) as e:
            logger.error(f"Failed to parse search result {raw.get('id', 'unknown')}: {str(e)}")
            continue
        key = _chunk_key(result)
        if key in seen:
            continue
        seen.add(key)
        new_results.append(result)
    
    if not new_results:
        return [], "No new relevant passages found for this search. Try different wording or filters, or answer with what you have."
    
    first_ref = len(existing)
    parts = [_format_result_for_llm(first_ref + i, r) for i, r in enumerate(new_results)]
    text = f"Found {len(new_results)} new passage(s):\n{json.dumps(parts, indent=2)}"
    return new_results, text


def _run_read_page(config: Any, params: dict, existing: list[QueryResult]) -> tuple[list[QueryResult], str]:
    """
    Execute a read_page call: every chunk on a page range, in reading order.
    
    Returns (new_results, tool_message_text). Chunks already in context are not
    repeated; the message lists their refs so the LLM can place them. New chunks
    get refs continuing after the existing ones.
    
    Raises:
        ValueError, ConnectionError, RuntimeError on bad input or service errors
    """
    page_start = params.get("page_start")
    if isinstance(page_start, bool) or not isinstance(page_start, int):
        raise ValueError("read_page requires an integer 'page_start'")
    page_end = params.get("page_end", page_start)
    if isinstance(page_end, bool) or not isinstance(page_end, int):
        page_end = page_start
    page_end = max(page_start, min(page_end, page_start + READ_PAGE_MAX_PAGES - 1))
    
    filename = _resolve_filename(params.get("filename"), existing)
    if not filename:
        raise ValueError("read_page requires a 'filename'")
    
    vector_store = ChromaVectorStore(
        persist_directory=config.chroma_persist_directory,
        collection_name=config.chroma_collection_name,
    )
    vector_store.initialize_collection()
    raw_chunks = vector_store.get_chunks({"$and": [
        {"source_filename": {"$eq": filename}},
        {"page_number": {"$gte": page_start}},
        {"page_number": {"$lte": page_end}},
    ]})
    
    refs_by_key = {_chunk_key(r): ref for ref, r in enumerate(existing)}
    new_results: list[QueryResult] = []
    already: list[int] = []
    for raw in raw_chunks:
        try:
            result = _build_query_result(raw)
        except (KeyError, TypeError) as e:
            logger.error(f"Failed to parse chunk {raw.get('id', 'unknown')}: {str(e)}")
            continue
        key = _chunk_key(result)
        if key in refs_by_key:
            already.append(refs_by_key[key])
            continue
        result.is_neighbor = True  # read on request, not matched by a query
        refs_by_key[key] = len(existing) + len(new_results)
        new_results.append(result)
    
    pages = f"page {page_start}" if page_start == page_end else f"pages {page_start}-{page_end}"
    if not raw_chunks:
        return [], f"No passages found for {filename}, {pages}. Check the page number (use the 'page' values shown in the documents)."
    
    lines = [f"{filename}, {pages}: {len(raw_chunks)} passage(s) in reading order."]
    if already:
        lines.append(f"Already in your context (not repeated): refs {sorted(already)}.")
    if new_results:
        first_ref = len(existing)
        parts = [_format_result_for_llm(first_ref + i, r) for i, r in enumerate(new_results)]
        lines.append(f"New passage(s):\n{json.dumps(parts, indent=2)}")
    else:
        lines.append("Nothing new: every passage on these pages is already in your context.")
    return new_results, "\n".join(lines)


def search_more(state: QueryState) -> dict[str, Any]:
    """
    Execute the LLM's search_documents call and feed the results back.
    
    Appends new, de-duplicated chunks to search_results (existing refs never
    shift), adds the tool result to the conversation and loops back to
    generate_response. Search failures are reported to the LLM, not raised.
    """
    logger.info("Entering search_more node")
    
    tool_call = state.tool_call
    messages = list(state.messages)
    search_results = list(state.search_results)
    
    if not tool_call or not state.config:
        error_msg = "search_more called without a tool call or config"
        logger.error(error_msg)
        return {
            "tool_call": None,
            "errors": list(state.errors) + [error_msg],
            "status": "search_more_failed",
        }
    
    try:
        if tool_call.tool_name == "read_page":
            new_results, content = _run_read_page(state.config, tool_call.parameters, search_results)
        else:
            new_results, content = _run_search(state.config, tool_call.parameters, search_results)
        search_results.extend(new_results)
        logger.info(
            f"search_more: round {state.search_round + 1}, {tool_call.tool_name}, "
            f"{len(new_results)} new result(s), params={tool_call.parameters}"
        )
    except Exception as e:
        logger.error(f"search_more: search failed: {str(e)}")
        content = f"{tool_call.tool_name} failed: {str(e)}. Answer with the documents you already have, or try something different."
    
    messages.append({
        "role": "tool",
        "tool_call_id": tool_call.tool_call_id or "call_0",
        "content": content,
    })
    
    return {
        "search_results": search_results,
        "messages": messages,
        "search_round": state.search_round + 1,
        "tool_call": None,
        "status": "search_more_completed",
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
    diagram_lines = _resolve_diagrams(params.get("diagram_refs"), search_results)
    step_lines = _format_steps(params.get("steps"))

    parts = [answer]
    if step_lines:
        parts.append("\nSteps:")
        parts.extend(step_lines)
    
    if include_sources:
        # Build response with sources
        parts.append("\n---\n\nSources:")

        # Add sources from tool call
        if sources:
            for src in sources:
                filename = src.get("filename", "unknown")
                page = src.get("page", "?")
                snippet = src.get("snippet", "")
                parts.append(f"- {filename} (page {page}): {snippet}")

    if diagram_lines:
        parts.append("\nDiagrams:")
        parts.extend(diagram_lines)

    return "\n".join(parts)


def _format_steps(steps: Any) -> list[str]:
    """Render the LLM's steps as a numbered list, skipping malformed entries."""
    if not isinstance(steps, list):
        return []
    lines: list[str] = []
    for step in steps:
        if isinstance(step, str):
            instruction, citation = step.strip(), ""
        elif isinstance(step, dict) and isinstance(step.get("instruction"), str):
            instruction = step["instruction"].strip()
            filename = step.get("source_filename")
            page = step.get("page")
            if isinstance(filename, str) and filename:
                citation = f" ({filename}"
                citation += f", p.{page})" if isinstance(page, int) and not isinstance(page, bool) else ")"
            elif isinstance(page, int) and not isinstance(page, bool):
                citation = f" (p.{page})"
            else:
                citation = ""
        else:
            continue
        if instruction:
            lines.append(f"{len(lines) + 1}. {instruction}{citation}")
    return lines


def _resolve_diagram_file(diagram_path: str) -> Path | None:
    """Locate a stored diagram image, independent of the process working directory."""
    if not diagram_path:
        return None
    stored = Path(diagram_path)
    candidates = [stored] if stored.is_absolute() else [PROJECT_ROOT / stored]
    candidates.append(PROJECT_ROOT / DIAGRAM_DIR_NAME / stored.name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _resolve_diagrams(refs: Any, search_results: list[QueryResult]) -> list[str]:
    """Turn LLM-provided diagram refs into output lines, dropping invalid ones."""
    if not isinstance(refs, list):
        return []
    lines: list[str] = []
    seen: set[int] = set()
    for ref in refs:
        if isinstance(ref, bool) or not isinstance(ref, int) or ref in seen:
            continue
        seen.add(ref)
        if not 0 <= ref < len(search_results):
            logger.warning(f"Ignoring out-of-range diagram ref {ref}")
            continue
        meta = search_results[ref].metadata
        if meta.chunk_type != DIAGRAM_CHUNK_TYPE:
            logger.warning(f"Ignoring diagram ref {ref}: not a diagram chunk")
            continue
        path = _resolve_diagram_file(meta.diagram_path)
        if path is None:
            logger.warning(f"Ignoring diagram ref {ref}: file not found ({meta.diagram_path})")
            continue
        kind = f", {meta.diagram_kind}" if meta.diagram_kind else ""
        lines.append(f"- {meta.source_filename} (page {meta.page_number}{kind}): {path}")
    return lines


def _handle_insufficient_evidence(params: dict, search_results: list[QueryResult]) -> str:
    """Handle insufficient_evidence tool call."""
    explanation = params.get("explanation", "No explanation provided")
    
    parts = [
        "⚠️  Insufficient Information",
        "",
        explanation,
        "",
    ]

    # Show only the single most relevant document: the highest-scoring direct
    # hit (neighbor / page-read chunks are context and carry no real score).
    if search_results:
        direct_hits = [r for r in search_results if not r.is_neighbor]
        best = max(direct_hits or search_results, key=lambda r: r.similarity_score)
        parts.extend([
            "However, here is the most relevant document that was retrieved:",
            "",
            f"  Source: {best.metadata.source_filename} (page {best.metadata.page_number})",
            f"  Snippet: {best.content[:150]}",
            "",
        ])

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
