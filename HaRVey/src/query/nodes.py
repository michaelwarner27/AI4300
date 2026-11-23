"""
LangGraph node implementations for the vector query pipeline.
Processes user queries to retrieve and generate responses from the vector database.
"""

from dataclasses import replace
from pathlib import Path
from typing import Any
import json
import os
import re
import time
from src.data_models import ChatTurn, QueryState, QueryResult, ChunkMetadata, ToolCall
from src.query.tools import (
    EmbeddingService, ChromaVectorStore, LLMService, MalformedToolCallError, NoToolCallError,
)
from src.utils.logging import get_logger

logger = get_logger()

DEFAULT_K = 10
DEFAULT_MAX_TOKENS = 50000  # room for reasoning models
DEFAULT_LLM_TEMPERATURE = 0.7
MAX_TOOL_CALL_RETRIES = 2  # a reply without a tool call is retried once with TOOL_NUDGE
RETRY_LLM_TEMPERATURE = 0.9  # retries use more randomness so they don't repeat the failed output
TOOL_NUDGE = (
    "You must reply with exactly one tool call, not plain text. "
    "Use respond_to_user for your answer."
)
MALFORMED_TOOL_NUDGE = (
    "Your previous reply contained a tool call that could not be parsed. Reply again with exactly "
    'one tool call whose arguments are valid JSON. Keep "answer" short (an introduction and '
    'warnings only) and put the instructions in "steps".'
)
MAX_SEARCH_ROUNDS = 3
AGENT_DEADLINE_SECONDS = 300
SEARCH_DEFAULT_K = 5
SEARCH_MAX_K = 10
NEIGHBOR_RADIUS = 1  # chunks fetched on each side of a text hit
NEIGHBOR_TOP_HITS = 5  # only the best-scoring hits get neighbors
PAGE_DIAGRAM_TOP_HITS = 5  # diagrams on the pages of this many best hits are added as context
PAGE_DIAGRAM_MAX = 8  # cap on page diagrams added per search (same-section ones first)
MAX_DIAGRAMS_PER_ANSWER = 4  # images shown with one answer (first valid diagram_refs)
SNIPPET_MAX_CHARS = 200  # source snippets are cut to this length (at a word boundary)
INSUFFICIENT_SNIPPET_CHARS = 150  # snippet of the best document in an insufficient_evidence reply
MAX_CLARIFY_OPTIONS = 4  # answer choices offered with a clarifying question
ERROR_MESSAGE = "Sorry, I couldn't generate an answer. Please try again or rephrase your question."
READ_PAGE_MAX_PAGES = 3
READ_SECTION_MAX_CHUNKS = 25  # passages returned by one read_section call
READ_SECTION_LIST_LIMIT = 60  # section names listed when a read_section request doesn't match
SECTION_SEPARATOR = " > "  # joins a heading path (same as ingestion's section_path)
# Similarity cutoff for the first retrieval AND search_documents (SEARCH_MIN_SIMILARITY in .env).
# Real questions score 0.55+, off-topic ones ~0.4 (see flaws.md).
DEFAULT_MIN_SIMILARITY = 0.5
AGENT_LOOP_TOOLS = {"search_documents", "read_page", "read_section"}  # non-terminal tools handled by search_more
DIAGRAM_CHUNK_TYPE = "diagram_reference"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DIAGRAM_DIR_NAME = "extracted_diagrams"

# Conversation history
MAX_HISTORY_TURNS = 5  # earlier Q&A turns sent to the LLM
HISTORY_DOC_TURNS = 2  # cited documents are carried over from this many most recent turns
MAX_HISTORY_DOCS = 30  # cap on carried-over documents (most recent kept)
REWRITE_MAX_TOKENS = 4096  # room for reasoning models when rewriting a follow-up
REWRITE_ANSWER_CHARS = 1500  # earlier answers are truncated to this in the rewrite prompt
REWRITE_MAX_QUERY_CHARS = 500  # longer rewrites are treated as failures

REWRITE_PROMPT = """You rewrite the user's latest message into a standalone search query for a document database.
Use the conversation for context: resolve references such as "it", "that", "the diagram" or "step 3" to what they mean.
If the latest message is already standalone, return it unchanged.
Output only the query text, with no explanation, quotes or prefix."""

SYSTEM_PROMPT = """You are HaRVey, an assistant that answers questions about equipment manuals using only the documents retrieved for you.

## Output
- Every reply is exactly one tool call. Never reply with plain text.
- Final replies: respond_to_user (an answer), insufficient_evidence (the documents do not cover the question) or ask_clarifying_question (see Finishing).
- Gathering context (you continue afterwards): search_documents, read_section, read_page.

## Documents
- Each retrieved document has a numeric "ref", a filename, a page, a chunk_type and its full text, listed in reading order.
- "neighbor": true means it was not matched directly but sits next to a match, or is a diagram on the same page as a match.
- "earlier_turn": true means it was retrieved for an earlier question in this conversation and is still available.
- "page" is the PDF page number, which can differ from the page number printed in the document (e.g. PDF page 16 may be printed as page 13). Always use the "page" value for citations and for read_page / search filters.
- Most documents have a "section": the heading path in the source document where the passage appears, e.g. "MAINTENANCE > A. Air Filter". Passages with the same section belong together, and a section can span several pages. Use the section to tell which procedure, component or model a passage belongs to, and do not mix steps from different sections unless the question needs both.

## Conversation
- Earlier user questions and your earlier replies (shown as your tool calls) come before the current question. Use them to understand follow-up questions (e.g. "that diagram", "step 3", "what about the other model?"). Answer only from the documents, not from your earlier replies alone.

## Answering (respond_to_user)
- Write a clear, concise "answer" synthesized from the documents. Markdown is allowed (short paragraphs, bullet lists, **bold** for warnings). Keep it short: your whole reply must be a valid tool call, and very long arguments can fail to parse.
- List the documents you used in "sources": filename, page, section (copied exactly from the document) and a snippet that is a short quote of at most one sentence.
- Never write file paths, image markdown (![...](...)), or ref numbers in the answer, steps or snippets: images are attached automatically from diagram_refs.

## Procedures
- When the question asks how to do something (install, set up, configure, troubleshoot, operate), or is otherwise procedural:
  * Put the instructions ONLY in the "steps" field: one action per step, in the correct order, each citing its source_filename, page and section
  * "answer" is 1-3 sentences of introduction plus any important warnings. Never list the stages or steps in "answer"; they appear only in "steps"
  * If the procedure has more than one stage (e.g. "Ceiling template", "Divider plate", "Control box"), EVERY step must have "stage" set to the stage it belongs to, in document order
  * Each step must be a concrete action from the documents (e.g. "Tighten the four mounting bolts to 40-50 inch-pounds"), not just the name of a stage (e.g. "Install the control box")
  * Do not skip or invent steps; include prerequisites, warnings and tools mentioned in the documents
- Whole-process questions (e.g. "steps to install X", "how do I set up X"): give an overview that covers every top-level stage the documents describe for that process, in order, not only the first section you found. For each stage give its 2-4 most important concrete steps (with "stage" set); do not read every stage in full. End "answer" by offering the complete steps for any stage (e.g. "Ask me for the full steps of any stage."). If a stage is missing from the documents you have, say in "answer" which stage was left out.
- When the user asks for the full steps of one stage, give every step of that stage, calling read_section for it first if you do not have all of its content.

## Diagrams
- Documents with chunk_type "diagram_reference" are figures from the documents. The user sees the diagrams you choose as images next to your answer, which makes instructions, parts, locations, wiring and dimensions much easier to follow.
- In EVERY respond_to_user call, whether or not the user asked for a diagram, put in diagram_refs the ref of each diagram that illustrates what your answer describes. Prefer diagrams in the same section or on the same page as the passages you used. Choose at most 4, most relevant first. Use an empty list only when no retrieved diagram relates to the answer.
- When present, the "Diagrams available" list after the documents names every diagram you can show, with its ref, page and section. Only use diagram ref values that appear in the documents.
- If the user asks to see a diagram and no relevant diagram_reference document is in your context, call search_documents (e.g. "diagram of <topic>") or read_section for the relevant section before answering.

## Gathering more context
- If the documents are not enough to answer fully, do not answer with partial information and do not give up yet: first search or read more, then answer from the new context.
- Signs the documents are incomplete: missing steps, "see section X", missing prerequisites, warnings or specifications, a procedure that starts or stops abruptly, or a heading without its content.
  * Found the start of a section (e.g. "MAINTENANCE > A. Air Filter") but not all of its content: call read_section with that document's filename and "section" value instead of searching again. For "see section X", call read_section with X
  * A document has no "section": call read_page for its page and the next one
  * Otherwise call search_documents with a short, targeted query for what is missing, optionally restricted with filename and page_start/page_end
  * Do not repeat a search, section read or page read you already made
  * You have a limited number of searches and reads in total; when told to stop, answer with what you have

## Finishing
- respond_to_user when the documents support an answer. If your answer would be that the documents do not mention or cover what was asked, call insufficient_evidence instead; never use respond_to_user to say the information is missing.
- insufficient_evidence only when the documents clearly do not address the question after you have searched, or when no more searches are available. Explain why, referring to the question.
- ask_clarifying_question only when the question does not say which model, series or document it is about AND the documents give materially different answers for them. Offer the choices found in the documents as options. Do not ask when the question can be answered as asked.

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
                "answer": {"type": "string", "description": "The answer to the user's question (markdown allowed). For procedural questions: 1-3 sentences of introduction plus warnings only; never list the steps here"},
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "filename": {"type": "string"},
                            "page": {"type": "integer"},
                            "section": {"type": "string", "description": "Optional: the document's 'section' value, copied exactly"},
                            "snippet": {"type": "string", "description": "A short quote from the document, at most one sentence"}
                        }
                    },
                    "description": "Documents used in the answer (always shown to the user)"
                },
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "stage": {"type": "string", "description": "Optional: the stage of a multi-stage procedure this step belongs to (e.g. 'Ceiling template'); steps of one stage are grouped under it"},
                            "instruction": {"type": "string", "description": "One action to perform"},
                            "source_filename": {"type": "string"},
                            "page": {"type": "integer"},
                            "section": {"type": "string", "description": "Optional: the source document's 'section' value, copied exactly"}
                        },
                        "required": ["instruction"]
                    },
                    "description": "Ordered step-by-step instructions for procedural questions: concrete actions from the documents, with 'stage' set on every step when there is more than one stage"
                },
                "diagram_refs": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": (
                        f"'ref' values of retrieved diagram_reference documents that illustrate the answer, most relevant "
                        f"first (max {MAX_DIAGRAMS_PER_ANSWER}). Shown to the user as images. Include them even when no "
                        "diagram was requested; [] only if no retrieved diagram relates to the answer."
                    )
                }
            },
            "required": ["answer", "diagram_refs"]
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
        "name": "read_section",
        "description": "Read every passage of one section of a document (and its sub-sections) in reading order. Use to get the rest of a section whose heading or start you found, or a section the documents refer to.",
        "parameters": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Source filename as shown in the documents"},
                "section": {"type": "string", "description": "Section to read: a 'section' value from the documents (e.g. 'MAINTENANCE > A. Air Filter'), or just a heading (e.g. 'Air Filter' or 'MAINTENANCE')"}
            },
            "required": ["filename", "section"]
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
                "explanation": {"type": "string", "description": "Why the documents do not answer the question"}
            },
            "required": ["explanation"]
        }
    },
    {
        "name": "ask_clarifying_question",
        "description": "Ask the user which model, series or document they mean, when the question does not say and the documents give materially different answers. Ends your turn; the user's reply comes as the next question.",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question to ask the user"},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": f"Optional: up to {MAX_CLARIFY_OPTIONS} short answer choices found in the documents (e.g. model names)"
                }
            },
            "required": ["question"]
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


def _recent_history(history: list[ChatTurn]) -> list[ChatTurn]:
    """The earlier turns that are sent to the LLM (most recent MAX_HISTORY_TURNS)."""
    return list(history[-MAX_HISTORY_TURNS:]) if MAX_HISTORY_TURNS > 0 else []


def _strip_think(text: str) -> str:
    """Remove <think>...</think> reasoning blocks some models put in their content."""
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()


def _clean_rewrite(text: str) -> str:
    """Strip reasoning tags, a 'Query:' prefix and surrounding quotes from a rewrite."""
    text = _strip_think(text)
    text = re.sub(r"^(standalone\s+)?(search\s+)?query\s*:\s*", "", text, flags=re.IGNORECASE)
    return text.strip().strip('"\'`').strip()


def rewrite_query(state: QueryState) -> dict[str, Any]:
    """
    Turn a follow-up question into a standalone search query.

    With no chat history the query is used as-is (no LLM call). Otherwise one
    LLM call rewrites it using the recent conversation, so retrieval finds what
    "that" or "step 3" refers to. Any failure falls back to the original query;
    the rewrite only affects retrieval, the LLM still answers the user's words.
    """
    logger.info("Entering rewrite_query node")

    history = _recent_history(state.chat_history)
    if not state.query_validated or not state.original_query or not history or not state.config:
        return {"retrieval_query": state.original_query}

    transcript: list[str] = []
    for turn in history:
        answer = turn.answer
        if len(answer) > REWRITE_ANSWER_CHARS:
            answer = answer[:REWRITE_ANSWER_CHARS] + "..."
        transcript.append(f"User: {turn.question}\nAssistant: {answer}")
    user_message = (
        "Conversation so far:\n\n" + "\n\n".join(transcript)
        + f"\n\nLatest message: {state.original_query}\n\nStandalone search query:"
    )

    try:
        llm_service = LLMService(
            base_url=state.config.llm_base_url,
            model=state.config.llm_model,
            api_key=state.config.llm_api_key,
            max_tokens=REWRITE_MAX_TOKENS,
        )
        rewritten = _clean_rewrite(llm_service.chat(
            messages=[
                {"role": "system", "content": REWRITE_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.0,
        ))
    except Exception as e:
        logger.warning(f"rewrite_query: rewrite failed, using original query: {str(e)}")
        return {"retrieval_query": state.original_query}

    if not rewritten or len(rewritten) > REWRITE_MAX_QUERY_CHARS:
        logger.warning(f"rewrite_query: unusable rewrite ({len(rewritten)} chars), using original query")
        return {"retrieval_query": state.original_query}

    logger.info(f"rewrite_query: {state.original_query!r} -> {rewritten!r}")
    return {"retrieval_query": rewritten, "status": "query_rewritten"}


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
        
        # Generate embedding for the (possibly rewritten) query
        query_embedding = embedding_service.embed_text(state.retrieval_query or state.original_query)
        
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
            min_similarity_score=_min_similarity(),
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
            hits = search_results  # best score first, before neighbors re-sort them
            search_results = _add_neighbors(vector_store, hits)
            diagrams = _page_diagrams(vector_store, hits, {_chunk_key(r) for r in search_results})
            if diagrams:
                search_results = sorted(search_results + diagrams, key=_reading_order_key)

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
        # Optional keys: absent on records ingested before diagram / section support
        chunk_type=meta.get("chunk_type", "text"),
        diagram_path=meta.get("diagram_path", ""),
        diagram_kind=meta.get("diagram_kind", ""),
        section_headings=_parse_section(meta.get("section_path", "")),
    )
    return QueryResult(
        content=result["content"],
        metadata=metadata,
        similarity_score=result["similarity_score"],
    )


def _parse_section(value: Any) -> list[str]:
    """Split a stored section_path ("A > B") back into its headings."""
    if not isinstance(value, str):
        return []
    return [part.strip() for part in value.split(SECTION_SEPARATOR) if part.strip()]


def _section_of(result: QueryResult) -> str:
    """The chunk's section path as one string ("" for records without one)."""
    return SECTION_SEPARATOR.join(result.metadata.section_headings)


def _strip_section_line(content: str, section: str) -> str:
    """
    Remove the "Section: ..." line ingestion puts in every chunk's text.

    The LLM gets the section as its own field, so the line would be a duplicate.
    """
    if not section:
        return content
    lines = content.split("\n")
    marker = f"Section: {section}"
    if marker in lines:
        lines.remove(marker)
    return "\n".join(lines)


def _chunk_key(result: QueryResult) -> tuple[str, int]:
    """Identity of a chunk, used to avoid showing the same passage twice."""
    return (result.metadata.source_filename, result.metadata.chunk_sequence)


def _format_result_for_llm(ref: int, result: QueryResult) -> dict:
    """Describe one retrieved chunk (full text) for the LLM, keyed by a stable ref."""
    section = _section_of(result)
    part = {
        "ref": ref,
        "filename": result.metadata.source_filename,
        "page": result.metadata.page_number,
        "chunk_type": result.metadata.chunk_type,
    }
    if section:
        part["section"] = section
    part["text"] = _strip_section_line(result.content, section)
    if result.is_neighbor:
        part["neighbor"] = True
    if result.from_history:
        part["earlier_turn"] = True
    if result.metadata.chunk_type == DIAGRAM_CHUNK_TYPE:
        # No diagram_path: the LLM picks diagrams by ref and must not echo file paths
        part["diagram_kind"] = result.metadata.diagram_kind
    return part


def _to_json(parts: list[dict]) -> str:
    """Documents for the LLM as compact JSON (no indentation; about 25% fewer tokens)."""
    return json.dumps(parts, ensure_ascii=False)


def _diagram_menu(results: list[QueryResult]) -> str:
    """
    "Diagrams available" list appended after the documents: one line per
    showable diagram (image file exists) with its ref, page, section and kind,
    so the choices for diagram_refs are not buried in the JSON. "" when none.
    """
    lines: list[str] = []
    for ref, r in enumerate(results):
        if r.metadata.chunk_type != DIAGRAM_CHUNK_TYPE or _resolve_diagram_file(r.metadata.diagram_path) is None:
            continue
        bits = [f"{r.metadata.source_filename} p.{r.metadata.page_number}"]
        if _section_of(r):
            bits.append(_section_of(r))
        if r.metadata.diagram_kind:
            bits.append(r.metadata.diagram_kind)
        lines.append(f"- ref {ref}: " + ", ".join(bits))
    if not lines:
        return ""
    return "Diagrams available (use these refs in diagram_refs):\n" + "\n".join(lines)


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
    a hit alone can be misleading. A neighbor is only added when it is in the
    same section as the hit it sits next to (hits from records without a
    section accept any neighbor). Neighbors are marked is_neighbor and the
    combined list is returned in reading order. Failures are logged and the
    original results are returned unchanged.
    """
    if NEIGHBOR_RADIUS <= 0 or NEIGHBOR_TOP_HITS <= 0:
        return results
    have = {_chunk_key(r) for r in results}
    # filename -> chunk_sequence -> sections of the hits that want this neighbor
    wanted: dict[str, dict[int, set[str]]] = {}
    for r in results[:NEIGHBOR_TOP_HITS]:
        if r.metadata.chunk_type != "text":
            continue
        for offset in range(-NEIGHBOR_RADIUS, NEIGHBOR_RADIUS + 1):
            seq = r.metadata.chunk_sequence + offset
            if offset and seq >= 0 and (r.metadata.source_filename, seq) not in have:
                wanted.setdefault(r.metadata.source_filename, {}).setdefault(seq, set()).add(_section_of(r))

    added: list[QueryResult] = []
    try:
        for filename, by_seq in wanted.items():
            where = {"$and": [
                {"source_filename": {"$eq": filename}},
                {"chunk_sequence": {"$in": sorted(by_seq)}},
            ]}
            for raw in vector_store.get_chunks(where):
                try:
                    candidate = _build_query_result(raw)
                except (KeyError, TypeError):
                    continue
                if candidate.metadata.chunk_type != "text" or _chunk_key(candidate) in have:
                    continue
                allowed = by_seq.get(candidate.metadata.chunk_sequence, set())
                if "" not in allowed and _section_of(candidate) not in allowed:
                    continue  # belongs to a different section than the hit
                candidate.is_neighbor = True
                have.add(_chunk_key(candidate))
                added.append(candidate)
    except Exception as e:
        logger.warning(f"Neighbor expansion failed, continuing with direct hits only: {str(e)}")
        return results
    
    logger.info(f"vector_store_search: added {len(added)} neighbor chunk(s) as context")
    return sorted(results + added, key=_reading_order_key)


def add_history_documents(state: QueryState) -> dict[str, Any]:
    """
    Carry documents from recent turns into this turn's search results.

    Documents cited by the answers of the last HISTORY_DOC_TURNS turns
    (ChatTurn.documents, i.e. that turn's source_documents; at most
    MAX_HISTORY_DOCS, most recent first) are copied in, marked from_history,
    skipping any chunk this turn already retrieved. The combined list is put in
    reading order, so every document gets one ref and diagram_refs stay valid.
    This also lets a follow-up be answered when the new search found nothing.
    """
    logger.info("Entering add_history_documents node")

    if HISTORY_DOC_TURNS <= 0 or not state.chat_history:
        return {}

    results = list(state.search_results)
    have = {_chunk_key(r) for r in results}
    carried: list[QueryResult] = []
    for turn in reversed(state.chat_history[-HISTORY_DOC_TURNS:]):
        for doc in turn.documents:
            if len(carried) >= MAX_HISTORY_DOCS:
                break
            key = _chunk_key(doc)
            if key in have:
                continue
            have.add(key)
            carried.append(replace(doc, from_history=True))

    if not carried:
        return {}

    logger.info(f"add_history_documents: carried over {len(carried)} document(s) from earlier turns")
    update: dict[str, Any] = {"search_results": sorted(results + carried, key=_reading_order_key)}
    if not results:
        update["status"] = "history_documents_only"
    return update


def _page_diagrams(vector_store: Any, hits: list[QueryResult], have: set[tuple[str, int]]) -> list[QueryResult]:
    """
    Diagram chunks on the same pages as the top PAGE_DIAGRAM_TOP_HITS hits.

    A diagram chunk's text ("Figure on page N ... Text in figure: ...") rarely
    ranks highly for a "how do I ..." question, so without this the LLM often
    has no diagram to show. Chunks in `have` are skipped. Diagrams in the same
    section as a hit come first; at most PAGE_DIAGRAM_MAX are returned, marked
    is_neighbor. Failures are logged and return [].
    """
    if PAGE_DIAGRAM_TOP_HITS <= 0 or PAGE_DIAGRAM_MAX <= 0:
        return []
    pages: dict[str, set[int]] = {}
    hit_sections: set[tuple[str, str]] = set()
    for r in hits[:PAGE_DIAGRAM_TOP_HITS]:
        pages.setdefault(r.metadata.source_filename, set()).add(r.metadata.page_number)
        if _section_of(r):
            hit_sections.add((r.metadata.source_filename, _section_of(r)))

    seen = set(have)
    found: list[QueryResult] = []
    try:
        for filename, page_set in pages.items():
            where = {"$and": [
                {"source_filename": {"$eq": filename}},
                {"chunk_type": {"$eq": DIAGRAM_CHUNK_TYPE}},
                {"page_number": {"$in": sorted(page_set)}},
            ]}
            for raw in vector_store.get_chunks(where):
                try:
                    candidate = _build_query_result(raw)
                except (KeyError, TypeError):
                    continue
                if candidate.metadata.chunk_type != DIAGRAM_CHUNK_TYPE or _chunk_key(candidate) in seen:
                    continue
                candidate.is_neighbor = True
                seen.add(_chunk_key(candidate))
                found.append(candidate)
    except Exception as e:
        logger.warning(f"Page diagram lookup failed, continuing without: {str(e)}")
        return []

    found.sort(key=lambda c: (
        (c.metadata.source_filename, _section_of(c)) not in hit_sections,  # same section first
        _reading_order_key(c),
    ))
    if found:
        logger.info(f"Added {min(len(found), PAGE_DIAGRAM_MAX)} diagram(s) from the hits' pages")
    return found[:PAGE_DIAGRAM_MAX]


def _min_similarity() -> float:
    """
    Similarity cutoff shared by the first retrieval (vector_store_search) and
    the search_documents tool: SEARCH_MIN_SIMILARITY in .env, default
    DEFAULT_MIN_SIMILARITY. Invalid values fall back to the default.
    """
    raw = os.getenv("SEARCH_MIN_SIMILARITY", "").strip()
    if not raw:
        return DEFAULT_MIN_SIMILARITY
    try:
        return float(raw)
    except ValueError:
        logger.warning(f"Invalid SEARCH_MIN_SIMILARITY={raw!r}; using {DEFAULT_MIN_SIMILARITY}")
        return DEFAULT_MIN_SIMILARITY


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
{_to_json(context_parts)}
"""
        menu = _diagram_menu(state.search_results)
        if menu:
            user_message += f"\n{menu}\n"
        # Earlier turns (replayed as tool calls) go between the system prompt and the current question
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *_history_messages(_recent_history(state.chat_history)),
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
    attempt_messages = messages  # a retry after a plain-text reply adds a nudge, never kept in state
    plain_text: str | None = None  # text of the last reply that had no tool call
    # Retry loop for tool calling
    for attempt in range(MAX_TOOL_CALL_RETRIES):
        try:
            result = llm_service.chat_with_tools(
                messages=attempt_messages,
                tools=tools,
                temperature=DEFAULT_LLM_TEMPERATURE if attempt == 0 else RETRY_LLM_TEMPERATURE,
            )
            tool_call = ToolCall(
                tool_name=result["tool_name"],
                parameters=result["parameters"],
                tool_call_id=result.get("tool_call_id"),
            )
            assistant_message = result.get("assistant_message")
            logger.info(f"generate_response: Tool call '{tool_call.tool_name}' succeeded on attempt {attempt + 1}")
            break

        except NoToolCallError as e:
            plain_text = _strip_think(e.content)
            logger.warning(
                f"generate_response: attempt {attempt + 1} replied without a tool call "
                f"({len(plain_text)} chars of text): {plain_text[:500]!r}"
            )
            attempt_messages = messages + [
                {"role": "assistant", "content": plain_text or "(empty reply)"},
                {"role": "user", "content": TOOL_NUDGE},
            ]
            if attempt == MAX_TOOL_CALL_RETRIES - 1 and not plain_text:
                errors.append(f"LLM tool calling failed after {MAX_TOOL_CALL_RETRIES} attempts: {str(e)}")

        except MalformedToolCallError as e:
            # The server could not parse the model's tool call; retry with a reminder (not kept in state)
            plain_text = None
            logger.warning(f"generate_response: attempt {attempt + 1} produced an unparseable tool call: {str(e)[:300]}")
            attempt_messages = messages + [{"role": "user", "content": MALFORMED_TOOL_NUDGE}]
            if attempt == MAX_TOOL_CALL_RETRIES - 1:
                errors.append(f"LLM tool calling failed after {MAX_TOOL_CALL_RETRIES} attempts: {str(e)}")

        except (ConnectionError, ValueError) as e:
            plain_text = None
            error_msg = f"Tool call attempt {attempt + 1} failed: {str(e)}"
            logger.error(error_msg)
            if attempt < MAX_TOOL_CALL_RETRIES - 1:
                logger.info(f"Retrying... (attempt {attempt + 1}/{MAX_TOOL_CALL_RETRIES})")
            else:
                logger.error(f"LLM tool calling failed after {MAX_TOOL_CALL_RETRIES} attempts. Last error: {str(e)}")
                errors.append(f"LLM tool calling failed after {MAX_TOOL_CALL_RETRIES} attempts: {str(e)}")

    if not tool_call and plain_text:
        # Salvage: the model kept answering in plain text, so show that text as the answer
        logger.warning("generate_response: no tool call after retries; using the plain-text reply as the answer")
        return {
            "tool_call": ToolCall(tool_name="respond_to_user",
                                  parameters={"answer": plain_text, "diagram_refs": []}),
            "messages": messages,
            "errors": errors,
            "started_at": started_at,
            "status": "salvaged_text_reply",
        }

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
        min_similarity_score=_min_similarity(),
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

    if new_results:
        new_results.extend(_page_diagrams(vector_store, new_results, seen))

    if not new_results:
        return [], "No new relevant passages found for this search. Try different wording or filters, or answer with what you have."
    
    first_ref = len(existing)
    parts = [_format_result_for_llm(first_ref + i, r) for i, r in enumerate(new_results)]
    text = f"Found {len(new_results)} new passage(s):\n{_to_json(parts)}"
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
        lines.append(f"New passage(s):\n{_to_json(parts)}")
    else:
        lines.append("Nothing new: every passage on these pages is already in your context.")
    return new_results, "\n".join(lines)


_SECTION_LABEL = re.compile(r"^(?:[A-Z]|\d{1,2}(?:\.\d+)*|[IVX]{1,4})[.)]\s+")


def _heading_key(heading: str) -> str:
    """Comparable heading: case, spacing and a leading "A." / "1." label ignored."""
    return " ".join(_SECTION_LABEL.sub("", heading.strip()).split()).lower()


def _match_sections(requested: str, paths: list[str]) -> list[str]:
    """
    Find the section(s) a read_section request refers to.

    Accepts a full path ("MAINTENANCE > A. Air Filter") or its last part(s)
    ("A. Air Filter", "Air Filter", "MAINTENANCE"), ignoring case and labels.
    Returns the matching section paths (a match on a parent section returns
    the parent path; its sub-sections are read with it).
    """
    wanted = [_heading_key(p) for p in requested.split(">") if p.strip()]
    if not wanted:
        return []
    matches: list[str] = []
    for path in paths:
        headings = path.split(SECTION_SEPARATOR)
        keys = [_heading_key(h) for h in headings]
        for end in range(len(wanted), len(keys) + 1):
            if keys[end - len(wanted):end] == wanted:
                root = SECTION_SEPARATOR.join(headings[:end])
                if root not in matches:
                    matches.append(root)
                break
    return matches


def _in_section(section: str, root: str) -> bool:
    return section == root or section.startswith(root + SECTION_SEPARATOR)


def _run_read_section(config: Any, params: dict, existing: list[QueryResult]) -> tuple[list[QueryResult], str]:
    """
    Execute a read_section call: every chunk of a section and its sub-sections,
    in reading order (at most READ_SECTION_MAX_CHUNKS).

    Returns (new_results, tool_message_text). When the request matches no
    section, or several, the message lists the document's sections instead.
    Chunks already in context are listed by ref, not repeated.

    Raises:
        ValueError, ConnectionError, RuntimeError on bad input or service errors
    """
    requested = params.get("section")
    if not isinstance(requested, str) or not requested.strip():
        raise ValueError("read_section requires a non-empty 'section'")
    filename = _resolve_filename(params.get("filename"), existing)
    if not filename:
        raise ValueError("read_section requires a 'filename'")

    vector_store = ChromaVectorStore(
        persist_directory=config.chroma_persist_directory,
        collection_name=config.chroma_collection_name,
    )
    vector_store.initialize_collection()
    chunks: list[QueryResult] = []
    for raw in vector_store.get_chunks({"source_filename": {"$eq": filename}}):
        try:
            chunks.append(_build_query_result(raw))
        except (KeyError, TypeError) as e:
            logger.error(f"Failed to parse chunk {raw.get('id', 'unknown')}: {str(e)}")

    paths: list[str] = []
    for chunk in chunks:
        section = _section_of(chunk)
        if section and section not in paths:
            paths.append(section)
    if not paths:
        return [], (f"{filename} has no section information (ingested before sections were added). "
                    "Use read_page or search_documents instead.")

    def section_list() -> str:
        shown = paths[:READ_SECTION_LIST_LIMIT]
        more = f" (and {len(paths) - len(shown)} more)" if len(paths) > len(shown) else ""
        return "\n".join(f"- {p}" for p in shown) + more

    matches = _match_sections(requested, paths)
    if not matches:
        return [], (f"No section matching {requested!r} in {filename}. Its sections are:\n{section_list()}\n"
                    "Call read_section again with one of these.")
    if len(matches) > 1:
        listed = "\n".join(f"- {m}" for m in matches)
        return [], (f"{requested!r} matches several sections in {filename}:\n{listed}\n"
                    "Call read_section again with the full section you want.")

    root = matches[0]
    in_root = sorted((c for c in chunks if _in_section(_section_of(c), root)),
                     key=lambda c: c.metadata.chunk_sequence)
    total = len(in_root)
    selected, cut = in_root[:READ_SECTION_MAX_CHUNKS], in_root[READ_SECTION_MAX_CHUNKS:]

    refs_by_key = {_chunk_key(r): ref for ref, r in enumerate(existing)}
    new_results: list[QueryResult] = []
    already: list[int] = []
    for result in selected:
        key = _chunk_key(result)
        if key in refs_by_key:
            already.append(refs_by_key[key])
            continue
        result.is_neighbor = True  # read on request, not matched by a query
        refs_by_key[key] = len(existing) + len(new_results)
        new_results.append(result)

    pages = sorted({c.metadata.page_number for c in selected})
    page_text = f"page {pages[0]}" if len(pages) == 1 else f"pages {pages[0]}-{pages[-1]}"
    lines = [f"{filename}, section '{root}' ({page_text}): {total} passage(s) in reading order."]
    if cut:
        rest: list[str] = []
        for c in cut:
            if _section_of(c) not in rest:
                rest.append(_section_of(c))
        lines.append(f"Showing the first {len(selected)}. Not shown (fully or partly): "
                     + ", ".join(repr(p) for p in rest) + ". Read them with read_section if needed.")
    if already:
        lines.append(f"Already in your context (not repeated): refs {sorted(already)}.")
    if new_results:
        first_ref = len(existing)
        parts = [_format_result_for_llm(first_ref + i, r) for i, r in enumerate(new_results)]
        lines.append(f"New passage(s):\n{_to_json(parts)}")
    else:
        lines.append("Nothing new: every passage of this section is already in your context.")
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
        elif tool_call.tool_name == "read_section":
            new_results, content = _run_read_section(state.config, tool_call.parameters, search_results)
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
    """
    Turn the final tool call into the structured response and its text.

    response = {
        "kind": "answer" | "insufficient" | "clarify" | "error",
        "answer": str,                                   # may contain markdown
        "steps": [{"stage", "instruction", "citation"}],
        "sources": [{"filename", "page", "section", "snippet"}],
        "diagrams": [{"filename", "page", "kind", "section", "path"}],
        "options": [str],                                # clarify only
    }
    final_response is format_response_text(response), for the CLI and tests.
    Errors keep their technical details in `errors`; the user sees ERROR_MESSAGE.
    """
    logger.info("Entering process_tool_call node")

    errors: list[str] = list(state.errors)

    if not state.tool_call:
        error_msg = "No tool call available in process_tool_call node"
        logger.error(error_msg)
        errors.append(error_msg)
        return _error_result(errors)

    name = state.tool_call.tool_name
    params = state.tool_call.parameters if isinstance(state.tool_call.parameters, dict) else {}
    source_documents: list[QueryResult] = []
    try:
        if name == "respond_to_user":
            params = _with_fallback_diagrams(params, state.search_results)
            response = _build_answer_response(params, state.search_results)
            source_documents = _cited_documents(params, state.search_results)
            status = "response_generated"
        elif name == "insufficient_evidence":
            response = _build_insufficient_response(params, state.search_results)
            status = "insufficient_evidence_returned"
        elif name == "ask_clarifying_question":
            response = _build_clarify_response(params)
            status = "clarification_requested"
        else:
            error_msg = f"Unknown tool: {name}"
            logger.error(error_msg)
            errors.append(error_msg)
            return _error_result(errors)

        final_response = format_response_text(response)
        logger.info(
            f"process_tool_call: {response['kind']} response ({len(final_response)} chars), "
            f"{len(response['steps'])} step(s), {len(response['sources'])} source(s), "
            f"{len(response['diagrams'])} diagram(s), {len(source_documents)} cited document(s)"
        )
        return {
            "response": response,
            "final_response": final_response,
            "source_documents": source_documents,
            "errors": errors,
            "status": status,
        }

    except Exception as e:
        error_msg = f"Error processing tool call: {str(e)}"
        logger.error(error_msg)
        errors.append(error_msg)
        return _error_result(errors)


def _error_result(errors: list[str]) -> dict[str, Any]:
    """process_tool_call result for a failed turn: friendly text, details in errors."""
    response = _error_response()
    return {
        "response": response,
        "final_response": format_response_text(response),
        "source_documents": [],
        "errors": errors,
        "status": "response_generated_with_errors",
    }


def _same_file(given: Any, stored: str) -> bool:
    """The LLM's filename refers to this stored filename (exact or substring, any case)."""
    if not isinstance(given, str) or not given.strip():
        return True  # no filename given: don't filter on it
    given, stored = given.strip().lower(), stored.lower()
    return given in stored or stored in given


def _as_page(value: Any) -> int | None:
    """A cited page number as int (accepts "3"), or None if it isn't one."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _cited_documents(params: dict, search_results: list[QueryResult]) -> list[QueryResult]:
    """
    The retrieved documents a respond_to_user answer actually used.

    - Each entry in "sources" selects every retrieved text/table chunk with the
      same page and a matching filename (see _same_file).
    - Diagram chunks are included only when shown (valid "diagram_refs"), not
      just because they sit on a cited page.

    Returned in reading order with from_history cleared, ready to be stored as
    ChatTurn.documents. These, not all retrieved chunks, carry into later turns.
    """
    cited: set[int] = set()

    sources = params.get("sources")
    for src in sources if isinstance(sources, list) else []:
        if not isinstance(src, dict):
            continue
        page = _as_page(src.get("page"))
        if page is None:
            continue
        for i, r in enumerate(search_results):
            if (r.metadata.page_number == page and r.metadata.chunk_type != DIAGRAM_CHUNK_TYPE
                    and _same_file(src.get("filename"), r.metadata.source_filename)):
                cited.add(i)

    cited.update(_shown_diagram_refs(params.get("diagram_refs"), search_results))

    docs = [replace(search_results[i], from_history=False) for i in cited]
    return sorted(docs, key=_reading_order_key)


def _cite_section(
    search_results: list[QueryResult],
    filename: Any,
    page: Any,
    given: Any = None,
    snippet: Any = None,
) -> str:
    """
    Section to show in a citation, taken from the retrieved documents only.

    1. The LLM's "section", if it matches a retrieved document's section
       (preferring one on the cited page).
    2. The section of the retrieved document on that page whose text
       contains the snippet.
    3. The only section among retrieved documents on that page.
    Otherwise "" (nothing is shown rather than a guess).
    """
    page_ok = isinstance(page, int) and not isinstance(page, bool)
    docs = [r for r in search_results if _section_of(r) and _same_file(filename, r.metadata.source_filename)]
    on_page = [r for r in docs if page_ok and r.metadata.page_number == page]

    if isinstance(given, str) and given.strip():
        wanted = " ".join(given.split()).lower()
        for r in on_page + docs:
            if " ".join(_section_of(r).split()).lower() == wanted:
                return _section_of(r)

    if isinstance(snippet, str) and snippet.strip():
        needle = " ".join(snippet.split()).lower()[:60]
        for r in on_page:
            if needle and needle in " ".join(r.content.split()).lower():
                return _section_of(r)

    sections = {_section_of(r) for r in on_page}
    return sections.pop() if len(sections) == 1 else ""


def _clean_snippet(text: Any, limit: int = SNIPPET_MAX_CHARS) -> str:
    """One-line snippet: literal "\\n" and whitespace collapsed, cut at a word boundary + "…"."""
    if not isinstance(text, str):
        return ""
    text = " ".join(text.replace("\\n", " ").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] or text[:limit]
    return cut.rstrip(" ,;:.") + "…"


def _build_sources(sources: Any, search_results: list[QueryResult]) -> list[dict]:
    """
    The answer's sources: short snippets, section from the retrieved documents
    (see _cite_section), duplicates of the same (file, page, section) dropped.
    """
    built: list[dict] = []
    seen: set[tuple[str, int | None, str]] = set()
    for src in sources if isinstance(sources, list) else []:
        if not isinstance(src, dict):
            continue
        filename = src.get("filename") if isinstance(src.get("filename"), str) and src.get("filename").strip() else "unknown"
        page = _as_page(src.get("page"))
        section = _cite_section(search_results, filename, page, src.get("section"), src.get("snippet"))
        key = (filename.strip().lower(), page, section)
        if key in seen:
            continue
        seen.add(key)
        built.append({
            "filename": filename.strip(),
            "page": page,
            "section": section,
            "snippet": _clean_snippet(src.get("snippet")),
        })
    return built


def _build_steps(steps: Any, search_results: list[QueryResult] | None = None) -> list[dict]:
    """
    The LLM's steps as [{"stage", "instruction", "citation"}], skipping malformed
    entries. The citation is "file, p.N, SECTION"; the section comes from the
    retrieved documents (see _cite_section) and is left out when unknown.
    """
    if not isinstance(steps, list):
        return []
    built: list[dict] = []
    for step in steps:
        stage = ""
        if isinstance(step, str):
            instruction, citation = step.strip(), ""
        elif isinstance(step, dict) and isinstance(step.get("instruction"), str):
            instruction = step["instruction"].strip()
            if isinstance(step.get("stage"), str):
                stage = step["stage"].strip()
            filename = step.get("source_filename")
            page = step.get("page")
            page_ok = isinstance(page, int) and not isinstance(page, bool)
            section = _cite_section(search_results or [], filename, page, step.get("section")) if page_ok else ""
            pieces = []
            if isinstance(filename, str) and filename:
                pieces.append(filename)
            if page_ok:
                pieces.append(f"p.{page}")
            if section:
                pieces.append(section)
            citation = ", ".join(pieces)
        else:
            continue
        if instruction:
            built.append({"stage": stage, "instruction": instruction, "citation": citation})
    return built


def _steps_text(steps: list[dict]) -> list[str]:
    """
    Text lines for built steps. With stages, each stage gets a "Stage:" heading
    and its own numbering; without, one flat numbered list.
    """
    if not any(s["stage"] for s in steps):
        return [
            f"{i}. {s['instruction']}" + (f" ({s['citation']})" if s["citation"] else "")
            for i, s in enumerate(steps, 1)
        ]
    lines: list[str] = []
    current: str | None = None
    number = 0
    for s in steps:
        if s["stage"] != current:
            current, number = s["stage"], 0
            if current:
                if lines:
                    lines.append("")
                lines.append(f"{current}:")
        number += 1
        lines.append(f"{number}. {s['instruction']}" + (f" ({s['citation']})" if s["citation"] else ""))
    return lines


def _build_answer_response(params: dict, search_results: list[QueryResult]) -> dict:
    """Structured response for respond_to_user (sources are always shown)."""
    answer = params.get("answer")
    return {
        "kind": "answer",
        "answer": answer.strip() if isinstance(answer, str) else "",
        "steps": _build_steps(params.get("steps"), search_results),
        "sources": _build_sources(params.get("sources"), search_results),
        "diagrams": _build_diagrams(params.get("diagram_refs"), search_results),
        "options": [],
    }


def _build_insufficient_response(params: dict, search_results: list[QueryResult]) -> dict:
    """
    Structured response for insufficient_evidence: the explanation, plus the
    single most relevant document as the only source (the highest-scoring direct
    hit; neighbor / page-read chunks are context and carry no real score).
    """
    explanation = params.get("explanation")
    sources: list[dict] = []
    if search_results:
        direct_hits = [r for r in search_results if not r.is_neighbor]
        best = max(direct_hits or search_results, key=lambda r: r.similarity_score)
        section = _section_of(best)
        sources.append({
            "filename": best.metadata.source_filename,
            "page": best.metadata.page_number,
            "section": section,
            "snippet": _clean_snippet(_strip_section_line(best.content, section), INSUFFICIENT_SNIPPET_CHARS),
        })
    return {
        "kind": "insufficient",
        "answer": explanation.strip() if isinstance(explanation, str) and explanation.strip() else "No explanation provided",
        "steps": [],
        "sources": sources,
        "diagrams": [],
        "options": [],
    }


def _build_clarify_response(params: dict) -> dict:
    """Structured response for ask_clarifying_question: the question and up to MAX_CLARIFY_OPTIONS choices."""
    question = params.get("question")
    options = params.get("options")
    clean_options = [
        o.strip() for o in (options if isinstance(options, list) else [])
        if isinstance(o, str) and o.strip()
    ][:MAX_CLARIFY_OPTIONS]
    return {
        "kind": "clarify",
        "answer": question.strip() if isinstance(question, str) and question.strip() else "Could you clarify your question?",
        "steps": [],
        "sources": [],
        "diagrams": [],
        "options": clean_options,
    }


def _error_response() -> dict:
    """Structured response for a failed turn (technical details stay in state.errors)."""
    return {"kind": "error", "answer": ERROR_MESSAGE, "steps": [], "sources": [], "diagrams": [], "options": []}


def _source_where(src: dict) -> str:
    page = src["page"] if src.get("page") is not None else "?"
    return f"page {page}, {src['section']}" if src.get("section") else f"page {page}"


def format_response_text(response: dict) -> str:
    """
    Plain-text rendering of a structured response (CLI, tests, final_response):
    answer, "Steps:", options, "Sources:" and "Diagrams:" blocks, each only when
    non-empty.
    """
    kind = response.get("kind")
    answer = response.get("answer", "")
    if kind == "error":
        return answer
    if kind == "insufficient":
        parts = ["⚠️  Insufficient Information", "", answer, ""]
        for src in response.get("sources", [])[:1]:
            parts.extend([
                "However, here is the most relevant document that was retrieved:",
                "",
                f"  Source: {src['filename']} ({_source_where(src)})",
                f"  Snippet: {src['snippet']}",
                "",
            ])
        return "\n".join(parts)

    parts = [answer] if answer else []
    steps = response.get("steps", [])
    if steps:
        parts.append("\nSteps:")
        parts.extend(_steps_text(steps))
    for option in response.get("options", []):
        parts.append(f"- {option}")
    sources = response.get("sources", [])
    if sources:
        parts.append("\n---\n\nSources:")
        for src in sources:
            line = f"- {src['filename']} ({_source_where(src)})"
            parts.append(f"{line}: {src['snippet']}" if src.get("snippet") else line)
    diagrams = response.get("diagrams", [])
    if diagrams:
        parts.append("\nDiagrams:")
        parts.extend(_diagram_line(d) for d in diagrams)
    return "\n".join(parts)


def response_history_text(response: dict) -> str:
    """
    The part of a response worth remembering as conversation history: the
    answer, its steps and any clarifying options. No sources, snippets or file
    paths (the documents themselves carry over separately).
    """
    parts = [response.get("answer", "")] if response.get("answer") else []
    steps = response.get("steps", [])
    if steps:
        parts.append("\nSteps:")
        parts.extend(_steps_text(steps))
    options = response.get("options", [])
    if options:
        parts.append("Options: " + "; ".join(options))
    return "\n".join(parts).strip()


# How a past turn's kind is replayed to the LLM: (tool name, argument key for the text)
HISTORY_TOOL_FOR_KIND = {
    "answer": ("respond_to_user", "answer"),
    "insufficient": ("insufficient_evidence", "explanation"),
    "clarify": ("ask_clarifying_question", "question"),
}


def _history_messages(history: list[ChatTurn]) -> list[dict]:
    """
    Earlier turns as the LLM would have produced them: user question, an
    assistant tool call carrying the answer, and the tool result. Replaying
    answers as tool calls (not plain assistant text) keeps every example in the
    context consistent with "always reply with a tool call".
    """
    messages: list[dict] = []
    for i, turn in enumerate(history):
        tool_name, key = HISTORY_TOOL_FOR_KIND.get(turn.kind, HISTORY_TOOL_FOR_KIND["answer"])
        arguments: dict[str, Any] = {key: turn.answer}
        if tool_name == "respond_to_user":
            arguments["diagram_refs"] = []  # refs are per turn; earlier ones mean nothing now
        call_id = f"hist_{i}"
        messages.append({"role": "user", "content": turn.question})
        messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": tool_name, "arguments": json.dumps(arguments)},
            }],
        })
        messages.append({"role": "tool", "tool_call_id": call_id, "content": "Shown to the user."})
    return messages


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


def _shown_diagram_refs(refs: Any, search_results: list[QueryResult]) -> list[int]:
    """
    The diagram refs that will actually be shown: valid, de-duplicated diagram
    chunks whose image file exists, in the LLM's order, capped at
    MAX_DIAGRAMS_PER_ANSWER.
    """
    if not isinstance(refs, list):
        return []
    shown: list[int] = []
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
        if _resolve_diagram_file(meta.diagram_path) is None:
            logger.warning(f"Ignoring diagram ref {ref}: file not found ({meta.diagram_path})")
            continue
        if len(shown) >= MAX_DIAGRAMS_PER_ANSWER:
            logger.info(f"Diagram cap ({MAX_DIAGRAMS_PER_ANSWER}) reached; ignoring the remaining refs")
            break
        shown.append(ref)
    return shown


def _build_diagrams(refs: Any, search_results: list[QueryResult]) -> list[dict]:
    """The diagrams shown with an answer (see _shown_diagram_refs), with absolute image paths."""
    built: list[dict] = []
    for ref in _shown_diagram_refs(refs, search_results):
        meta = search_results[ref].metadata
        built.append({
            "filename": meta.source_filename,
            "page": meta.page_number,
            "kind": meta.diagram_kind,
            "section": _section_of(search_results[ref]),
            "path": str(_resolve_diagram_file(meta.diagram_path)),
        })
    return built


def _diagram_line(diagram: dict) -> str:
    kind = f", {diagram['kind']}" if diagram.get("kind") else ""
    where = f", {diagram['section']}" if diagram.get("section") else ""
    return f"- {diagram['filename']} (page {diagram['page']}{kind}{where}): {diagram['path']}"


def _fallback_diagram_refs(params: dict, search_results: list[QueryResult]) -> list[int]:
    """
    Diagrams to attach when the LLM chose none: diagram chunks on the pages the
    answer's sources cite (same filename/page matching as _cited_documents).
    Diagrams in the same section as a cited source come first, then reading
    order; only ones with an image file, at most MAX_DIAGRAMS_PER_ANSWER.
    """
    cited_pages: set[tuple[str, int]] = set()
    cited_sections: set[tuple[str, str]] = set()
    sources = params.get("sources")
    for src in sources if isinstance(sources, list) else []:
        if not isinstance(src, dict):
            continue
        page = _as_page(src.get("page"))
        if page is None:
            continue
        for r in search_results:
            if r.metadata.page_number == page and _same_file(src.get("filename"), r.metadata.source_filename):
                cited_pages.add((r.metadata.source_filename, page))
        section = _cite_section(search_results, src.get("filename"), page, src.get("section"), src.get("snippet"))
        if section:
            for r in search_results:
                if _same_file(src.get("filename"), r.metadata.source_filename):
                    cited_sections.add((r.metadata.source_filename, section))

    candidates = [
        i for i, r in enumerate(search_results)
        if r.metadata.chunk_type == DIAGRAM_CHUNK_TYPE
        and (r.metadata.source_filename, r.metadata.page_number) in cited_pages
        and _resolve_diagram_file(r.metadata.diagram_path) is not None
    ]
    candidates.sort(key=lambda i: (
        (search_results[i].metadata.source_filename, _section_of(search_results[i])) not in cited_sections,
        _reading_order_key(search_results[i]),
    ))
    return candidates[:MAX_DIAGRAMS_PER_ANSWER]


def _with_fallback_diagrams(params: dict, search_results: list[QueryResult]) -> dict:
    """params with diagram_refs filled from the cited pages when the LLM chose no usable diagram."""
    if _shown_diagram_refs(params.get("diagram_refs"), search_results):
        return params
    refs = _fallback_diagram_refs(params, search_results)
    if not refs:
        return params
    logger.info(f"process_tool_call: no diagrams chosen; attaching {len(refs)} from the cited pages")
    return {**params, "diagram_refs": refs}
