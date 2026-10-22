"""Black-box helpers: reference solver and tolerant tool-call accessors."""
import copy

ALLOWED_TOOLS = {
    "readBoard": set(), "readTile": {"row", "column"}, "readRow": {"row"},
    "readColumn": {"column"}, "readBlock": {"block"},
    "writeTile": {"row", "column", "value"}, "eraseTile": {"row", "column"},
    "checkTileCandidate": {"row", "column", "candidate"},
    "validateRow": {"row"}, "validateColumn": {"column"}, "validateBlock": {"block"},
    "validateIncompleteBoard": set(), "validatePuzzle": set(),
    "locateBlock": {"row", "column"}, "checkIfInRow": {"row", "value"},
    "checkIfInColumn": {"column", "value"}, "checkIfInBlock": {"block", "value"},
    "kb_add_formula": {"formula"}, "kb_add_clause": {"literals"},
    "kb_add_fact": {"symbol", "positive"}, "kb_entails": {"query"},
    "kb_is_consistent": set(),
}
RANGES = {"row": (0, 8), "column": (0, 8), "block": (0, 8),
          "value": (1, 9), "candidate": (1, 9)}


def solve(puzzle):
    """Backtracking solver. Returns solved grid or None."""
    g = copy.deepcopy(puzzle)
    def ok(r, c, v):
        if any(g[r][i] == v or g[i][c] == v for i in range(9)):
            return False
        br, bc = 3 * (r // 3), 3 * (c // 3)
        return all(g[br + i][bc + j] != v for i in range(3) for j in range(3))
    def rec():
        for r in range(9):
            for c in range(9):
                if g[r][c] == 0:
                    for v in range(1, 10):
                        if ok(r, c, v):
                            g[r][c] = v
                            if rec():
                                return True
                            g[r][c] = 0
                    return False
        return True
    return g if rec() else None


def count_solutions(puzzle, limit=2):
    g = copy.deepcopy(puzzle)
    n = 0
    def ok(r, c, v):
        if any(g[r][i] == v or g[i][c] == v for i in range(9)):
            return False
        br, bc = 3 * (r // 3), 3 * (c // 3)
        return all(g[br + i][bc + j] != v for i in range(3) for j in range(3))
    def rec():
        nonlocal n
        for r in range(9):
            for c in range(9):
                if g[r][c] == 0:
                    for v in range(1, 10):
                        if ok(r, c, v):
                            g[r][c] = v
                            rec()
                            g[r][c] = 0
                            if n >= limit:
                                return
                    return
        n += 1
    rec()
    return n


def kind(e):
    return e.get("type")


def name(e):
    return e.get("name") or e.get("tool")


def params(e):
    p = e.get("parameters", e.get("params"))
    return p if isinstance(p, dict) else {}


def result(e):
    return e.get("result", e.get("error"))


def grid_of(board):
    return [[board.readTile(r, c) for c in range(9)] for r in range(9)]
