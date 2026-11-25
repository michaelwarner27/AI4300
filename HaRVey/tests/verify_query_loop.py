"""
Offline verification of the query agent loop (search_documents + steps).

Needs no embedding/LLM server: the LLM is scripted, embeddings are fake and
Chroma is a temporary database.

Usage:
    python tests/verify_query_loop.py
"""

import json
import logging
import os
import shutil
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import IngestionConfig  # noqa: E402
from src.data_models import ChatTurn, ChunkMetadata, QueryResult, QueryState, TextChunk, ToolCall  # noqa: E402
from src.ingestion.tools import ChromaVectorStore  # noqa: E402
from src.query import graph as query_graph  # noqa: E402
from src.query import nodes  # noqa: E402
from src.query.tools import MalformedToolCallError, NoToolCallError  # noqa: E402

logging.disable(logging.CRITICAL)

_results: list[tuple[str, bool]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    _results.append((name, bool(condition)))
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not condition else ""))


def section(title: str) -> None:
    print(f"\n{title}")


# ---------------------------------------------------------------- fakes
def hit(i: int, page: int = 1, filename: str = "manual.pdf") -> dict:
    return {
        "id": f"{filename}_{page}_{i}",
        "content": f"chunk {i} text",
        "metadata": {
            "source_filename": filename, "page_number": page, "chunk_sequence": i,
            "start_char_index": 0, "end_char_index": 10,
        },
        "similarity_score": 0.9,
    }


class FakeEmbedding:
    def __init__(self, *a, **k): pass
    def embed_text(self, text): return [0.1, 0.2, 0.3]


class FakeStore:
    """Returns queued raw hits; records the kwargs of each query."""
    queue: list[list[dict]] = []
    calls: list[dict] = []
    chunk_queue: list[list[dict]] = []
    chunk_calls: list[dict] = []
    def __init__(self, *a, **k): pass
    def initialize_collection(self): pass
    def get_chunks(self, where):
        FakeStore.chunk_calls.append(where)
        return FakeStore.chunk_queue.pop(0) if FakeStore.chunk_queue else []
    def query_similarity(self, **kwargs):
        FakeStore.calls.append(kwargs)
        return FakeStore.queue.pop(0) if FakeStore.queue else []
    def hybrid_search(self, **kwargs):
        # Same queue and recorded kwargs (query_text included) as query_similarity,
        # so subclasses overriding query_similarity (e.g. BoomStore) still apply
        return self.query_similarity(**kwargs)
    contains: list[dict] = []  # returned by every get_chunks_containing call (substring filter applied)
    contains_calls: list[tuple] = []
    def get_chunks_containing(self, text, where, query_embedding=None):
        FakeStore.contains_calls.append((text, where))
        return [dict(c) for c in FakeStore.contains if text in c["content"]]


class ScriptedLLM:
    """Plays back a list of (tool_name, params) and records what it was sent."""
    script: list = []
    seen: list[dict] = []
    rewrite_reply: object = ""  # returned (or raised) by chat(), used by rewrite_query
    chat_seen: list[list[dict]] = []
    def __init__(self, *a, **k): pass
    def chat(self, messages, temperature=0.0):
        ScriptedLLM.chat_seen.append([dict(m) for m in messages])
        if isinstance(ScriptedLLM.rewrite_reply, Exception):
            raise ScriptedLLM.rewrite_reply
        return ScriptedLLM.rewrite_reply
    def chat_with_tools(self, messages, tools, temperature=0.7):
        ScriptedLLM.seen.append({"messages": [dict(m) for m in messages], "tools": [t["name"] for t in tools],
                                 "temperature": temperature})
        step = ScriptedLLM.script.pop(0)
        if isinstance(step, Exception):
            raise step
        name, params = step
        cid = f"call_{len(ScriptedLLM.seen)}"
        return {
            "tool_name": name, "parameters": params, "tool_call_id": cid,
            "assistant_message": {"role": "assistant", "content": "", "tool_calls": [
                {"id": cid, "type": "function", "function": {"name": name, "arguments": "{}"}}]},
        }


def config() -> IngestionConfig:
    return IngestionConfig(
        embedding_base_url="x", embedding_model="m", embedding_api_key="k",
        llm_base_url="x", llm_model="m", llm_api_key="k",
    )


def install_fakes(script: list, queue: list[list[dict]]) -> None:
    nodes.LLMService = ScriptedLLM
    nodes.EmbeddingService = FakeEmbedding
    nodes.ChromaVectorStore = FakeStore
    ScriptedLLM.script, ScriptedLLM.seen = list(script), []
    ScriptedLLM.rewrite_reply, ScriptedLLM.chat_seen = "", []
    FakeStore.queue, FakeStore.calls = [list(q) for q in queue], []
    FakeStore.chunk_queue, FakeStore.chunk_calls = [], []
    FakeStore.contains, FakeStore.contains_calls = [], []


def initial_state(**overrides) -> QueryState:
    results = [nodes._build_query_result(hit(0)), nodes._build_query_result(hit(1, page=2))]
    state = QueryState(original_query="How do I install it?", query_validated=True,
                       search_results=results, config=config())
    for key, value in overrides.items():
        setattr(state, key, value)
    return state


def run_graph(state: QueryState) -> dict:
    # Drive the real nodes and router the way the graph wires them
    # (generate_response -> [search_more -> generate_response]* -> process_tool_call).
    current = state
    for _ in range(40):
        update = nodes.generate_response(current)
        for k, v in update.items():
            setattr(current, k, v)
        if nodes.route_after_generate(current) == "search":
            for k, v in nodes.search_more(current).items():
                setattr(current, k, v)
            continue
        for k, v in nodes.process_tool_call(current).items():
            setattr(current, k, v)
        break
    return {"state": current}


# ---------------------------------------------------------------- tests
def test_loop_with_search() -> None:
    section("Agent loop: search then answer with steps")
    install_fakes(
        script=[
            ("search_documents", {"query": "mounting bracket", "filename": "manual.pdf", "page_start": 2, "page_end": 4}),
            ("respond_to_user", {"answer": "Install guide", "include_sources": False,
                                 "steps": [{"instruction": "Mount bracket", "source_filename": "manual.pdf", "page": 3},
                                           {"instruction": "Connect wires"}]}),
        ],
        queue=[[hit(1, page=2), hit(2, page=3), hit(3, page=3)]],  # chunk 1 is a duplicate
    )
    state = run_graph(initial_state())["state"]
    check("two LLM turns", len(ScriptedLLM.seen) == 2)
    check("search tool offered on both turns", all("search_documents" in s["tools"] for s in ScriptedLLM.seen))
    check("duplicate chunk skipped, new ones added", len(state.search_results) == 4,
          str(len(state.search_results)))
    check("original refs stable", state.search_results[0].metadata.chunk_sequence == 0
          and state.search_results[1].metadata.chunk_sequence == 1)
    second = ScriptedLLM.seen[1]["messages"]
    check("history has assistant + tool messages",
          [m["role"] for m in second] == ["system", "user", "assistant", "tool"], str([m["role"] for m in second]))
    check("tool message echoes tool_call_id", second[-1]["tool_call_id"] == second[-2]["tool_calls"][0]["id"])
    check("new refs continue after existing (2 and 3)", '"ref": 2' in second[-1]["content"] and '"ref": 3' in second[-1]["content"])
    check("chunk 1 duplicate not re-sent", "chunk 1 text" not in second[-1]["content"])
    check("steps rendered as numbered list",
          "1. Mount bracket (manual.pdf, p.3)" in state.final_response and "2. Connect wires" in state.final_response,
          state.final_response)
    check("status response_generated", state.status == "response_generated", state.status)
    kwargs = FakeStore.calls[0]
    check("where filter has filename and page range", kwargs["where"] == {"$and": [
        {"source_filename": {"$eq": "manual.pdf"}}, {"page_number": {"$gte": 2}}, {"page_number": {"$lte": 4}}]},
          str(kwargs["where"]))
    check("full chunk text in first turn", "chunk 0 text" in ScriptedLLM.seen[0]["messages"][1]["content"])


def test_cap() -> None:
    section("Search cap")
    searches = [("search_documents", {"query": f"q{i}"}) for i in range(3)]
    install_fakes(
        script=searches + [("respond_to_user", {"answer": "done", "include_sources": False})],
        queue=[[hit(10 + i, page=5)] for i in range(3)],
    )
    state = run_graph(initial_state())["state"]
    check("search tool removed after 3 rounds",
          ScriptedLLM.seen[-1]["tools"] == ["respond_to_user", "insufficient_evidence", "ask_clarifying_question"],
          str(ScriptedLLM.seen[-1]["tools"]))
    check("earlier turns still had search", "search_documents" in ScriptedLLM.seen[2]["tools"])
    check("stop hint added to last tool message", "No further searches" in ScriptedLLM.seen[-1]["messages"][-1]["content"])
    check("search_round is 3", state.search_round == 3)
    check("final answer produced", state.final_response == "done", str(state.final_response))


def test_deadline() -> None:
    section("Deadline")
    install_fakes(script=[("respond_to_user", {"answer": "ok"})], queue=[])
    state = initial_state(started_at=time.time() - nodes.AGENT_DEADLINE_SECONDS - 1, search_round=1,
                          messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
                                    {"role": "assistant", "content": "", "tool_calls": []},
                                    {"role": "tool", "tool_call_id": "c", "content": "r"}])
    nodes.generate_response(state)
    check("search tool dropped after deadline", "search_documents" not in ScriptedLLM.seen[0]["tools"])


def test_search_failure_and_empty() -> None:
    section("Search failure / empty results")

    class BoomStore(FakeStore):
        def query_similarity(self, **kwargs):
            raise RuntimeError("chroma down")

    install_fakes(script=[("search_documents", {"query": "x"}), ("respond_to_user", {"answer": "fine", "include_sources": False})], queue=[])
    nodes.ChromaVectorStore = BoomStore
    state = run_graph(initial_state())["state"]
    tool_msg = ScriptedLLM.seen[1]["messages"][-1]["content"]
    check("error passed to LLM, pipeline continues", "failed: chroma down" in tool_msg and state.final_response == "fine", tool_msg)

    install_fakes(script=[("search_documents", {"query": "x"}), ("respond_to_user", {"answer": "fine", "include_sources": False})], queue=[[]])
    state = run_graph(initial_state())["state"]
    check("empty results reported", "No new relevant passages" in ScriptedLLM.seen[1]["messages"][-1]["content"])

    install_fakes(script=[("search_documents", {"query": "  "}), ("respond_to_user", {"answer": "fine", "include_sources": False})], queue=[])
    state = run_graph(initial_state())["state"]
    check("blank query rejected as tool error", "non-empty" in ScriptedLLM.seen[1]["messages"][-1]["content"])


def test_failure_routes_to_fallback() -> None:
    section("LLM failure")
    install_fakes(script=[ValueError("bad response")] * nodes.MAX_TOOL_CALL_RETRIES, queue=[])
    state = run_graph(initial_state())["state"]
    check("fallback response on LLM failure", state.status == "response_generated_with_errors", state.status)


def test_tool_call_reliability() -> None:
    section("Tool-call reliability (retry, salvage, logging)")
    check("temperature matches the documented 0.7", nodes.DEFAULT_LLM_TEMPERATURE == 0.7)

    # retry: plain-text reply, then a tool call
    install_fakes(script=[NoToolCallError("No tool calls", content="<think>hm</think>Here you go: hold reset."),
                          ("respond_to_user", {"answer": "Hold reset 5s", "diagram_refs": []})], queue=[])
    state = run_graph(initial_state())["state"]
    check("retried after a reply without a tool call", len(ScriptedLLM.seen) == 2)
    retry_msgs = ScriptedLLM.seen[1]["messages"]
    check("retry includes the model's text (think stripped) and the nudge",
          retry_msgs[-2] == {"role": "assistant", "content": "Here you go: hold reset."}
          and retry_msgs[-1] == {"role": "user", "content": nodes.TOOL_NUDGE}, str(retry_msgs[-2:]))
    check("nudge not kept in the conversation state",
          all(m.get("content") != nodes.TOOL_NUDGE for m in state.messages))
    check("retried answer used, no errors", state.final_response.startswith("Hold reset 5s") and not state.errors,
          f"{state.final_response!r} {state.errors}")

    # salvage: plain text on every attempt
    install_fakes(script=[NoToolCallError("No tool calls", content="First try"),
                          NoToolCallError("No tool calls", content="<think>x</think>Plain answer")], queue=[])
    state = run_graph(initial_state())["state"]
    check("plain text salvaged as the answer", state.final_response.startswith("Plain answer"), state.final_response)
    check("salvaged answer is not an error", state.status == "response_generated" and not state.errors,
          f"{state.status} {state.errors}")

    # unparseable tool call (LiteLLM HTTP 500): retry with its own nudge and a higher temperature
    malformed = MalformedToolCallError("LLM service returned HTTP 500: Model output contains an unrecoverable tool call")
    install_fakes(script=[malformed, ("respond_to_user", {"answer": "Recovered", "diagram_refs": []})], queue=[])
    state = run_graph(initial_state())["state"]
    check("retried after an unparseable tool call", len(ScriptedLLM.seen) == 2
          and state.final_response.startswith("Recovered") and not state.errors, f"{state.final_response!r} {state.errors}")
    retry = ScriptedLLM.seen[1]
    check("retry adds the malformed-call nudge (not kept in state)",
          retry["messages"][-1] == {"role": "user", "content": nodes.MALFORMED_TOOL_NUDGE}
          and all(m.get("content") != nodes.MALFORMED_TOOL_NUDGE for m in state.messages), str(retry["messages"][-1]))
    check("first attempt at the default temperature, retry higher",
          ScriptedLLM.seen[0]["temperature"] == nodes.DEFAULT_LLM_TEMPERATURE
          and retry["temperature"] == nodes.RETRY_LLM_TEMPERATURE > nodes.DEFAULT_LLM_TEMPERATURE)
    install_fakes(script=[malformed] * nodes.MAX_TOOL_CALL_RETRIES, queue=[])
    state = run_graph(initial_state())["state"]
    check("repeated unparseable calls end in the friendly error",
          state.response["kind"] == "error" and any("unrecoverable tool call" in e for e in state.errors))

    # the client maps a 5xx mentioning a tool call to MalformedToolCallError, other HTTP errors stay ValueError
    from src.query import tools as query_tools
    import requests

    class FakeResp:
        def __init__(self, code, text):
            self.status_code, self.text = code, text
        def raise_for_status(self):
            raise requests.exceptions.HTTPError(f"{self.status_code}")
    saved_post = query_tools.requests.post
    try:
        client = query_tools.LLMService(base_url="x", model="m", api_key="k")
        for code, text, expected in [(500, '{"error": "unrecoverable tool call"}', MalformedToolCallError),
                                     (500, '{"error": "out of memory"}', ValueError),
                                     (400, '{"error": "bad tool call schema"}', ValueError)]:
            query_tools.requests.post = lambda *a, _r=FakeResp(code, text), **k: _r
            try:
                client.chat_with_tools(messages=[], tools=[])
                raised = None
            except Exception as e:  # noqa: BLE001
                raised = e
            check(f"HTTP {code} {text[:30]!r} -> {expected.__name__}",
                  type(raised) is expected, repr(raised))
    finally:
        query_tools.requests.post = saved_post

    # nothing to salvage: empty replies -> error fallback
    install_fakes(script=[NoToolCallError("No tool calls", content="")] * nodes.MAX_TOOL_CALL_RETRIES, queue=[])
    state = run_graph(initial_state())["state"]
    check("empty replies fall back to an error", state.status == "response_generated_with_errors"
          and any("No tool calls" in e for e in state.errors), f"{state.status} {state.errors}")

    # log file: INFO goes to the file even when the console is CRITICAL
    from src.utils.logging import setup_logging, get_logger
    tmp = Path(tempfile.mkdtemp())
    logger = get_logger()
    before = list(logger.handlers)
    logging.disable(logging.NOTSET)
    try:
        setup_logging(level=logging.CRITICAL, log_file=tmp / "logs" / "harvey.log")
        setup_logging(level=logging.CRITICAL, log_file=tmp / "logs" / "harvey.log")  # no duplicate handler
        added = [h for h in logger.handlers if h not in before]
        logger.info("agent detail line")
        for h in added:
            h.flush()
        text = (tmp / "logs" / "harvey.log").read_text()
        check("log file created and receives INFO", "agent detail line" in text, text)
        file_handlers = [h for h in added if isinstance(h, logging.FileHandler)]
        check("only one file handler added for the same path", len(file_handlers) == 1, str(added))
        console = [h for h in logger.handlers if type(h) is logging.StreamHandler]
        check("console handler stays at CRITICAL", all(h.level == logging.CRITICAL for h in console)
              or not console, str([h.level for h in console]))
    finally:
        for h in logger.handlers[:]:
            if h not in before:
                logger.removeHandler(h)
                h.close()
        logging.disable(logging.CRITICAL)
        shutil.rmtree(tmp, ignore_errors=True)


def test_steps_formatting() -> None:
    section("Steps formatting")
    out = nodes._steps_text(nodes._build_steps(
        [{"instruction": "A", "page": 2}, "B", {"nope": 1}, 7, {"instruction": "  "}]))
    check("tolerates malformed entries", out == ["1. A (p.2)", "2. B"], str(out))
    check("non-list ignored", nodes._build_steps("x") == [] and nodes._build_steps(None) == [])


def test_where_builder() -> None:
    section("Where builder")
    check("no filters -> None", nodes._build_where(None, None, None) is None)
    check("single filter unwrapped", nodes._build_where("a.pdf", None, None) == {"source_filename": {"$eq": "a.pdf"}})
    check("bad types ignored", nodes._build_where(5, "2", True) is None)


def test_read_page() -> None:
    section("read_page")
    install_fakes(
        script=[
            ("read_page", {"filename": "manual", "page_start": 2, "page_end": 9}),
            ("respond_to_user", {"answer": "ok", "include_sources": False}),
        ],
        queue=[],
    )
    # page 2 chunk 1 is already in context; chunks 7, 8 are new (reading order)
    FakeStore.chunk_queue = [[hit(1, page=2), hit(7, page=2), hit(8, page=3)]]
    state = run_graph(initial_state())["state"]
    msg = ScriptedLLM.seen[1]["messages"][-1]["content"]
    check("filename resolved from substring", FakeStore.chunk_calls[0]["$and"][0] == {"source_filename": {"$eq": "manual.pdf"}},
          str(FakeStore.chunk_calls))
    check("page range clamped to 3 pages", FakeStore.chunk_calls[0]["$and"][2] == {"page_number": {"$lte": 4}},
          str(FakeStore.chunk_calls))
    check("already-present chunk listed by ref, not repeated", "refs [1]" in msg and "chunk 1 text" not in msg, msg)
    check("new chunks returned with continuing refs", '"ref": 2' in msg and '"ref": 3' in msg and "chunk 7 text" in msg)
    check("counts toward search_round", state.search_round == 1)
    check("results appended", len(state.search_results) == 4)

    install_fakes(script=[("read_page", {"filename": "manual.pdf", "page_start": 2}),
                          ("respond_to_user", {"answer": "ok", "include_sources": False})], queue=[])
    FakeStore.chunk_queue = [[hit(1, page=2)]]
    run_graph(initial_state())
    check("nothing-new reported", "Nothing new" in ScriptedLLM.seen[1]["messages"][-1]["content"])

    install_fakes(script=[("read_page", {"filename": "manual.pdf", "page_start": 99}),
                          ("respond_to_user", {"answer": "ok", "include_sources": False})], queue=[])
    run_graph(initial_state())
    check("empty page explained", "No passages found" in ScriptedLLM.seen[1]["messages"][-1]["content"])

    install_fakes(script=[("read_page", {"filename": "other.pdf", "page_start": 1}),
                          ("respond_to_user", {"answer": "ok", "include_sources": False})], queue=[])
    nodes.ChromaVectorStore = FakeStore
    run_graph(initial_state())
    check("unknown filename lists known ones", "Known filenames: manual.pdf" in ScriptedLLM.seen[1]["messages"][-1]["content"])

    install_fakes(script=[("read_page", {"page_start": "x"}),
                          ("respond_to_user", {"answer": "ok", "include_sources": False})], queue=[])
    run_graph(initial_state())
    check("bad page_start is a tool error", "integer" in ScriptedLLM.seen[1]["messages"][-1]["content"])

    check("read_page offered; removed with cap",
          "read_page" in ScriptedLLM.seen[0]["tools"])


def test_neighbors() -> None:
    section("Neighbor expansion")
    install_fakes(script=[], queue=[])
    hits = [nodes._build_query_result(hit(5, page=2)), nodes._build_query_result(hit(20, page=6))]
    table = hit(20, page=6)
    table["metadata"]["chunk_type"] = "table"
    hits[1] = nodes._build_query_result(table)
    FakeStore.chunk_queue = [[hit(6, page=3), hit(4, page=2), {**hit(7, page=3), "metadata": {**hit(7)["metadata"], "chunk_type": "table"}}]]
    out = nodes._add_neighbors(FakeStore(), hits)
    seqs = [r.metadata.chunk_sequence for r in out]
    check("neighbors added in reading order", seqs == [4, 5, 6, 20], str(seqs))
    check("neighbors flagged, hits not", [r.is_neighbor for r in out] == [True, False, True, False])
    check("non-text neighbors dropped", 7 not in seqs)
    check("table hit not expanded", FakeStore.chunk_calls[0]["$and"][1] == {"chunk_sequence": {"$in": [4, 6]}},
          str(FakeStore.chunk_calls))
    check("neighbor flag shown to LLM", nodes._format_result_for_llm(0, out[0]).get("neighbor") is True)

    class BoomStore(FakeStore):
        def get_chunks(self, where): raise RuntimeError("down")
    out = nodes._add_neighbors(BoomStore(), hits)
    check("failure falls back to direct hits", out == hits)

    install_fakes(script=[], queue=[])
    many = [nodes._build_query_result(hit(i * 10, page=i + 1)) for i in range(8)]
    FakeStore.chunk_queue = [[]]
    nodes._add_neighbors(FakeStore(), many)
    wanted = FakeStore.chunk_calls[0]["$and"][1]["chunk_sequence"]["$in"]
    check("only top 5 hits are expanded", wanted == sorted({s for i in range(5) for s in (i * 10 - 1, i * 10 + 1) if s >= 0}),
          str(wanted))


def test_search_cutoff_env() -> None:
    section("SEARCH_MIN_SIMILARITY (shared by first retrieval and search_documents)")
    def cutoff_used(env):
        os.environ.pop("SEARCH_MIN_SIMILARITY", None)
        if env is not None:
            os.environ["SEARCH_MIN_SIMILARITY"] = env
        install_fakes(script=[("search_documents", {"query": "x"}),
                              ("respond_to_user", {"answer": "ok", "include_sources": False})], queue=[[]])
        run_graph(initial_state())
        return FakeStore.calls[0]["min_similarity_score"]
    def first_retrieval_cutoff(env):
        os.environ.pop("SEARCH_MIN_SIMILARITY", None)
        if env is not None:
            os.environ["SEARCH_MIN_SIMILARITY"] = env
        install_fakes(script=[], queue=[[]])
        nodes.vector_store_search(QueryState(query_embedding=[0.1], config=config()))
        return FakeStore.calls[0]["min_similarity_score"]

    try:
        check("default cutoff is DEFAULT_MIN_SIMILARITY (0.5)",
              cutoff_used(None) == nodes.DEFAULT_MIN_SIMILARITY == 0.5)
        check("env var applied", cutoff_used("0.35") == 0.35)
        check("invalid value falls back to the default", cutoff_used("abc") == 0.5)
        check("first retrieval uses the same default", first_retrieval_cutoff(None) == 0.5)
        check("first retrieval reads the same env var", first_retrieval_cutoff("0.35") == 0.35)
    finally:
        os.environ.pop("SEARCH_MIN_SIMILARITY", None)


def test_chroma_where() -> None:
    section("Chroma where filter (real temp database)")
    tmp = tempfile.mkdtemp()
    try:
        store = ChromaVectorStore(tmp, "test_coll")
        store.initialize_collection()
        chunks = []
        for fname in ("a.pdf", "b.pdf"):
            for page in (1, 2, 3):
                meta = ChunkMetadata(source_filename=fname, page_number=page,
                                     chunk_sequence=len(chunks), start_char_index=0, end_char_index=5,
                                     file_hash="h", ingested_at="t", total_chunks=6)
                chunks.append(TextChunk(content=f"{fname} p{page}", metadata=meta, embedding=[0.1, 0.2, 0.3]))
        store.store_chunks(chunks)
        vec = [0.1, 0.2, 0.3]
        everything = store.query_similarity(vec, k=10)
        only_a = store.query_similarity(vec, k=10, where=nodes._build_where("a.pdf", None, None))
        ranged = store.query_similarity(vec, k=10, where=nodes._build_where("a.pdf", 2, 3))
        nb = store.get_chunks({"$and": [{"source_filename": {"$eq": "b.pdf"}}, {"chunk_sequence": {"$in": [3, 5]}}]})
        check("get_chunks with $in, reading order", [r["metadata"]["chunk_sequence"] for r in nb] == [3, 5], str(nb))
        pages = store.get_chunks({"$and": [{"source_filename": {"$eq": "a.pdf"}}, {"page_number": {"$gte": 2}}, {"page_number": {"$lte": 3}}]})
        check("get_chunks page range", [r["metadata"]["page_number"] for r in pages] == [2, 3])
        try:
            store.get_chunks({})
            check("get_chunks refuses empty filter", False)
        except ValueError:
            check("get_chunks refuses empty filter", True)
        check("unfiltered returns all", len(everything) == 6, str(len(everything)))
        check("filename filter", {r["metadata"]["source_filename"] for r in only_a} == {"a.pdf"} and len(only_a) == 3)
        check("filename + page range", sorted(r["metadata"]["page_number"] for r in ranged) == [2, 3], str(ranged))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_graph_wiring() -> None:
    section("Graph wiring")
    g = query_graph.create_query_graph(config())
    names = set(g.get_graph().nodes)
    check("search_more node present", "search_more" in names, str(names))
    edges = {(e.source, e.target) for e in g.get_graph().edges}
    check("search_more loops back to generate_response", ("search_more", "generate_response") in edges)
    check("generate_response can reach process_tool_call", ("generate_response", "process_tool_call") in edges)
    check("rewrite_query sits between user_query and embed_query",
          ("user_query", "rewrite_query") in edges and ("rewrite_query", "embed_query") in edges)
    check("add_history_documents follows vector_store_search and routes on",
          ("vector_store_search", "add_history_documents") in edges
          and ("add_history_documents", "generate_response") in edges)


def sec_hit(i: int, section: str, page: int = 1, chunk_type: str = "text", text: str | None = None) -> dict:
    """A raw chunk as ingestion now stores it: 'Section:' line in the text + section_path metadata."""
    raw = hit(i, page=page)
    body = text if text is not None else f"chunk {i} text"
    raw["content"] = f"Section: {section}\n{body}" if section else body
    raw["metadata"] = {**raw["metadata"], "section_path": section, "chunk_type": chunk_type}
    if chunk_type == "diagram_reference":
        raw["metadata"].update(diagram_path="extracted_diagrams/manual_p0_fig0.png", diagram_kind="vector_figure")
    return raw


SECTION_DOC = [
    sec_hit(10, "MAINTENANCE > A. Air Filter", page=16, text="Wash the filter every 2 weeks."),
    sec_hit(11, "MAINTENANCE > A. Air Filter", page=16, text="NEVER run without the filter."),
    sec_hit(12, "MAINTENANCE > B. Fan Motor", page=16, text="The motor is factory lubricated."),
    sec_hit(13, "GENERAL INFORMATION > C. Condensation", page=16, text="Keep doors closed."),
    sec_hit(14, "MAINTENANCE > C. Condensation", page=17, text="Drain pan check."),
    sec_hit(15, "MAINTENANCE > B. Fan Motor", page=16, chunk_type="diagram_reference", text="Figure on page 16"),
]


def test_sections() -> None:
    section("Sections (section_path, read_section, neighbors, citations)")
    install_fakes(script=[], queue=[])

    r = nodes._build_query_result(sec_hit(3, "MAINTENANCE > A. Air Filter", text="Wash it."))
    check("section_path parsed into section_headings",
          r.metadata.section_headings == ["MAINTENANCE", "A. Air Filter"], str(r.metadata.section_headings))
    shown = nodes._format_result_for_llm(0, r)
    check("LLM sees a 'section' field", shown.get("section") == "MAINTENANCE > A. Air Filter", str(shown))
    check("duplicate 'Section:' line removed from the text shown", shown["text"] == "Wash it.", repr(shown["text"]))
    old = nodes._format_result_for_llm(0, nodes._build_query_result(hit(1)))
    check("records without a section have no 'section' field", "section" not in old and old["text"] == "chunk 1 text")
    check("system prompt no longer warns about reading order", "out of reading order" not in nodes.SYSTEM_PROMPT)
    check("system prompt explains sections and read_section",
          '"section"' in nodes.SYSTEM_PROMPT and "read_section" in nodes.SYSTEM_PROMPT)

    # neighbors stay in the hit's section
    hits = [nodes._build_query_result(sec_hit(11, "MAINTENANCE > A. Air Filter", page=16))]
    FakeStore.chunk_queue = [[SECTION_DOC[0], SECTION_DOC[2]]]  # seq 10 same section, seq 12 different
    out = nodes._add_neighbors(FakeStore(), hits)
    check("neighbor from the same section added, other section dropped",
          [x.metadata.chunk_sequence for x in out] == [10, 11], str([x.metadata.chunk_sequence for x in out]))
    hits = [nodes._build_query_result(hit(11, page=16))]  # old record: no section
    FakeStore.chunk_queue = [[SECTION_DOC[0], SECTION_DOC[2]]]
    out = nodes._add_neighbors(FakeStore(), hits)
    check("hits without a section still accept any neighbor",
          [x.metadata.chunk_sequence for x in out] == [10, 11, 12], str([x.metadata.chunk_sequence for x in out]))

    # read_section through the agent loop
    def read_section(params: dict, chunks: list[dict] | None = None, state=None):
        install_fakes(script=[("read_section", params),
                              ("respond_to_user", {"answer": "ok", "include_sources": False})], queue=[])
        FakeStore.chunk_queue = [list(SECTION_DOC if chunks is None else chunks)]
        final = run_graph(state or initial_state())["state"]
        return final, ScriptedLLM.seen[1]["messages"][-1]["content"]

    state, msg = read_section({"filename": "manual", "section": "Fan Motor"})
    check("read_section offered as a tool", "read_section" in ScriptedLLM.seen[0]["tools"])
    check("read_section fetches the whole document by filename",
          FakeStore.chunk_calls[0] == {"source_filename": {"$eq": "manual.pdf"}}, str(FakeStore.chunk_calls))
    check("heading without label or path matches its section",
          "section 'MAINTENANCE > B. Fan Motor'" in msg and "factory lubricated" in msg
          and "Wash the filter" not in msg, msg[:300])
    check("diagram chunks of the section are included", '"chunk_type": "diagram_reference"' in msg)
    check("read_section counts toward search_round and appends results",
          state.search_round == 1 and len(state.search_results) == 4, f"{state.search_round} {len(state.search_results)}")

    _, msg = read_section({"filename": "manual.pdf", "section": "maintenance"})
    check("a parent section includes its sub-sections in reading order",
          msg.index("Wash the filter") < msg.index("NEVER run") < msg.index("factory lubricated")
          < msg.index("Drain pan") and "Keep doors closed" not in msg, msg[:400])

    _, msg = read_section({"filename": "manual.pdf", "section": "MAINTENANCE > A. Air Filter"})
    check("full section path matches exactly", "Wash the filter" in msg and "factory lubricated" not in msg)

    _, msg = read_section({"filename": "manual.pdf", "section": "Condensation"})
    check("ambiguous heading lists the candidates",
          "matches several sections" in msg and "GENERAL INFORMATION > C. Condensation" in msg
          and "MAINTENANCE > C. Condensation" in msg, msg)

    _, msg = read_section({"filename": "manual.pdf", "section": "Compressor"})
    check("unknown section lists the document's sections",
          "No section matching" in msg and "- MAINTENANCE > A. Air Filter" in msg, msg)

    _, msg = read_section({"filename": "manual.pdf", "section": "Air Filter"}, chunks=[hit(10), hit(11)])
    check("documents without sections point to read_page", "no section information" in msg, msg)

    existing = initial_state()
    existing.search_results.append(nodes._build_query_result(SECTION_DOC[0]))
    _, msg = read_section({"filename": "manual.pdf", "section": "Air Filter"}, state=existing)
    check("chunks already in context are listed by ref, not repeated",
          "refs [2]" in msg and "Wash the filter" not in msg and "NEVER run" in msg, msg)

    saved = nodes.READ_SECTION_MAX_CHUNKS
    nodes.READ_SECTION_MAX_CHUNKS = 2
    try:
        _, msg = read_section({"filename": "manual.pdf", "section": "MAINTENANCE"})
    finally:
        nodes.READ_SECTION_MAX_CHUNKS = saved
    check("long sections are capped and the rest is named",
          "Showing the first 2" in msg and "'MAINTENANCE > B. Fan Motor'" in msg and "factory lubricated" not in msg, msg)

    _, msg = read_section({"filename": "manual.pdf", "section": " "})
    check("empty section is a tool error", "non-empty 'section'" in msg, msg)

    # citations
    results = [nodes._build_query_result(c) for c in SECTION_DOC]
    steps = nodes._steps_text(nodes._build_steps([
        {"instruction": "Wash the filter", "source_filename": "manual.pdf", "page": 16,
         "section": "MAINTENANCE > A. Air Filter"},
        {"instruction": "Check the drain pan", "source_filename": "manual.pdf", "page": 17},
        {"instruction": "Invented", "source_filename": "manual.pdf", "page": 16, "section": "MADE UP > X"},
    ], results))
    check("step citation shows the section the LLM copied",
          steps[0] == "1. Wash the filter (manual.pdf, p.16, MAINTENANCE > A. Air Filter)", steps[0])
    check("step section derived when its page has a single section",
          steps[1] == "2. Check the drain pan (manual.pdf, p.17, MAINTENANCE > C. Condensation)", steps[1])
    check("invented section is not shown (page 16 has several sections)",
          steps[2] == "3. Invented (manual.pdf, p.16)", steps[2])

    out = nodes.format_response_text(nodes._build_answer_response({
        "answer": "Clean it.",
        "sources": [{"filename": "manual.pdf", "page": 16, "snippet": "The motor is factory lubricated."}],
        "diagram_refs": [5],
    }, results))
    check("source citation finds the section from the snippet",
          "- manual.pdf (page 16, MAINTENANCE > B. Fan Motor): The motor is factory lubricated." in out, out)

    diagram_file = ROOT / "extracted_diagrams" / "manual_p0_fig0.png"
    created = not diagram_file.exists()
    if created:
        diagram_file.parent.mkdir(exist_ok=True)
        diagram_file.write_bytes(b"\x89PNG")
    try:
        lines = [nodes._diagram_line(d) for d in nodes._build_diagrams([5], results)]
    finally:
        if created:
            diagram_file.unlink()
    check("diagram line shows the section",
          bool(lines) and "(page 16, vector_figure, MAINTENANCE > B. Fan Motor):" in lines[0], str(lines))

    out = nodes.format_response_text(nodes._build_insufficient_response({"explanation": "no"}, [results[2]]))
    check("insufficient_evidence shows the section and a clean snippet",
          "(page 16, MAINTENANCE > B. Fan Motor)" in out and "Snippet: The motor is factory lubricated." in out, out)


def past_turn(question: str, answer: str, seqs: list[int], page: int = 1) -> ChatTurn:
    return ChatTurn(question=question, answer=answer,
                    documents=[nodes._build_query_result(hit(s, page=page)) for s in seqs])


def test_conversation() -> None:
    section("Conversation history")
    install_fakes(script=[], queue=[])

    # rewrite_query: no history -> no LLM call, query unchanged
    state = QueryState(original_query="How do I reset it?", query_validated=True, config=config())
    out = nodes.rewrite_query(state)
    check("no history: query used as-is", out["retrieval_query"] == "How do I reset it?", str(out))
    check("no history: no rewrite call", ScriptedLLM.chat_seen == [])

    # rewrite_query: history -> LLM rewrite, cleaned
    history = [past_turn("What is diagnostic mode on the CCC2?", "Diagnostic mode tests the controller.", [0])]
    ScriptedLLM.rewrite_reply = '<think>hmm</think>\nQuery: "How to reset CCC2 diagnostic mode"'
    state = QueryState(original_query="How do I reset it?", query_validated=True, config=config(),
                       chat_history=history)
    out = nodes.rewrite_query(state)
    check("rewrite cleaned of think tags, prefix and quotes",
          out["retrieval_query"] == "How to reset CCC2 diagnostic mode", str(out))
    sent = ScriptedLLM.chat_seen[-1][1]["content"]
    check("rewrite prompt includes earlier Q&A and latest message",
          "What is diagnostic mode" in sent and "Latest message: How do I reset it?" in sent)

    # rewrite_query: failures fall back to original
    ScriptedLLM.rewrite_reply = ConnectionError("down")
    check("rewrite error falls back", nodes.rewrite_query(state)["retrieval_query"] == "How do I reset it?")
    ScriptedLLM.rewrite_reply = "x" * (nodes.REWRITE_MAX_QUERY_CHARS + 1)
    check("over-long rewrite falls back", nodes.rewrite_query(state)["retrieval_query"] == "How do I reset it?")

    # embed_query embeds the rewritten query
    class RecordingEmbedding(FakeEmbedding):
        texts: list[str] = []
        def embed_text(self, text):
            RecordingEmbedding.texts.append(text)
            return [0.1]
    nodes.EmbeddingService = RecordingEmbedding
    nodes.embed_query(QueryState(original_query="it?", retrieval_query="standalone q", query_validated=True,
                                 config=config()))
    check("embed_query uses retrieval_query", RecordingEmbedding.texts == ["standalone q"], str(RecordingEmbedding.texts))
    nodes.EmbeddingService = FakeEmbedding

    # add_history_documents: dedupe, flag, reading order, recent-turns window
    old = past_turn("q1", "a1", [50], page=9)        # outside HISTORY_DOC_TURNS (=2) -> not carried
    mid = past_turn("q2", "a2", [3, 4], page=1)
    new = past_turn("q3", "a3", [1, 7], page=1)       # chunk 1 duplicates this turn's hit
    current = [nodes._build_query_result(hit(1)), nodes._build_query_result(hit(5))]
    state = QueryState(original_query="q4", search_results=current, chat_history=[old, mid, new])
    out = nodes.add_history_documents(state)
    seqs = [r.metadata.chunk_sequence for r in out["search_results"]]
    check("carried docs merged in reading order, duplicate skipped", seqs == [1, 3, 4, 5, 7], str(seqs))
    flags = {r.metadata.chunk_sequence: r.from_history for r in out["search_results"]}
    check("carried docs flagged, current hits not", flags == {1: False, 3: True, 4: True, 5: False, 7: True}, str(flags))
    check("history copies not mutated", not any(d.from_history for d in new.documents))
    check("earlier_turn shown to LLM",
          nodes._format_result_for_llm(0, out["search_results"][1]).get("earlier_turn") is True)
    out = nodes.add_history_documents(QueryState(original_query="q", search_results=[], chat_history=[new]))
    check("history docs alone still allow an answer",
          len(out["search_results"]) == 2 and out["status"] == "history_documents_only", str(out))
    check("no history: no change", nodes.add_history_documents(QueryState(search_results=current)) == {})

    # generate_response: earlier turns between system prompt and current question
    install_fakes(script=[("respond_to_user", {"answer": "Hold reset 5s", "include_sources": False})], queue=[])
    turns = [past_turn(f"q{i}", f"a{i}", [i]) for i in range(nodes.MAX_HISTORY_TURNS + 2)]
    state = run_graph(initial_state(chat_history=turns))["state"]
    sent = ScriptedLLM.seen[0]["messages"]
    roles = [m["role"] for m in sent]
    expected = ["system"] + ["user", "assistant", "tool"] * nodes.MAX_HISTORY_TURNS + ["user"]
    check("history capped at MAX_HISTORY_TURNS as user / tool call / tool result", roles == expected, str(roles))
    newest_args = json.loads(sent[-3]["tool_calls"][0]["function"]["arguments"])
    check("oldest turns dropped, newest kept", sent[1]["content"] == "q2" and newest_args["answer"] == f"a{len(turns) - 1}",
          f"{sent[1]['content']} / {newest_args}")
    check("current question last, with documents", "How do I install it?" in sent[-1]["content"]
          and "chunk 0 text" in sent[-1]["content"])
    check("answer produced", state.final_response.startswith("Hold reset 5s"), state.final_response)


def diagram_hit(i: int, page: int, path: str, filename: str = "manual.pdf") -> dict:
    raw = hit(i, page=page, filename=filename)
    raw["metadata"].update(chunk_type=nodes.DIAGRAM_CHUNK_TYPE, diagram_path=path, diagram_kind="figure")
    return raw


def test_cited_documents() -> None:
    section("Only cited documents carry into the next turn")
    real_image = next((nodes.PROJECT_ROOT / nodes.DIAGRAM_DIR_NAME).glob("*.png"), None)
    check("an extracted diagram exists for this test", real_image is not None)
    if real_image is None:
        return

    results = [
        nodes._build_query_result(hit(0, page=1)),                          # ref 0: cited via page 1
        nodes._build_query_result(hit(1, page=1)),                          # ref 1: same page -> also cited
        nodes._build_query_result(hit(2, page=2)),                          # ref 2: not cited
        nodes._build_query_result(hit(3, page=3, filename="other.pdf")),   # ref 3: cited as "OTHER" + "3"
        nodes._build_query_result(diagram_hit(4, 5, str(real_image))),     # ref 4: shown diagram
        nodes._build_query_result(diagram_hit(5, 6, "missing.png")),       # ref 5: diagram file missing
        nodes._build_query_result(hit(6, page=4)),                          # ref 6: text, listed in diagram_refs
    ]
    results[1] = replace(results[1], from_history=True)
    params = {
        "answer": "x",
        "sources": [{"filename": "manual.pdf", "page": 1}, {"filename": "OTHER", "page": "3"},
                    {"filename": "manual.pdf"}, "junk", {"filename": "manual.pdf", "page": 99}],
        "diagram_refs": [4, 5, 6, 42, True],
    }
    cited = nodes._cited_documents(params, results)
    keys = [(r.metadata.source_filename, r.metadata.chunk_sequence) for r in cited]
    check("page matches, lenient filename, shown diagram only",
          keys == [("manual.pdf", 0), ("manual.pdf", 1), ("manual.pdf", 4), ("other.pdf", 3)], str(keys))
    check("from_history cleared on cited copies", not any(r.from_history for r in cited))
    check("original results untouched", results[1].from_history is True)
    check("no sources -> nothing", nodes._cited_documents({"answer": "x"}, results) == [])

    # Through process_tool_call: respond_to_user sets source_documents, insufficient_evidence does not
    state = QueryState(original_query="q", search_results=results, config=config(),
                       tool_call=ToolCall("respond_to_user", params))
    out = nodes.process_tool_call(state)
    check("respond_to_user returns source_documents", len(out["source_documents"]) == 4, str(len(out["source_documents"])))
    state.tool_call = ToolCall("insufficient_evidence", {"explanation": "no"})
    check("insufficient_evidence carries nothing", nodes.process_tool_call(state)["source_documents"] == [])

    # Next turn: only the cited documents come back as earlier_turn
    turn = ChatTurn(question="q", answer="a", documents=out["source_documents"])
    carried = nodes.add_history_documents(QueryState(original_query="q2", search_results=[], chat_history=[turn]))
    seqs = sorted(r.metadata.chunk_sequence for r in carried["search_results"])
    check("next turn receives only cited chunks", seqs == [0, 1, 3, 4], str(seqs))


def test_diagram_priority() -> None:
    section("Diagrams prioritised in answers")
    install_fakes(script=[], queue=[])

    # A. prompt + schema
    prompt = nodes.SYSTEM_PROMPT
    check("prompt asks for diagrams in every answer", "In EVERY respond_to_user call" in prompt)
    check("prompt no longer limits diagrams to explicit requests",
          "When the question asks to see, find or show a diagram" not in prompt)
    check("prompt forbids paths / image markdown in text", "Never write file paths, image markdown" in prompt)
    respond = next(t for t in nodes.TOOLS if t["name"] == "respond_to_user")
    check("diagram_refs is required", "diagram_refs" in respond["parameters"]["required"])
    shown = nodes._format_result_for_llm(0, nodes._build_query_result(diagram_hit(1, 5, "x/y.png")))
    check("LLM no longer sees diagram_path", "diagram_path" not in shown and shown["diagram_kind"] == "figure", str(shown))

    # B. diagrams on the hits' pages
    sec = "INSTALL > A. Ceiling Template"
    hits = [nodes._build_query_result(sec_hit(20, sec, page=9)),
            nodes._build_query_result(sec_hit(30, "INSTALL > B. Divider", page=10))]
    same = sec_hit(25, sec, page=9, chunk_type="diagram_reference")
    other = sec_hit(21, "INSTALL > Z. Other", page=9, chunk_type="diagram_reference")
    present = sec_hit(31, "INSTALL > B. Divider", page=10, chunk_type="diagram_reference")
    text = sec_hit(22, sec, page=9)
    FakeStore.chunk_queue = [[other, same, present, text]]
    have = {nodes._chunk_key(nodes._build_query_result(present))}
    out = nodes._page_diagrams(FakeStore(), hits, have)
    check("where filter: file + diagram type + hit pages", FakeStore.chunk_calls[-1] == {"$and": [
        {"source_filename": {"$eq": "manual.pdf"}},
        {"chunk_type": {"$eq": nodes.DIAGRAM_CHUNK_TYPE}},
        {"page_number": {"$in": [9, 10]}}]}, str(FakeStore.chunk_calls[-1]))
    seqs = [r.metadata.chunk_sequence for r in out]
    check("same-section diagram first, present/text skipped", seqs == [25, 21], str(seqs))
    check("page diagrams marked as neighbor context", all(r.is_neighbor for r in out))
    saved = nodes.PAGE_DIAGRAM_MAX
    nodes.PAGE_DIAGRAM_MAX = 1
    try:
        FakeStore.chunk_queue = [[other, same]]
        capped = nodes._page_diagrams(FakeStore(), hits, set())
    finally:
        nodes.PAGE_DIAGRAM_MAX = saved
    check("PAGE_DIAGRAM_MAX caps the additions", [r.metadata.chunk_sequence for r in capped] == [25])

    class BoomStore(FakeStore):
        def get_chunks(self, where): raise RuntimeError("down")
    check("lookup failure returns no diagrams", nodes._page_diagrams(BoomStore(), hits, set()) == [])

    # initial retrieval: neighbors call first, then page diagrams; merged in reading order
    install_fakes(script=[], queue=[[sec_hit(20, sec, page=9)]])
    FakeStore.chunk_queue = [[], [same]]
    out = nodes.vector_store_search(QueryState(query_embedding=[0.1], config=config()))
    seqs = [r.metadata.chunk_sequence for r in out["search_results"]]
    check("vector_store_search adds diagrams from the hits' pages", seqs == [20, 25], str(seqs))

    # search_documents results get page diagrams too
    install_fakes(script=[("search_documents", {"query": "ceiling template"}),
                          ("respond_to_user", {"answer": "ok", "include_sources": False, "diagram_refs": []})],
                  queue=[[sec_hit(20, sec, page=9)]])
    FakeStore.chunk_queue = [[same]]
    state = run_graph(initial_state())["state"]
    msg = ScriptedLLM.seen[1]["messages"][-1]["content"]
    check("search_documents returns page diagrams with the new passages",
          '"chunk_type": "diagram_reference"' in msg and len(state.search_results) == 4, msg[:300])

    # cap on images per answer, shared by display and carry-over
    real_image = next((nodes.PROJECT_ROOT / nodes.DIAGRAM_DIR_NAME).glob("*.png"), None)
    if real_image is None:
        check("an extracted diagram exists for the cap test", False)
        return
    results = [nodes._build_query_result(diagram_hit(i, page=i + 1, path=str(real_image))) for i in range(6)]
    refs = [5, 0, 5, 3, 1, 2, 4]
    check("shown refs capped at MAX_DIAGRAMS_PER_ANSWER in LLM order",
          nodes._shown_diagram_refs(refs, results) == [5, 0, 3, 1], str(nodes._shown_diagram_refs(refs, results)))
    shown = nodes._build_diagrams(refs, results)
    check("only 4 diagrams rendered", len(shown) == nodes.MAX_DIAGRAMS_PER_ANSWER, str(len(shown)))
    cited = nodes._cited_documents({"diagram_refs": refs}, results)
    check("carry-over keeps only the shown diagrams",
          sorted(r.metadata.chunk_sequence for r in cited) == [0, 1, 3, 5])


def test_structured_response() -> None:
    section("Structured response (steps, sources, diagrams, kinds)")
    real_image = next((nodes.PROJECT_ROOT / nodes.DIAGRAM_DIR_NAME).glob("*.png"), None)
    check("an extracted diagram exists for this test", real_image is not None)
    if real_image is None:
        return
    img = str(real_image)
    sec_a, sec_b, sec_c = "INSTALL > A. Ceiling Template", "INSTALL > B. Divider", "INSTALL > C. Control Box"

    def dia(i, page, sec):
        raw = sec_hit(i, sec, page=page, chunk_type="diagram_reference")
        raw["metadata"]["diagram_path"] = img
        return nodes._build_query_result(raw)

    results = [
        nodes._build_query_result(sec_hit(0, sec_a, page=9, text="Hold the template up to the opening.")),
        dia(1, 9, sec_b), dia(2, 9, sec_a), dia(3, 9, sec_b), dia(4, 9, sec_a), dia(5, 9, sec_b),
        dia(6, 11, sec_a),                                              # page 11 is not cited
        nodes._build_query_result(sec_hit(7, sec_c, page=10, text="Measure the thickness.")),
    ]
    long_snippet = "Hold the template up to the opening.\\nBe sure the solid end faces the rear. " + "word " * 80
    params = {
        "answer": "  Install it like this.  ",
        "steps": [
            {"instruction": "Hold template", "stage": "Ceiling template", "source_filename": "manual.pdf", "page": 9},
            {"instruction": "Start bolts", "stage": "Ceiling template"},
            {"instruction": "Measure thickness", "stage": "Divider plate", "page": 10},
            {"nope": 1},
        ],
        "sources": [
            {"filename": "manual.pdf", "page": 9, "section": sec_a, "snippet": long_snippet},
            {"filename": "manual.pdf", "page": "9", "section": sec_a, "snippet": "duplicate"},
            {"filename": "manual.pdf", "page": 10, "snippet": "Measure the thickness."},
        ],
        "include_sources": False,
        "diagram_refs": [],
    }
    state = QueryState(original_query="q", search_results=results, config=config(),
                       tool_call=ToolCall("respond_to_user", params))
    out = nodes.process_tool_call(state)
    resp = out["response"]
    check("answer kind, trimmed answer", resp["kind"] == "answer" and resp["answer"] == "Install it like this.")
    check("steps carry stage and citation",
          resp["steps"][0] == {"stage": "Ceiling template", "instruction": "Hold template",
                               "citation": "manual.pdf, p.9"}  # page 9 has 2 sections: none guessed
          and resp["steps"][2]["citation"] == f"p.10, {sec_c}"
          and len(resp["steps"]) == 3, str(resp["steps"]))
    text = out["final_response"]
    check("text groups steps by stage with per-stage numbering",
          "Ceiling template:\n1. Hold template" in text and "2. Start bolts" in text
          and "\n\nDivider plate:\n1. Measure thickness" in text, text)
    check("built steps keep document order across stages",
          nodes._build_steps(params["steps"], results)[2]["instruction"].startswith("Measure thickness"))
    srcs = resp["sources"]
    check("duplicate (file, page, section) source dropped", len(srcs) == 2, str(srcs))
    check("snippet cleaned and capped", len(srcs[0]["snippet"]) <= nodes.SNIPPET_MAX_CHARS + 1
          and srcs[0]["snippet"].endswith("…") and "\\n" not in srcs[0]["snippet"], srcs[0]["snippet"])
    check("include_sources=false no longer hides sources", "Sources:" in text)

    # D1 fallback: no diagram chosen -> same-section diagrams on cited pages first, capped
    refs = [results.index(r) for r in results if r.metadata.chunk_type == nodes.DIAGRAM_CHUNK_TYPE]
    seqs = [d["path"] == img for d in resp["diagrams"]]
    got = [nodes._section_of(results[i]) for i in nodes._fallback_diagram_refs(params, results)]
    check("fallback attaches up to MAX_DIAGRAMS_PER_ANSWER", len(resp["diagrams"]) == nodes.MAX_DIAGRAMS_PER_ANSWER
          and all(seqs), str(resp["diagrams"]))
    check("fallback prefers the cited section", got == [sec_a, sec_a, sec_b, sec_b], str(got))
    fb_pages = {d["page"] for d in resp["diagrams"]}
    check("fallback only uses cited pages", fb_pages == {9}, str(fb_pages))
    carried = {r.metadata.chunk_sequence for r in out["source_documents"]
               if r.metadata.chunk_type == nodes.DIAGRAM_CHUNK_TYPE}
    shown_seqs = {results[i].metadata.chunk_sequence for i in nodes._fallback_diagram_refs(params, results)}
    check("only the shown diagrams carry over (not every diagram on the page)", carried == shown_seqs,
          f"{carried} vs {shown_seqs}")
    chosen = nodes._with_fallback_diagrams({**params, "diagram_refs": [6]}, results)
    check("no fallback when the LLM chose a valid diagram", chosen["diagram_refs"] == [6])
    check("no fallback without sources", nodes._fallback_diagram_refs({"diagram_refs": []}, results) == [])
    check("no empty Sources heading", "Sources:" not in nodes.format_response_text(
        nodes._build_answer_response({"answer": "a"}, results)))

    # other kinds
    state.tool_call = ToolCall("insufficient_evidence", {"explanation": "Not covered."})
    out = nodes.process_tool_call(state)
    check("insufficient kind with one best source",
          out["response"]["kind"] == "insufficient" and len(out["response"]["sources"]) == 1
          and out["response"]["answer"] == "Not covered.", str(out["response"]))
    state.tool_call = ToolCall("ask_clarifying_question",
                               {"question": "Which model?", "options": [" 600 series ", "", 7, "579", "590", "A", "B"]})
    out = nodes.process_tool_call(state)
    check("clarify kind, options cleaned and capped",
          out["response"]["kind"] == "clarify" and out["response"]["options"] == ["600 series", "579", "590", "A"]
          and out["status"] == "clarification_requested", str(out["response"]))
    check("clarify text lists options", "Which model?" in out["final_response"] and "- 579" in out["final_response"])
    state.tool_call = None
    out = nodes.process_tool_call(state)
    check("missing tool call -> friendly error, details in errors",
          out["response"]["kind"] == "error" and out["final_response"] == nodes.ERROR_MESSAGE
          and any("No tool call" in e for e in out["errors"]), str(out))
    state.tool_call = ToolCall("made_up_tool", {})
    out = nodes.process_tool_call(state)
    check("unknown tool -> error kind", out["response"]["kind"] == "error" and out["status"] == "response_generated_with_errors")


def test_history_hygiene() -> None:
    section("History as tool calls, clean text, failed turns not remembered")
    turns = [ChatTurn("How do I install it?", "Mount it.", kind="answer"),
             ChatTurn("Compressor specs?", "Not covered.", kind="insufficient"),
             ChatTurn("Show wiring", "Which model?\nOptions: 600; 579", kind="clarify")]
    msgs = nodes._history_messages(turns)
    check("three messages per turn", [m["role"] for m in msgs] == ["user", "assistant", "tool"] * 3,
          str([m["role"] for m in msgs]))
    calls = [m["tool_calls"][0] for m in msgs if m["role"] == "assistant"]
    check("each kind replayed as its own tool",
          [c["function"]["name"] for c in calls] == ["respond_to_user", "insufficient_evidence", "ask_clarifying_question"])
    args = [json.loads(c["function"]["arguments"]) for c in calls]
    check("arguments carry the clean text (and empty diagram_refs for answers)",
          args[0] == {"answer": "Mount it.", "diagram_refs": []} and args[1] == {"explanation": "Not covered."}
          and args[2] == {"question": "Which model?\nOptions: 600; 579"}, str(args))
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    check("tool results answer their tool call ids",
          [t["tool_call_id"] for t in tool_msgs] == [c["id"] for c in calls] and len({c["id"] for c in calls}) == 3)
    check("no plain-text assistant answers in history",
          all(m.get("tool_calls") for m in msgs if m["role"] == "assistant"))

    # clean history text: answer + steps (+ options), no sources or paths
    response = {"kind": "answer", "answer": "Do this.",
                "steps": [{"stage": "", "instruction": "Step one", "citation": "manual.pdf, p.3"}],
                "sources": [{"filename": "manual.pdf", "page": 3, "section": "", "snippet": "secret snippet"}],
                "diagrams": [{"filename": "manual.pdf", "page": 3, "kind": "", "section": "", "path": "/abs/x.png"}],
                "options": []}
    text = nodes.response_history_text(response)
    check("history text keeps answer and steps", "Do this." in text and "1. Step one (manual.pdf, p.3)" in text, text)
    check("history text drops sources and image paths",
          "secret snippet" not in text and "/abs/x.png" not in text and "Sources" not in text, text)

    # chat_turn_from_result
    doc = nodes._build_query_result(hit(4))
    ok = QueryState(original_query="q", response=response, source_documents=[doc])
    turn = query_graph.chat_turn_from_result("q", ok)
    check("answered turn becomes a ChatTurn with clean text and cited docs",
          turn is not None and turn.answer == text and turn.kind == "answer" and turn.documents == [doc])
    failed = QueryState(original_query="q", response=nodes._error_response(), errors=["boom"])
    check("error turn is not remembered", query_graph.chat_turn_from_result("q", failed) is None)
    check("turn without a response (no documents found) is not remembered",
          query_graph.chat_turn_from_result("q", QueryState(original_query="q")) is None)
    clarify = QueryState(original_query="q", response=nodes._build_clarify_response(
        {"question": "Which model?", "options": ["600", "579"]}))
    turn = query_graph.chat_turn_from_result("wiring?", clarify)
    check("clarify turn remembered with its options",
          turn.kind == "clarify" and turn.answer == "Which model?\nOptions: 600; 579", turn.answer)


def test_prompt_and_tools() -> None:
    section("Prompt rewrite and tool schema")
    prompt = nodes.SYSTEM_PROMPT
    for heading in ("## Output", "## Documents", "## Conversation", "## Answering", "## Procedures",
                    "## Diagrams", "## Gathering more context", "## Finishing"):
        check(f"prompt has section {heading!r}", heading in prompt)
    check("prompt: never plain text", "Never reply with plain text" in prompt)
    check("prompt: whole-process questions cover every stage", "covers every top-level stage" in prompt)
    check("prompt: stage required on every step of multi-stage procedures", 'EVERY step must have "stage" set' in prompt)
    check("prompt: whole-process answers are an overview with an offer",
          "2-4 most important concrete steps" in prompt and "offering the complete steps for any stage" in prompt)
    check("prompt: steps never repeated in answer", "Never list the stages or steps in \"answer\"" in prompt)
    check("prompt: steps are concrete actions", "not just the name of a stage" in prompt)
    check("prompt: full steps of one stage on request", "full steps of one stage" in prompt)
    check("prompt: clarifying question only when answers differ", "materially different answers" in prompt)
    check("prompt: insufficient_evidence only after searching", "after you have searched" in prompt)
    check("prompt: include_sources gone", "include_sources" not in prompt)

    tools = {t["name"]: t for t in nodes.TOOLS}
    respond = tools["respond_to_user"]["parameters"]
    check("include_sources removed from respond_to_user", "include_sources" not in respond["properties"])
    check("steps have an optional stage", "stage" in respond["properties"]["steps"]["items"]["properties"]
          and respond["properties"]["steps"]["items"]["required"] == ["instruction"])
    check("snippet asks for a short quote", "one sentence" in respond["properties"]["sources"]["items"]["properties"]["snippet"]["description"])
    check("insufficient_evidence has no redundant query param",
          list(tools["insufficient_evidence"]["parameters"]["properties"]) == ["explanation"])
    clarify = tools.get("ask_clarifying_question")
    check("ask_clarifying_question tool defined", clarify is not None
          and clarify["parameters"]["required"] == ["question"] and "options" in clarify["parameters"]["properties"])
    check("ask_clarifying_question is a final tool (not in the agent loop)",
          "ask_clarifying_question" not in nodes.AGENT_LOOP_TOOLS)

    # clarifying question through the loop
    install_fakes(script=[("ask_clarifying_question", {"question": "Which series?", "options": ["600", "579"]})], queue=[])
    state = run_graph(initial_state())["state"]
    check("clarifying question ends the turn as kind clarify",
          state.response["kind"] == "clarify" and state.status == "clarification_requested"
          and len(ScriptedLLM.seen) == 1, f"{state.status} {state.response}")

    # D2 diagram menu + E4 compact JSON in the first message
    real_image = next((nodes.PROJECT_ROOT / nodes.DIAGRAM_DIR_NAME).glob("*.png"), None)
    if real_image is None:
        check("an extracted diagram exists for the menu test", False)
        return
    results = [nodes._build_query_result(hit(0, page=9)),
               nodes._build_query_result(diagram_hit(1, 9, str(real_image))),
               nodes._build_query_result(diagram_hit(2, 10, "missing.png"))]
    menu = nodes._diagram_menu(results)
    check("menu lists showable diagrams with ref and page",
          menu.startswith("Diagrams available") and "- ref 1: manual.pdf p.9, figure" in menu, menu)
    check("menu skips diagrams without an image file", "ref 2" not in menu, menu)
    check("no menu without diagrams", nodes._diagram_menu(results[:1]) == "")

    install_fakes(script=[("respond_to_user", {"answer": "ok", "diagram_refs": [1]})], queue=[])
    run_graph(initial_state(search_results=results))
    first = ScriptedLLM.seen[0]["messages"][-1]["content"]
    check("first message ends with the diagram menu", first.rstrip().endswith("- ref 1: manual.pdf p.9, figure"), first[-200:])
    check("documents sent as compact JSON", '[{"ref": 0' in first and '\n  "ref"' not in first, first[:200])
    install_fakes(script=[("respond_to_user", {"answer": "ok", "diagram_refs": []})], queue=[])
    run_graph(initial_state())
    check("no menu when there are no diagrams", "Diagrams available" not in ScriptedLLM.seen[0]["messages"][-1]["content"])


def test_part_numbers() -> None:
    section("Part-number extraction (src/utils/part_numbers.py)")
    from src.utils.part_numbers import (
        extract_part_numbers, filename_part_keys, join_keys, normalize_part, part_family, part_keys, split_keys,
    )
    text = ("Dometic 457915.70X and 640312C85X, Coleman 8330-733 and 3106481.009, MACH-1, R-22, "
            "CCC2 error E1. Not: 3.5 120 63.0 2-86 561-566 12VDC 115VAC 24V 5/16 6-3/4 9-Pin pk/2 1-pc 7.5-Mfd/370V")
    found = extract_part_numbers(text)
    check("codes kept", found == ["457915.70X", "640312C85X", "8330-733", "3106481.009", "MACH-1", "R-22", "CCC2", "E1"],
          str(found))
    check("lowercase query codes found", extract_part_numbers("what is e1 on the ccc2?") == ["E1", "CCC2"])
    check("separator variants normalize alike",
          normalize_part("457915.70X") == normalize_part("457915-70x") == "45791570X")
    check("family of a variant", part_family("457915.70X") == "457915" and part_family("457915") == "")
    check("part_keys adds the family", part_keys("model 457915.70X") == ["45791570X", "457915"])
    check("filename codes", filename_part_keys("CCC2-Operating-Instructions.pdf") == ["CCC2"]
          and filename_part_keys("Dometic 457915 Installation & Operating Instructions Manual1.pdf") == ["457915"],
          str(filename_part_keys("CCC2-Operating-Instructions.pdf")))
    check("stored string round-trips", split_keys(join_keys(["A1", "457915"])) == ["A1", "457915"]
          and join_keys([]) == "" and split_keys(None) == [])
    check("off-topic text has no codes", extract_part_numbers("best sourdough recipe at high altitude") == [])


def test_hybrid_search() -> None:
    section("Hybrid search (real temp database)")
    import src.query.lexical as lexical
    from src.utils.part_numbers import part_keys
    tmp = tempfile.mkdtemp()
    try:
        store = ChromaVectorStore(tmp, "hybrid_coll")
        store.initialize_collection()
        docs = [
            # (file, text, embedding): realistic gap like the live 8330-733 query -
            # the exact-part chunk is below the cutoff (cosine ~0.45), another model scores ~0.8
            ("a.pdf", "Dometic 457915.70X specifications: running amps 12.5", [0.45, 0.89, 0.0, 0.0]),
            ("b.pdf", "Dometic 640312.80X specifications: running amps 13.0", [0.8, 0.6, 0.0, 0.0]),
            ("b.pdf", "Clean the return air filter every two weeks", [0.0, 0.0, 1.0, 0.0]),
        ]
        chunks = [
            TextChunk(content=text, embedding=emb, metadata=ChunkMetadata(
                source_filename=fname, page_number=1, chunk_sequence=i, file_hash="h", ingested_at="t",
                total_chunks=len(docs), part_numbers=part_keys(text)))
            for i, (fname, text, emb) in enumerate(docs)
        ]
        store.store_chunks(chunks)
        query, vec = "running amps for the 457915.70X", [1.0, 0.0, 0.0, 0.0]

        results = store.hybrid_search(query, vec, k=5, min_similarity_score=0.5)
        top = results[0] if results else {}
        check("exact part chunk ranked first despite low cosine",
              top.get("metadata", {}).get("source_filename") == "a.pdf" and top.get("dense_score", 1) < 0.5, str(results))
        check("exact hit labelled with the query's part number",
              top.get("match_type") == "exact_part" and top.get("matched_parts") == ["457915.70X"], str(top))
        check("semantically close other model still returned",
              any(r["metadata"]["source_filename"] == "b.pdf" and r["dense_score"] > 0.7 for r in results))
        check("unrelated chunk dropped by the cutoff", not any("air filter" in r["content"] for r in results))
        check("breakdown fields present", all({"dense_score", "bm25_score", "match_type"} <= r.keys() for r in results))

        check("off-topic query returns nothing",
              store.hybrid_search("sourdough bread recipe", [0.0, 0.0, 0.0, 1.0], k=5, min_similarity_score=0.5) == [])

        filtered = store.hybrid_search(query, vec, k=5, min_similarity_score=0.5,
                                       where=nodes._build_where("b.pdf", None, None))
        check("where filter applies to lexical hits",
              filtered and all(r["metadata"]["source_filename"] == "b.pdf" for r in filtered), str(filtered))

        broad = store.hybrid_search(query, vec, k=5, min_similarity_score=0.5, specific_part_max=0)
        check("broad keys give no exact_part bypass", not any(r["match_type"] == "exact_part" for r in broad), str(broad))

        guaranteed = store.hybrid_search(query, vec, k=1, min_similarity_score=0.5, part_boost=0.0)
        check("specific part always represented beyond k",
              len(guaranteed) == 2 and guaranteed[0]["metadata"]["source_filename"] == "b.pdf"
              and guaranteed[1]["matched_parts"] == ["457915.70X"], str(guaranteed))

        real_get = lexical.get_lexical_index
        lexical.get_lexical_index = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("index broken"))
        try:
            fallback = store.hybrid_search(query, vec, k=5, min_similarity_score=0.5)
        finally:
            lexical.get_lexical_index = real_get
        check("falls back to dense only when the lexical index fails",
              fallback and all(r["match_type"] == "dense" for r in fallback)
              and not any(r["metadata"]["source_filename"] == "a.pdf" for r in fallback), str(fallback))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_hybrid_wiring() -> None:
    section("Hybrid search wiring (nodes)")
    install_fakes(script=[], queue=[[]])
    nodes.vector_store_search(QueryState(query_embedding=[0.1], config=config(),
                                         original_query="What does E1 mean?"))
    kwargs = FakeStore.calls[0]
    check("first retrieval sends the query text", kwargs.get("query_text") == "What does E1 mean?", str(kwargs))
    check("default hybrid settings passed",
          (kwargs.get("alpha"), kwargs.get("candidates"), kwargs.get("part_boost"))
          == (nodes.HYBRID_ALPHA, nodes.HYBRID_CANDIDATES, nodes.PART_BOOST), str(kwargs))

    install_fakes(script=[], queue=[[]])
    nodes.vector_store_search(QueryState(query_embedding=[0.1], config=config(), original_query="E1",
                                         retrieval_query="What does error E1 mean on the CCC2?"))
    check("rewritten follow-up used as the search text",
          FakeStore.calls[0]["query_text"] == "What does error E1 mean on the CCC2?")

    os.environ.update(HYBRID_ALPHA="0.8", HYBRID_CANDIDATES="12", PART_BOOST="oops")
    try:
        install_fakes(script=[], queue=[[]])
        nodes.vector_store_search(QueryState(query_embedding=[0.1], config=config(), original_query="q"))
        kwargs = FakeStore.calls[0]
        check("env overrides applied (int stays int)", kwargs["alpha"] == 0.8 and kwargs["candidates"] == 12
              and isinstance(kwargs["candidates"], int), str(kwargs))
        check("invalid env value falls back", kwargs["part_boost"] == nodes.PART_BOOST)
    finally:
        for name in ("HYBRID_ALPHA", "HYBRID_CANDIDATES", "PART_BOOST"):
            os.environ.pop(name, None)

    exact = nodes._build_query_result({**hit(0), "match_type": "exact_part", "matched_parts": ["457915.70X"],
                                       "dense_score": 0.4, "bm25_score": 9.0})
    plain = nodes._build_query_result(hit(1))
    check("QueryResult keeps the breakdown", exact.match_type == "exact_part" and exact.dense_score == 0.4
          and plain.match_type == "" and plain.matched_parts == [])
    check("LLM sees matched_part only on exact hits",
          nodes._format_result_for_llm(0, exact).get("matched_part") == "457915.70X"
          and "matched_part" not in nodes._format_result_for_llm(1, plain))
    check("prompt explains matched_part", '"matched_part"' in nodes.SYSTEM_PROMPT)


def code_hit(i: int, text: str, page: int = 1, filename: str = "manual.pdf", score: float = 0.0) -> dict:
    raw = hit(i, page=page, filename=filename)
    raw.update(content=text, similarity_score=score)
    return raw


def test_find_code() -> None:
    section("Exact-code lookup: automatic pass and find_code tool")
    check("short codes found, long part numbers and model names not",
          nodes._short_codes("What does error code E1 mean on the CCC2?") == ["E1"]
          and nodes._short_codes("Specs for the 457915.70X") == [], str(nodes._short_codes("E1 on the CCC2")))
    check("whole-token, any-case match", nodes._has_code("Error e1: loss", "E1")
          and not nodes._has_code("Code E10 reserved", "E1") and not nodes._has_code("E1A fault", "E1"))

    # Real temporary database: Chroma's substring filter, then whole-token check and ranking
    tmp = tempfile.mkdtemp()
    try:
        store = ChromaVectorStore(tmp, "code_coll")
        store.initialize_collection()
        docs = [  # (file, page, text, embedding)
            ("ccc2.pdf", 2, "E1 Loss of communications. Two power module DIP switches set as same zone.", [1.0, 0.0, 0.0, 0.0]),
            ("ccc2.pdf", 3, "Command 10 shows outdoor temperature; E10 is reserved.", [0.9, 0.1, 0.0, 0.0]),
            ("service.pdf", 36, "e1 loss of communication causes the relay control module to shut down.", [0.2, 0.98, 0.0, 0.0]),
            ("service.pdf", 40, "General wiring information.", [0.0, 1.0, 0.0, 0.0]),
            ("other.pdf", 1, "E1 ignition fault on the water heater.", [0.0, 0.0, 1.0, 0.0]),
        ]
        store.store_chunks([
            TextChunk(content=text, embedding=emb, metadata=ChunkMetadata(
                source_filename=f, page_number=p, chunk_sequence=i, file_hash="h", ingested_at="t", total_chunks=1))
            for i, (f, p, text, emb) in enumerate(docs)
        ])
        raw = store.get_chunks_containing("E1", {"source_filename": {"$in": ["ccc2.pdf", "service.pdf"]}})
        check("store substring search obeys the filter and case",
              sorted(r["metadata"]["page_number"] for r in raw) == [2, 3], str(raw))
        ranked = store.get_chunks_containing("E1", {"source_filename": {"$eq": "ccc2.pdf"}}, [1.0, 0.0, 0.0, 0.0])
        check("store scores matches against the question", ranked and ranked[0]["similarity_score"] > 0.9
              if ranked[0]["metadata"]["page_number"] == 2 else ranked[1]["similarity_score"] > 0.9, str(ranked))

        matches = nodes._code_matches(store, "E1", ["ccc2.pdf", "service.pdf"], [1.0, 0.0, 0.0, 0.0])
        where = [(m.metadata.source_filename, m.metadata.page_number) for m in matches]
        check("only whole-token matches in the given manuals, any case, best match first",
              where == [("ccc2.pdf", 2), ("service.pdf", 36)], str(where))
        check("matches marked with the code", all(m.matched_parts == ["E1"] and m.match_type == "exact_code"
                                                  for m in matches))

        # Automatic pass: retrieved passage with the code is marked, new ones added, both listed first
        hits = [nodes._build_query_result(code_hit(3, "General wiring information.", 40, "service.pdf", 0.8)),
                nodes._build_query_result(code_hit(1, "Command 10 shows outdoor temperature; E10 is reserved.", 3,
                                                   "ccc2.pdf", 0.7)),
                nodes._build_query_result(code_hit(2, "e1 loss of communication causes the relay control module "
                                                   "to shut down.", 36, "service.pdf", 0.6))]
        out = nodes._add_code_matches(store, list(hits), hits, ["E1"], [1.0, 0.0, 0.0, 0.0])
        ordered = sorted(out, key=nodes._context_order_key)
        firsts = [(r.metadata.source_filename, r.metadata.page_number) for r in ordered[:2]]
        check("automatic pass adds the missing passage and puts code matches first",
              len(out) == 4 and sorted(firsts) == [("ccc2.pdf", 2), ("service.pdf", 36)], str(firsts))
        check("retrieved passage with the code is marked, not duplicated",
              hits[2].matched_parts == ["E1"] and not hits[1].matched_parts)
        check("lookup limited to the hits' manuals", not any(r.metadata.source_filename == "other.pdf" for r in out))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    class BrokenStore(FakeStore):
        def get_chunks_containing(self, *a, **k): raise RuntimeError("down")
    original = [nodes._build_query_result(hit(0))]
    check("lookup failure keeps the results",
          nodes._add_code_matches(BrokenStore(), list(original), original, ["E1"], None) == original)

    # Wiring in vector_store_search: rewritten query used, matches ordered first
    install_fakes(script=[], queue=[[hit(0, page=1), hit(1, page=5)]])
    FakeStore.contains = [code_hit(9, "E5 freeze sensor open", 3)]
    out = nodes.vector_store_search(QueryState(query_embedding=[0.1], config=config(), original_query="what about it?",
                                               retrieval_query="What does error E5 mean on the CCC2?"))
    first = out["search_results"][0]
    check("first retrieval looks up the rewritten query's code and lists the match first",
          first.metadata.chunk_sequence == 9 and first.matched_parts == ["E5"]
          and FakeStore.contains_calls and FakeStore.contains_calls[0][1] == {"source_filename": {"$eq": "manual.pdf"}},
          str([(r.metadata.chunk_sequence, r.matched_parts) for r in out["search_results"]]))
    install_fakes(script=[], queue=[[hit(0)]])
    nodes.vector_store_search(QueryState(query_embedding=[0.1], config=config(), original_query="How do I clean it?"))
    check("no lookup without a short code", FakeStore.contains_calls == [])
    check("LLM sees matched_part on code matches", nodes._format_result_for_llm(0, first).get("matched_part") == "E5")

    # History ordering: this turn's code matches first, carried documents keep reading order
    carried = replace(nodes._build_query_result(hit(4)), matched_parts=["E1"])
    current = [nodes._build_query_result(hit(2)), replace(nodes._build_query_result(hit(8)), matched_parts=["E5"])]
    turn = ChatTurn(question="q", answer="a", kind="answer", documents=[carried])
    seqs = [r.metadata.chunk_sequence for r in nodes.add_history_documents(
        QueryState(original_query="q", search_results=current, chat_history=[turn]))["search_results"]]
    check("earlier-turn code matches are not moved first", seqs == [8, 2, 4], str(seqs))

    # find_code tool through the agent loop
    install_fakes(script=[("find_code", {"code": "E1"}),
                          ("respond_to_user", {"answer": "Loss of communication", "diagram_refs": []})], queue=[])
    FakeStore.contains = [code_hit(7, "E1 Loss of communications", 3), code_hit(0, "chunk 0 text E1", 1),
                          code_hit(5, "Code E10 reserved", 4)]
    state = run_graph(initial_state())["state"]
    tool_msg = next(m["content"] for m in ScriptedLLM.seen[1]["messages"] if m["role"] == "tool")
    check("find_code offered to the agent", "find_code" in ScriptedLLM.seen[0]["tools"])
    check("find_code returns new matches, lists ones already in context",
          "2 passage(s) containing 'E1'" in tool_msg and "refs [0]" in tool_msg and '"matched_part": "E1"' in tool_msg
          and "E10" not in tool_msg, tool_msg[:300])
    check("find_code results join the context and use a search round",
          state.search_results[-1].metadata.chunk_sequence == 7 and state.search_round == 1)
    check("find_code searches the manuals in context",
          FakeStore.contains_calls[0][1] == {"source_filename": {"$eq": "manual.pdf"}})

    existing = [nodes._build_query_result(hit(0))]
    install_fakes(script=[], queue=[])
    _, text = nodes._run_find_code(config(), {"code": "E9"}, existing)
    check("find_code reports no match", text.startswith("No passage containing 'E9' in manual.pdf"), text)
    try:
        nodes._run_find_code(config(), {"code": " "}, existing)
        check("find_code rejects an empty code", False)
    except ValueError:
        check("find_code rejects an empty code", True)
    try:
        nodes._run_find_code(config(), {"code": "E1", "filename": "nope.pdf"}, existing)
        check("find_code rejects an unknown filename", False)
    except ValueError:
        check("find_code rejects an unknown filename", True)
    check("prompt tells the agent when to use find_code", "call find_code" in nodes.SYSTEM_PROMPT)


def test_nested_filenames() -> None:
    section("Citations of documents in subfolders")
    docs = [nodes._build_query_result(h) for h in (
        hit(0, page=1, filename="brand_a/manual.pdf"),
        hit(1, page=2, filename="brand_b/manual.pdf"),
        hit(2, page=3, filename="brand_b/other.pdf"),
    )]
    check("basename completed to the stored path",
          nodes._stored_filename("other.pdf", 3, docs) == "brand_b/other.pdf")
    check("shared basename resolved by the cited page",
          nodes._stored_filename("manual.pdf", 2, docs) == "brand_b/manual.pdf")
    check("ambiguous name left unchanged", nodes._stored_filename("manual.pdf", None, docs) == "manual.pdf")
    check("full path and unknown names unchanged",
          nodes._stored_filename("brand_a/manual.pdf", 2, docs) == "brand_a/manual.pdf"
          and nodes._stored_filename("nope.pdf", 1, docs) == "nope.pdf")
    check("sources show the stored path",
          nodes._build_sources([{"filename": "other.pdf", "page": 3}], docs)[0]["filename"] == "brand_b/other.pdf")
    check("steps cite the stored path", nodes._build_steps(
        [{"instruction": "x", "source_filename": "other.pdf", "page": 3}], docs)[0]["citation"]
        .startswith("brand_b/other.pdf, p.3"))


def test_query_grader() -> None:
    section("Live test runner: parsing and grading (tests/test_queries.py)")
    import test_queries as tq

    cases = tq.parse_cases(tq.QUERIES_FILE.read_text())
    ids = [c["id"] for c in cases]
    check("test-queries.md parses, format example ignored", ids[:1] == ["Q01"] and len(ids) == len(set(ids)),
          str(ids))
    sample = [{"id": i} for i in ("Q01", "R01", "RW01", "C02")]
    check("--only takes IDs and group prefixes",
          [c["id"] for c in tq.select_cases(sample, "r, c02")] == ["R01", "C02"])

    def rejected(body: str) -> bool:
        try:
            tq.parse_cases(f"## X1: t\n\n```yaml\n{body}\n```\n")
        except ValueError as e:
            return "X1" in str(e)
        return False

    check("typos in a case are rejected", all(rejected(b) for b in (
        "turns: [{ask: q, expect: {fact: [a]}}]", "turns: [{ask: q, expect: {kind: answr}}]",
        "turns: [{ask: q, expect: {facts: ['(']}}]", "turns: [{ask: q, expect: {cited: [{page: 1}]}}]",
        "turns: []", "history: [{ask: q}]\nturns: [{ask: q}]")))

    docs = [nodes._build_query_result(hit(0, page=2, filename="brand/manual.pdf")),
            replace(nodes._build_query_result(hit(1, page=3, filename="old.pdf")), from_history=True)]
    result = QueryState(
        original_query="and that one?", retrieval_query="CCC 5 button diagnostic mode", search_results=docs,
        search_round=1,
        response={"kind": "answer", "answer": "Press **MODE** twice.", "options": [], "diagrams": [],
                  "steps": [], "sources": [{"filename": "brand/manual.pdf", "page": 2}]})
    good = {"kind": "answer", "rewrite_has": ["5 button"], "rewrite_lacks": ["CCC2"],
            "retrieved": ["manual.pdf"], "cited": [{"file": "Manual.pdf", "pages": [2, 4]}],
            "cited_any": ["nope.pdf", "brand/manual.pdf"], "facts": ["press mode"], "not_facts": ["thrice"],
            "diagrams": False, "max_searches": 1}
    checks = tq.grade_turn(good, result)
    check("a correct turn passes every check", checks and all(c["passed"] for c in checks), str(checks))
    bad = {"kind": "clarify", "rewrite_has": ["furnace"], "rewrite_lacks": ["CCC"], "retrieved": ["old.pdf"],
           "cited": [{"file": "manual.pdf", "pages": [9]}], "cited_any": ["al.pdf"], "facts": ["thrice"],
           "not_facts": ["mode"], "options_has": ["CCC"], "diagrams": True, "max_searches": 0}
    checks = tq.grade_turn(bad, result)
    check("each wrong expectation fails", len(checks) == len(bad) and not any(c["passed"] for c in checks),
          str([c for c in checks if c["passed"]]))
    check("failures are tagged by stage",
          {c["stage"] for c in checks} == {"rewrite", "retrieval", "decision", "answer"})

    clarify = QueryState(original_query="q", response={
        "kind": "clarify", "answer": "Which thermostat?", "options": ["CCC 5 Button", "CT"],
        "steps": [], "sources": [], "diagrams": []})
    check("clarify options graded",
          all(c["passed"] for c in tq.grade_turn({"kind": "clarify", "options_has": ["5 button", r"\bCT\b"]}, clarify)))
    failed = tq.grade_turn({"kind": "answer"}, QueryState(original_query="q", errors=["LLM down"]))
    check("a failed turn is reported as a pipeline error", failed[0]["stage"] == "error" and not failed[0]["passed"])
    check("no documents found is 'empty'",
          all(c["passed"] for c in tq.grade_turn({"kind": ["empty", "insufficient"]}, QueryState(original_query="q"))))

    history = tq.scripted_history([{"ask": "q", "answer": "Which one?", "kind": "clarify",
                                    "options": ["A", "B"]}], None)
    check("scripted clarify history keeps its options", history[0].kind == "clarify"
          and "Options: A; B" in history[0].answer, history[0].answer)

    install_fakes(script=[], queue=[])
    ScriptedLLM.rewrite_reply = "CCC 5 button reset"
    case = {"history": [{"ask": "q", "answer": "a"}],
            "turns": [{"ask": "reset it", "expect": {"rewrite_has": ["5 button"], "kind": "answer"}}]}
    records = tq.run_rewrite(case, config())
    check("--rewrite-only grades just the rewrite", [c["stage"] for c in records[0]["checks"]] == ["rewrite"]
          and records[0]["checks"][0]["passed"])
    check("--rewrite-only skips cases without scripted history",
          tq.run_rewrite({**case, "history": []}, config()) is None)

    runs = [records, [{**records[0], "checks": [{"stage": "rewrite", "name": "n", "passed": False, "detail": ""}]}]]
    summary = "\n".join(tq.summarize([{"case": {"id": "RW01", "title": "t"}, "runs": runs}]))
    check("repeated runs report a pass rate", "FLAKY" in summary and "1/2" in summary
          and "rewrite 1" in summary, summary)


if __name__ == "__main__":
    for test in (test_find_code, test_query_grader, test_nested_filenames, test_part_numbers, test_hybrid_search, test_hybrid_wiring,
                 test_loop_with_search, test_cap, test_deadline, test_search_failure_and_empty,
                 test_failure_routes_to_fallback, test_read_page, test_neighbors, test_search_cutoff_env, test_steps_formatting, test_where_builder,
                 test_chroma_where, test_graph_wiring, test_conversation, test_sections, test_cited_documents,
                 test_diagram_priority, test_tool_call_reliability, test_structured_response,
                 test_history_hygiene, test_prompt_and_tools):
        test()
    failed = [n for n, ok in _results if not ok]
    print(f"\n{len(_results) - len(failed)}/{len(_results)} checks passed")
    sys.exit(1 if failed else 0)
