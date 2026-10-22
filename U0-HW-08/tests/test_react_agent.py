"""Black-box tests for the ReAct Sudoku agent.

Run:  pytest tests --difficulty easy|medium|hard|diabolical|all [--budget 350] -s
pytest tests --difficulty easy -s
"""
import glob
import os
import re

import pytest

from tests.conftest import BOARDS
from tests.helpers import (ALLOWED_TOOLS, RANGES, count_solutions, grid_of,
                           kind, name, params, result, solve)

pytestmark = pytest.mark.llm
KINDS = {"executed", "failed", "rejected"}


def calls(run):
    return run["state"]["tool_calls"]


def executed(run):
    return [e for e in calls(run) if kind(e) in ("executed", "failed")]


# ---- sanity of the test fixtures themselves (no LLM) ----
@pytest.mark.parametrize("d", list(BOARDS))
def test_board_has_unique_solution(d):
    assert count_solutions(BOARDS[d]) == 1


# ---- 1. report-only metrics ----
def test_report_metrics(agent_run):
    s, p = agent_run["state"], agent_run["puzzle"]
    b = s["board_state"]
    ref = solve(p)
    empty = sum(v == 0 for row in p for v in row)
    correct = sum(1 for r in range(9) for c in range(9)
                  if p[r][c] == 0 and b.readTile(r, c) == ref[r][c])
    print(f"\n[{agent_run['difficulty']}] tiles filled: {s['tiles_filled']}/{empty}"
          f" | correct: {correct}/{empty}"
          f" | validateIncompleteBoard: {b.validateIncompleteBoard()}"
          f" | validatePuzzle: {b.validatePuzzle()}"
          f" | KB consistent: {s['knowledge_base'].is_consistent()}"
          f" | calls: {s['total_calls_count']} (invalid {s['invalid_calls_count']})"
          f" | {agent_run['elapsed']:.1f}s")
    print(agent_run["report"])


# ---- 2. budget ----
def test_budget(agent_run):
    s = agent_run["state"]
    assert s["total_calls_count"] <= agent_run["budget"]
    assert 0 <= s["invalid_calls_count"] <= s["total_calls_count"]


def test_invalid_call_ratio(agent_run, request, warn):
    s = agent_run["state"]
    limit = request.config.getoption("--max-invalid-ratio")
    ratio = s["invalid_calls_count"] / max(1, s["total_calls_count"])
    if ratio > limit:
        warn(f"[{agent_run['difficulty']}] invalid-call ratio {ratio:.0%} > {limit:.0%} "
             f"({s['invalid_calls_count']}/{s['total_calls_count']})")


# ---- 3. tool-call format & tracking ----
def test_call_types_and_counts(agent_run):
    s = agent_run["state"]
    tc = calls(agent_run)
    assert all(kind(e) in KINDS for e in tc), {kind(e) for e in tc}
    assert len(tc) == s["total_calls_count"]
    rejected = sum(kind(e) == "rejected" for e in tc)
    # invalid calls are tracked separately; rejected calls must be among them
    assert s["invalid_calls_count"] >= rejected


def test_executed_entries_have_fields(agent_run):
    for e in executed(agent_run):
        assert name(e), e
        assert isinstance(params(e), dict), e
        assert "result" in e or "error" in e, e


def test_rejected_entries_have_reason(agent_run):
    for e in calls(agent_run):
        if kind(e) == "rejected":
            assert e.get("call") is not None or e.get("json") is not None, e
            assert e.get("reason") or e.get("error"), e


def test_tool_names_allowed(agent_run):
    bad = [name(e) for e in executed(agent_run) if name(e) not in ALLOWED_TOOLS]
    assert not bad, bad


def test_parameters_valid(agent_run):
    for e in executed(agent_run):
        if kind(e) != "executed" or name(e) not in ALLOWED_TOOLS:
            continue
        p = params(e)
        assert set(p) == ALLOWED_TOOLS[name(e)], (name(e), p)
        for k, (lo, hi) in RANGES.items():
            if k in p:
                assert isinstance(p[k], int) and lo <= p[k] <= hi, (name(e), p)


# ---- 4. behavioural invariants ----
def test_first_call_is_read_board(agent_run):
    ex = [e for e in calls(agent_run) if kind(e) == "executed"]
    assert ex and name(ex[0]) == "readBoard"


def test_clues_unchanged(agent_run):
    g, p = grid_of(agent_run["state"]["board_state"]), agent_run["puzzle"]
    for r in range(9):
        for c in range(9):
            if p[r][c]:
                assert g[r][c] == p[r][c], (r, c)


def test_write_followed_by_incomplete_validation(agent_run):
    """After a successful writeTile, the agent must be told the validation result."""
    msgs = agent_run["state"]["agent_messages"]
    text = " ".join(str(m) for m in msgs)
    if any(name(e) == "writeTile" for e in executed(agent_run)):
        assert re.search(r"validat", text, re.I)


def test_writes_preceded_by_kb_entails(agent_run, warn):
    seen_entails = False
    unjustified = 0
    for e in executed(agent_run):
        if name(e) == "kb_entails":
            seen_entails = True
        if name(e) == "writeTile" and not seen_entails:
            unjustified += 1
    if unjustified:
        warn(f"[{agent_run['difficulty']}] {unjustified} writeTile call(s) "
             f"before any kb_entails query")


def test_writes_justified_per_cell(agent_run, warn):
    """Warn when a written (row,col,value) was never queried via kb_entails."""
    queries = [str(params(e).get("query", "")) for e in executed(agent_run)
               if name(e) == "kb_entails"]
    missing = []
    for e in executed(agent_run):
        if name(e) == "writeTile":
            p = params(e)
            r, c, v = p.get("row"), p.get("column"), p.get("value")
            if not any(f"_{r}_{c}_" in q and str(v) in q for q in queries):
                missing.append((r, c, v))
    if missing:
        warn(f"[{agent_run['difficulty']}] writes without matching kb_entails: {missing[:10]}"
             f"{'...' if len(missing) > 10 else ''}")


def test_correctness_reported(agent_run, warn):
    s, p = agent_run["state"], agent_run["puzzle"]
    ref, g = solve(p), grid_of(s["board_state"])
    wrong = [(r, c, g[r][c]) for r in range(9) for c in range(9)
             if p[r][c] == 0 and g[r][c] not in (None, 0) and g[r][c] != ref[r][c]]
    if wrong:
        warn(f"[{agent_run['difficulty']}] {len(wrong)} incorrect tile(s): {wrong[:10]}")


# ---- 5. termination ----
def test_solved_implies_valid_and_full(agent_run):
    s = agent_run["state"]
    b = s["board_state"]
    if s["solved"]:
        assert b.validatePuzzle()
        assert all(v for row in grid_of(b) for v in row)
        last = [e for e in executed(agent_run) if name(e) == "validatePuzzle"]
        assert last and last[-1].get("result") is True


def test_unsolved_means_budget_exhausted(agent_run):
    s = agent_run["state"]
    if not s["solved"]:
        assert s["total_calls_count"] >= agent_run["budget"]


# ---- 6. report file ----
def test_report_file(agent_run):
    path = agent_run["path"]
    assert path and os.path.exists(path)
    assert re.fullmatch(r"output/output_\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d\.txt", path)
    assert os.path.getmtime(path) >= agent_run["started"] - 1
    txt = open(path).read()
    for section in ["Final board state", "Execution Metrics", "Board Validation",
                    "Knowledge Base Status", "Puzzle Solve Status"]:
        assert section in txt, section
    s = agent_run["state"]
    assert re.search(rf"Total tool calls:\s+{s['total_calls_count']}\b", txt)
    assert re.search(rf"Invalid tool calls:\s+{s['invalid_calls_count']}\b", txt)
    assert re.search(rf"Tiles filled by agent:\s+{s['tiles_filled']}\b", txt)


def test_report_tool_analysis(agent_run):
    txt = open(agent_run["path"]).read()
    assert re.search(r"executed", txt, re.I)
    assert re.search(r"failed", txt, re.I)
    assert re.search(r"rejected", txt, re.I)
    used = {name(e) for e in executed(agent_run)}
    for t in used:
        assert t in txt, t


# ---- 7. KB ----
def test_kb_initial_state(agent_run):
    kb = agent_run["state"]["knowledge_base"]
    erased = any(name(e) == "eraseTile" for e in executed(agent_run))
    if not erased:
        assert kb.is_consistent()
