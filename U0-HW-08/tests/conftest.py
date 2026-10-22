import glob
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)  # agent writes output/ relative to cwd

import Example_Board  # noqa: E402

BOARDS = {
    "easy": Example_Board.sudoku_easy,
    "medium": Example_Board.sudoku_medium,
    "hard": Example_Board.sudoku_hard,
    "diabolical": Example_Board.sudoku_diabolical,
}
SUMMARY = []
WARNINGS = []


def pytest_addoption(parser):
    parser.addoption("--difficulty", default="all",
                     choices=list(BOARDS) + ["all"],
                     help="Board difficulty to run (default: all)")
    parser.addoption("--budget", type=int, default=350)
    parser.addoption("--max-invalid-ratio", type=float, default=0.20,
                     help="Warn if invalid/total calls exceeds this")


def pytest_configure(config):
    config.addinivalue_line("markers", "llm: runs the real LLM agent")


def pytest_generate_tests(metafunc):
    if "difficulty" in metafunc.fixturenames:
        sel = metafunc.config.getoption("--difficulty")
        names = list(BOARDS) if sel == "all" else [sel]
        metafunc.parametrize("difficulty", names)


@pytest.fixture(scope="session")
def _runs():
    return {}


@pytest.fixture
def agent_run(difficulty, _runs, request):
    """Runs the agent once per difficulty; cached for all tests."""
    if difficulty not in _runs:
        import react_agent
        budget = request.config.getoption("--budget")
        puzzle = [row[:] for row in BOARDS[difficulty]]
        started = time.time()
        state, elapsed, text, path = react_agent.run_agent(puzzle, budget=budget)
        _runs[difficulty] = dict(
            state=state, elapsed=elapsed, report=text, path=path,
            puzzle=puzzle, started=started, budget=budget, difficulty=difficulty)
        from tests.helpers import solve, grid_of
        b = state["board_state"]
        ref = solve(puzzle)
        correct = sum(1 for r in range(9) for c in range(9)
                      if puzzle[r][c] == 0 and b.readTile(r, c) == ref[r][c])
        SUMMARY.append(dict(
            difficulty=difficulty, empty=sum(v == 0 for row in puzzle for v in row),
            filled=state["tiles_filled"], correct=correct,
            incomplete=b.validateIncompleteBoard(), complete=b.validatePuzzle(),
            kb=state["knowledge_base"].is_consistent(),
            total=state["total_calls_count"], invalid=state["invalid_calls_count"],
            solved=state["solved"], elapsed=elapsed, path=path))
    return _runs[difficulty]


@pytest.fixture
def warn():
    def _w(msg):
        WARNINGS.append(msg)
        import warnings
        warnings.warn(msg)
    return _w


def pytest_terminal_summary(terminalreporter):
    tr = terminalreporter
    if SUMMARY:
        tr.section("Sudoku agent results")
        tr.write_line(f"{'board':<11}{'filled':>7}{'correct':>9}{'incompl':>9}"
                      f"{'puzzle':>8}{'KB':>6}{'calls':>7}{'invalid':>8}{'time':>8}")
        for s in SUMMARY:
            tr.write_line(
                f"{s['difficulty']:<11}{s['filled']:>3}/{s['empty']:<3}"
                f"{s['correct']:>5}/{s['empty']:<3}"
                f"{'PASS' if s['incomplete'] else 'FAIL':>6}"
                f"{'PASS' if s['complete'] else 'FAIL':>8}"
                f"{'OK' if s['kb'] else 'BAD':>6}{s['total']:>7}{s['invalid']:>8}"
                f"{s['elapsed']:>7.0f}s")
            tr.write_line(f"    solved={s['solved']} report={s['path']}")
    if WARNINGS:
        tr.section("Agent warnings (non-fatal)")
        for w in WARNINGS:
            tr.write_line(w)
