"""
Offline verification of the query agent loop (search_documents + steps).

Needs no embedding/LLM server: the LLM is scripted, embeddings are fake and
Chroma is a temporary database.

Usage:
    python tests/verify_query_loop.py
"""

import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import IngestionConfig  # noqa: E402
from src.data_models import ChunkMetadata, QueryResult, QueryState, TextChunk  # noqa: E402
from src.ingestion.tools import ChromaVectorStore  # noqa: E402
from src.query import graph as query_graph  # noqa: E402
from src.query import nodes  # noqa: E402

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


class ScriptedLLM:
    """Plays back a list of (tool_name, params) and records what it was sent."""
    script: list = []
    seen: list[dict] = []
    def __init__(self, *a, **k): pass
    def chat_with_tools(self, messages, tools, temperature=0.7):
        ScriptedLLM.seen.append({"messages": [dict(m) for m in messages], "tools": [t["name"] for t in tools]})
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
    FakeStore.queue, FakeStore.calls = [list(q) for q in queue], []
    FakeStore.chunk_queue, FakeStore.chunk_calls = [], []


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
    check("search tool removed after 3 rounds", ScriptedLLM.seen[-1]["tools"] == ["respond_to_user", "insufficient_evidence"],
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
    install_fakes(script=[ValueError("No tool calls in LLM response")], queue=[])
    state = run_graph(initial_state())["state"]
    check("fallback response on LLM failure", state.status == "response_generated_with_errors", state.status)


def test_steps_formatting() -> None:
    section("Steps formatting")
    out = nodes._format_steps([{"instruction": "A", "page": 2}, "B", {"nope": 1}, 7, {"instruction": "  "}])
    check("tolerates malformed entries", out == ["1. A (p.2)", "2. B"], str(out))
    check("non-list ignored", nodes._format_steps("x") == [] and nodes._format_steps(None) == [])


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
    section("SEARCH_MIN_SIMILARITY")
    def cutoff_used(env):
        os.environ.pop("SEARCH_MIN_SIMILARITY", None)
        if env is not None:
            os.environ["SEARCH_MIN_SIMILARITY"] = env
        install_fakes(script=[("search_documents", {"query": "x"}),
                              ("respond_to_user", {"answer": "ok", "include_sources": False})], queue=[[]])
        run_graph(initial_state())
        return FakeStore.calls[0]["min_similarity_score"]
    try:
        check("default is no cutoff", cutoff_used(None) == 0.0)
        check("env var applied", cutoff_used("0.35") == 0.35)
        check("invalid value falls back", cutoff_used("abc") == 0.0)
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


if __name__ == "__main__":
    for test in (test_loop_with_search, test_cap, test_deadline, test_search_failure_and_empty,
                 test_failure_routes_to_fallback, test_read_page, test_neighbors, test_search_cutoff_env, test_steps_formatting, test_where_builder,
                 test_chroma_where, test_graph_wiring):
        test()
    failed = [n for n, ok in _results if not ok]
    print(f"\n{len(_results) - len(failed)}/{len(_results)} checks passed")
    sys.exit(1 if failed else 0)
