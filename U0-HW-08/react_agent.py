"""
ReAct Sudoku solver agent (Version 1) built with LangGraph.

Graph:  llm_decision -> parse_response -> execute_tool -> llm_decision (loop)
Only llm_decision makes an API call. Invalid responses route from
parse_response straight back to llm_decision with an error message.

Run:  python react_agent.py
"""
import json
import os
import time
from typing import Any, TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from openai import OpenAI

from board import Board
from Example_Board import sudoku_puzzle
from proplogic import KnowledgeBase, Literal, atom

BUDGET = 15
MAX_NONWRITE_CALLS_PER_TILE = 9
HISTORY_WINDOW = 12  # most recent conversation messages sent to the LLM
SUCCESS_TILES = 10


# --------------------------------------------------------------------------
# Environment / LLM access
# --------------------------------------------------------------------------
load_dotenv()
API_KEY = os.environ["API_KEY"]
URL = os.environ["URL"].rstrip("/")
MODEL = os.environ["MODELS"].split(",")[0].strip()  # MODELS[0]

client = OpenAI(api_key=API_KEY, base_url=URL, timeout=120)


def call_llm(messages: list[dict]) -> str:
    """Single chat-completion call to the OpenAI-compatible endpoint."""
    last_err = None
    for _ in range(3):
        try:
            resp = client.chat.completions.create(model=MODEL, messages=messages, temperature=0)
            return resp.choices[0].message.content or ""
        except Exception as e:  # network / HTTP / shape errors
            last_err = e
    raise RuntimeError(f"LLM API failed after 3 attempts: {last_err}")


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------
# name -> (param spec {name: (lo, hi)}, description, function(board, **params))
TOOLS: dict[str, tuple[dict, str, Any]] = {
    "readBoard": ({}, "Returns the 9x9 grid (list of rows, null = empty).",
                   lambda b: b.readBoard()),
    "readTile": ({"row": (0, 8), "column": (0, 8)},
                 "Returns the tile value (1-9) or null if empty.",
                 lambda b, row, column: b.readTile(row, column)),
    "readRow": ({"row": (0, 8)},
                "Returns list of 9 values from the row in left-to-right order (values are 1-9 or null).",
                lambda b, row: b.readRow(row)),
    "readColumn": ({"column": (0, 8)},
                   "Returns list of 9 values from the column in top-to-bottom order (values are 1-9 or null).",
                   lambda b, column: b.readColumn(column)),
    "readBlock": ({"block": (0, 8)},
                  "Returns list of 9 values from the block in reading order (left to right, top to bottom; values are 1-9 or null).",
                  lambda b, block: b.readBlock(block)),
    "writeTile": ({"row": (0, 8), "column": (0, 8), "value": (1, 9)},
                  "Writes value to a writable tile. Returns true on success, false otherwise. "
                  "You will be told whether the board still validates afterwards.",
                  lambda b, row, column, value: b.writeValue(row, column, value)),
    "eraseTile": ({"row": (0, 8), "column": (0, 8)},
                  "Erases a writable tile. Returns true on success, false otherwise.",
                  lambda b, row, column: b.eraseTile(row, column)),
    "checkTileCandidate": ({"row": (0, 8), "column": (0, 8), "candidate": (1, 9)},
                           "Returns true if the candidate is valid for the tile (not already in its "
                           "row, column or block), false if it conflicts.",
                           lambda b, row, column, candidate: b.checkTileCandidate(row, column, candidate)),
    "validateRow": ({"row": (0, 8)}, "Returns true if the row has no duplicate values.",
                    lambda b, row: b.validateRow(row)),
    "validateColumn": ({"column": (0, 8)}, "Returns true if the column has no duplicate values.",
                       lambda b, column: b.validateColumn(column)),
    "validateBlock": ({"block": (0, 8)}, "Returns true if the block has no duplicate values.",
                      lambda b, block: b.validateBlock(block)),
    "validateIncompleteBoard": ({}, "Returns true if the board has no duplicates (empty tiles allowed).",
                                lambda b: b.validateIncompleteBoard()),
    "validatePuzzle": ({}, "Returns true if the board is completely filled and valid. "
                           "Call this when you believe the puzzle is complete.",
                       lambda b: b.validatePuzzle()),
    "locateBlock": ({"row": (0, 8), "column": (0, 8)}, "Returns the block index (0-8) of the tile.",
                    lambda b, row, column: b.locateBlock(row, column)),
    "checkIfInRow": ({"row": (0, 8), "value": (1, 9)}, "Returns true if value exists in the row.",
                     lambda b, row, value: b.checkIfInRow(row, value)),
    "checkIfInColumn": ({"column": (0, 8), "value": (1, 9)}, "Returns true if value exists in the column.",
                        lambda b, column, value: b.checkIfInColumn(column, value)),
    "checkIfInBlock": ({"block": (0, 8), "value": (1, 9)}, "Returns true if value exists in the block.",
                       lambda b, block, value: b.checkIfInBlock(block, value)),
    # Note: KB tools require special handling in execute_tool() to access KB from state
    "kb_add_formula": ({"formula": (0, 0)},  # special: string param, validation handled separately
                       "Add a formula to the knowledge base (parsed and converted to CNF). Useful for encoding Sudoku constraints. Returns true if added successfully.",
                       lambda b, formula: True),  # implemented in execute_tool()
    "kb_add_clause": ({"literals": (0, 0)},  # special: list param, validation handled separately
                      "Add a disjunctive clause (OR) of literals to the knowledge base when constraints are in CNF form. Returns true if added successfully.",
                      lambda b, literals: True),  # implemented in execute_tool()
    "kb_add_fact": ({"symbol": (0, 0), "positive": (0, 0)},  # special: string/bool params, validation handled separately
                    "Add a definite fact to the knowledge base (e.g., cell_0_0_has_value_5 is True). Returns true if added successfully.",
                    lambda b, symbol, positive: True),  # implemented in execute_tool()
    "kb_entails": ({"query": (0, 0)},  # special: string param, validation handled separately
                   "Query the knowledge base to check if a deduction is entailed. Returns true if KB entails the query.",
                   lambda b, query: True),  # implemented in execute_tool()
    "kb_is_consistent": ({}, "Check if the knowledge base is consistent (contains no contradictions). Returns true if consistent, false otherwise.",
                         lambda b: True),  # implemented in execute_tool()
}


def tool_descriptions() -> str:
    lines = []
    for name, (spec, desc, _) in TOOLS.items():
        params = ", ".join(f'"{p}": {lo}-{hi}' for p, (lo, hi) in spec.items())
        lines.append(f'- {name}  parameters {{{params}}}: {desc}')
    return "\n".join(lines)


CALL_FORMAT = '{"tool": "toolName", "parameters": {"param1": value1, "param2": value2}}'


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------
class AgentState(TypedDict):
    board_state: Board
    knowledge_base: KnowledgeBase
    current_tile_index: int
    tiles_filled: int
    invalid_calls_count: int
    total_calls_count: int
    agent_messages: list[dict]   # full trace: {"role", "content"}
    # internal plumbing between nodes
    last_response: str
    parsed_call: dict | None
    nonwrite_calls_on_tile: int
    done: bool
    solved: bool


def initialize_knowledge_base(board: Board) -> KnowledgeBase:
    """
    Initialize a knowledge base with fundamental Sudoku constraints.
    
    Adds:
    - Cell Constraints: Each tile contains exactly one value (mutual exclusion)
    - Uniqueness Constraints: For each row, column, and block, no two positions contain the same value
    - Initial Facts: Clues from the puzzle (starter tiles)
    
    Args:
        board: The Sudoku board with clues
        
    Returns:
        Initialized KnowledgeBase
    """
    kb = KnowledgeBase()
    
    # Cell Constraints: Each tile (r, c) must have exactly one value
    # For each tile, add: cell_r_c_has_v OR cell_r_c_has_v+1 OR ... (at least one)
    # And add mutual exclusion: NOT (cell_r_c_has_v AND cell_r_c_has_w) for v != w
    for r in range(9):
        for c in range(9):
            # At least one value must be true for this tile
            literals = [Literal(f"cell_{r}_{c}_has_{v}") for v in range(1, 10)]
            kb.add_clause(*literals)
            
            # Mutual exclusion: no two values can be simultaneously true
            for v in range(1, 10):
                for w in range(v + 1, 10):
                    kb.add_clause(
                        Literal(f"cell_{r}_{c}_has_{v}", False),
                        Literal(f"cell_{r}_{c}_has_{w}", False)
                    )
    
    # Uniqueness Constraints: Each row, column, and block contains each value at most once
    
    # Row uniqueness: For each row and value, at most one cell in the row has that value
    for r in range(9):
        for v in range(1, 10):
            for c1 in range(9):
                for c2 in range(c1 + 1, 9):
                    kb.add_clause(
                        Literal(f"cell_{r}_{c1}_has_{v}", False),
                        Literal(f"cell_{r}_{c2}_has_{v}", False)
                    )
    
    # Column uniqueness: For each column and value, at most one cell in the column has that value
    for c in range(9):
        for v in range(1, 10):
            for r1 in range(9):
                for r2 in range(r1 + 1, 9):
                    kb.add_clause(
                        Literal(f"cell_{r1}_{c}_has_{v}", False),
                        Literal(f"cell_{r2}_{c}_has_{v}", False)
                    )
    
    # Block uniqueness: For each 3x3 block and value, at most one cell in the block has that value
    for block in range(9):
        block_row = (block // 3) * 3
        block_col = (block % 3) * 3
        
        # Get all cells in this block
        block_cells = []
        for r in range(block_row, block_row + 3):
            for c in range(block_col, block_col + 3):
                block_cells.append((r, c))
        
        # For each value, no two cells in the block can have it
        for v in range(1, 10):
            for i in range(len(block_cells)):
                for j in range(i + 1, len(block_cells)):
                    r1, c1 = block_cells[i]
                    r2, c2 = block_cells[j]
                    kb.add_clause(
                        Literal(f"cell_{r1}_{c1}_has_{v}", False),
                        Literal(f"cell_{r2}_{c2}_has_{v}", False)
                    )
    
    # Initial Facts: Add clues from the board as facts
    for r in range(9):
        for c in range(9):
            value = board.readTile(r, c)
            if value is not None:
                # This tile has a definite value from the puzzle clue
                kb.add_fact(Literal(f"cell_{r}_{c}_has_{value}"))
    
    return kb


def count_filled(board: Board) -> int:
    return sum(1 for r in range(9) for c in range(9)
               if board.grid[r][c].writable and board.grid[r][c].value is not None)


def advance_index(board: Board, idx: int) -> int:
    """Move to the next writable, empty tile in reading order (wraps 80 -> 0)."""
    for step in range(1, 82):
        j = (idx + step) % 81
        t = board.grid[j // 9][j % 9]
        if t.writable and t.value is None:
            return j
    return idx  # nothing empty left


def render_board(board: Board) -> str:
    out = ["    0 1 2   3 4 5   6 7 8"]
    for r in range(9):
        cells = []
        for c in range(9):
            v = board.readTile(r, c)
            cells.append("." if v is None else str(v))
        out.append(f"{r}   " + "   ".join(" ".join(cells[i:i + 3]) for i in (0, 3, 6)))
    return "\n".join(out)


def build_system_prompt(state: AgentState) -> str:
    idx = state["current_tile_index"]
    return (
        "You are a Sudoku solving bot. Your goal: solve the puzzle by filling every empty tile with the correct value.\n\n"
        f"Current board ('.' = empty; rows/columns/blocks numbered 0-8 from top left):\n"
        f"{render_board(state['board_state'])}\n\n"
        f"Current tile: ({idx // 9}, {idx % 9})\n\n"
        "**SOLVING STRATEGY (Version 2 - Knowledge Base Solver):**\n"
        "1. Use kb_entails() to query which values are logically possible for the current tile.\n"
        "2. For each candidate value (1-9), call kb_entails() with the query 'cell_{row}_{col}_has_{value}'.\n"
        "3. Count how many candidates the KB entails. If exactly ONE value is entailed, that is the only valid choice.\n"
        "4. ONLY call writeTile() when the KB entails exactly one candidate for that tile.\n"
        "5. After writeTile(), the cell is automatically added to the KB as a fact to propagate constraints.\n"
        "6. Check kb_is_consistent() if you suspect logical contradictions.\n"
        "7. When all 81 tiles are filled, call validatePuzzle() to verify the solution.\n"
        "8. Use readRow(), readColumn(), readBlock() to efficiently analyze board structure.\n\n"
        f"Available tools:\n{tool_descriptions()}\n\n"
        f"Output a single tool call as JSON {CALL_FORMAT} and nothing else."
    )


def log(msg: str) -> None:
    print(msg, flush=True)


def record(state: AgentState, role: str, content: str) -> None:
    state["agent_messages"].append({"role": role, "content": content})


# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------
def llm_decision(state: AgentState) -> dict:
    """The only node that calls the LLM. Counts every response against the budget."""
    n = state["total_calls_count"] + 1
    history = [m for m in state["agent_messages"] if m["role"] in ("user", "assistant")]
    messages = [{"role": "system", "content": build_system_prompt(state)}]
    messages += history[-HISTORY_WINDOW:]
    if not history or history[-1]["role"] == "assistant":
        messages.append({"role": "user", "content": "Make your next tool call."})

    log(f"\n===== Call {n}/{BUDGET} | tile index {state['current_tile_index']} "
        f"| filled {state['tiles_filled']} =====")
    response = call_llm(messages)
    # log(f"[LLM response]\n{response}")
    record(state, "assistant", response)
    return {"last_response": response, "total_calls_count": n}


def _extract_json(text: str) -> Any:
    """Find the first balanced {...} that parses as JSON and has a 'tool' key."""
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        if isinstance(obj, dict) and "tool" in obj:
                            return obj
                    except json.JSONDecodeError:
                        pass
                    break
        start = text.find("{", start + 1)
    return None


def _validate_call(call: Any) -> str | None:
    """Return an error reason, or None if the call is well-formed."""
    if call is None:
        return "No JSON object with a 'tool' key was found."
    name = call["tool"]
    if name not in TOOLS:
        return f"Unknown tool '{name}'."
    params = call.get("parameters", {})
    if not isinstance(params, dict):
        return "'parameters' must be a JSON object (dict)."
    spec = TOOLS[name][0]
    if set(params) != set(spec):
        return f"Tool '{name}' requires exactly the parameters {list(spec)}; got {list(params)}."
    
    # Special handling for proplogic tools (string and complex parameters)
    if name in ("kb_add_formula", "kb_entails"):
        # These tools expect a "query" or "formula" string parameter
        if "formula" in spec or "query" in spec:
            param_name = "formula" if "formula" in spec else "query"
            if param_name in params and not isinstance(params[param_name], str):
                return f"Parameter '{param_name}' must be a string; got {type(params[param_name]).__name__}."
    elif name == "kb_add_fact":
        # Expects "symbol" (string) and "positive" (bool)
        if "symbol" in params and not isinstance(params["symbol"], str):
            return "Parameter 'symbol' must be a string."
        if "positive" in params and not isinstance(params["positive"], bool):
            return "Parameter 'positive' must be a boolean."
    elif name == "kb_add_clause":
        # Expects "literals" as a list of {symbol: str, positive: bool}
        if "literals" in params:
            if not isinstance(params["literals"], list):
                return "Parameter 'literals' must be a list."
            for i, lit in enumerate(params["literals"]):
                if not isinstance(lit, dict):
                    return f"Literal at index {i} must be a dict with 'symbol' and 'positive'."
                if not isinstance(lit.get("symbol"), str):
                    return f"Literal at index {i}: 'symbol' must be a string."
                if not isinstance(lit.get("positive"), bool):
                    return f"Literal at index {i}: 'positive' must be a boolean."
    
    # Standard integer range validation for non-proplogic tools
    for p, (lo, hi) in spec.items():
        if lo == 0 and hi == 0:  # Skip validation for special params (proplogic tools)
            continue
        v = params[p]
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            return f"Parameter '{p}' must be an integer in {lo}-{hi}; got {v!r}."
    return None


def parse_response(state: AgentState) -> dict:
    call = _extract_json(state["last_response"])
    reason = _validate_call(call)
    if reason is None:
        return {"parsed_call": call}

    invalid = state["invalid_calls_count"] + 1
    attempted = json.dumps(call) if call is not None else state["last_response"].strip()
    error = (f"Invalid tool call: {attempted}\nReason: {reason}\n\n"
             f"Allowed tools:\n{tool_descriptions()}\n\nProper call format: {CALL_FORMAT}")
    log(f"[Invalid call #{invalid}] {reason}")
    record(state, "user", error)
    return {"parsed_call": None, "invalid_calls_count": invalid}


def execute_tool(state: AgentState) -> dict:
    board = state["board_state"]
    kb = state["knowledge_base"]
    call = state["parsed_call"]
    name, params = call["tool"], call.get("parameters", {})
    
    # Handle proplogic tools specially (they need access to KB from state)
    if name == "kb_add_formula":
        kb.add_formula(params["formula"])
        result = True
        log(f"[KB] Added formula: {params['formula']}")
    elif name == "kb_add_clause":
        # Convert list of {symbol, positive} dicts to Literal objects
        literals = [Literal(lit["symbol"], lit["positive"]) for lit in params["literals"]]
        kb.add_clause(*literals)
        result = True
        lit_str = " OR ".join(f"{'~' if not lit.positive else ''}{lit.symbol}" for lit in literals)
        log(f"[KB] Added clause: {lit_str}")
    elif name == "kb_add_fact":
        kb.add_fact(Literal(params["symbol"], params["positive"]))
        result = True
        fact_str = f"{'~' if not params['positive'] else ''}{params['symbol']}"
        log(f"[KB] Added fact: {fact_str}")
    elif name == "kb_entails":
        result = kb.entails(params["query"])
        log(f"[KB] Entailment query '{params['query']}': {result}")
    elif name == "kb_is_consistent":
        result = kb.is_consistent()
        log(f"[KB] Consistency check: {result}")
    else:
        # Non-proplogic tools use the standard tool function
        result = TOOLS[name][2](board, **params)
    
    log(f"[Tool] {name}({params}) -> {result}")

    feedback = f"Tool result for {name}({json.dumps(params)}): {json.dumps(result)}"
    updates: dict = {}

    if name == "writeTile":
        # Add the written tile to the KB as a fact
        row, col, value = params["row"], params["column"], params["value"]
        kb.add_fact(Literal(f"cell_{row}_{col}_has_{value}"))
        log(f"[KB] Added fact: cell_{row}_{col}_has_{value}")
        
        # Check board validation
        ok = board.validateIncompleteBoard()
        log(f"[validateIncompleteBoard after write] {ok}")
        feedback += ("\nBoard validation passed." if ok else
                     "\nValidation failed, try again. The board now contains a duplicate "
                     "value; use eraseTile to correct it.")
        
        # Check KB consistency
        kb_consistent = kb.is_consistent()
        log(f"[KB consistency check] {kb_consistent}")
        if not kb_consistent:
            feedback += "\nWARNING: Knowledge base is inconsistent. There may be a logical contradiction."

    filled = count_filled(board)
    solved = name == "validatePuzzle" and result is True

    # advance the controller's tile pointer
    idx = state["current_tile_index"]
    nonwrite = state["nonwrite_calls_on_tile"]
    if name in ("writeTile", "eraseTile"):
        idx, nonwrite = advance_index(board, idx), 0
    else:
        nonwrite += 1
        if nonwrite >= MAX_NONWRITE_CALLS_PER_TILE:
            idx, nonwrite = advance_index(board, idx), 0
    # the current tile may have been filled by a write elsewhere
    t = board.grid[idx // 9][idx % 9]
    if not t.writable or t.value is not None:
        idx = advance_index(board, idx)

    record(state, "user", feedback)
    updates.update(tiles_filled=filled, current_tile_index=idx,
                   nonwrite_calls_on_tile=nonwrite, solved=solved, done=solved)
    return updates


# --------------------------------------------------------------------------
# Routing / graph
# --------------------------------------------------------------------------
def route_after_llm(state: AgentState) -> str:
    return "parse_response"


def route_after_parse(state: AgentState) -> str:
    if state["parsed_call"] is not None:
        return "execute_tool"
    return END if state["total_calls_count"] >= BUDGET else "llm_decision"


def route_after_execute(state: AgentState) -> str:
    if state["done"] or state["total_calls_count"] >= BUDGET:
        return END
    return "llm_decision"


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("llm_decision", llm_decision)
    g.add_node("parse_response", parse_response)
    g.add_node("execute_tool", execute_tool)
    g.set_entry_point("llm_decision")
    g.add_edge("llm_decision", "parse_response")
    g.add_conditional_edges("parse_response", route_after_parse,
                            {"execute_tool": "execute_tool", "llm_decision": "llm_decision", END: END})
    g.add_conditional_edges("execute_tool", route_after_execute,
                            {"llm_decision": "llm_decision", END: END})
    return g.compile()


# --------------------------------------------------------------------------
# Reporting / main
# --------------------------------------------------------------------------
def solve_reference(grid: list[list[int]]) -> list[list[int]] | None:
    """Backtracking solver, used only to score how many agent tiles are correct."""
    g = [row[:] for row in grid]

    def ok(r, c, v):
        if any(g[r][i] == v or g[i][c] == v for i in range(9)):
            return False
        br, bc = r // 3 * 3, c // 3 * 3
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


def final_report(state: AgentState, elapsed_time: float) -> None:
    board = state["board_state"]
    kb = state["knowledge_base"]
    valid_incomplete = board.validateIncompleteBoard()
    valid_complete = board.validatePuzzle()
    kb_consistent = kb.is_consistent()
    ref = solve_reference(sudoku_puzzle)
    correct = None
    if ref:
        correct = sum(1 for r in range(9) for c in range(9)
                      if board.grid[r][c].writable and board.readTile(r, c) == ref[r][c])
    
    print("\n" + "=" * 60 + "\nFINAL REPORT (Version 2 - KB Solver)\n" + "=" * 60)
    print("Final board state:")
    board.printBoard()
    print(f"\nExecution Metrics:")
    print(f"  Total tool calls:              {state['total_calls_count']}")
    print(f"  Invalid tool calls:            {state['invalid_calls_count']}")
    print(f"  Tiles filled by agent:         {state['tiles_filled']}")
    print(f"  Elapsed time:                  {elapsed_time:.2f}s")
    print(f"\nBoard Validation:")
    print(f"  validateIncompleteBoard():     {'PASS' if valid_incomplete else 'FAIL'}")
    print(f"  validatePuzzle():              {'PASS' if valid_complete else 'FAIL'}")
    print(f"\nKnowledge Base Status:")
    print(f"  KB consistency:                {'CONSISTENT' if kb_consistent else 'INCONSISTENT'}")
    print(f"\nPuzzle Solve Status:")
    print(f"  Puzzle solved (100% complete): {state['solved']}")
    if correct is not None:
        print(f"  Tiles correct (vs. solution):  {correct}/81")
    
    # Version 2 success criteria: 100% complete puzzle, valid, and within budget
    success = valid_complete and state['solved'] and kb_consistent
    print(f"\n{'SUCCESS' if success else 'INCOMPLETE'}")
    print(f"  All 81 tiles filled correctly:  {state['solved']}")
    print(f"  validatePuzzle() passed:        {valid_complete}")
    print(f"  KB remained consistent:         {kb_consistent}")
    print("=" * 60)


def main() -> None:
    start_time = time.time()
    board = Board(sudoku_puzzle)
    kb = initialize_knowledge_base(board)
    first = advance_index(board, -1)
    state: AgentState = {
        "board_state": board,
        "knowledge_base": kb,
        "current_tile_index": first,
        "tiles_filled": 0,
        "invalid_calls_count": 0,
        "total_calls_count": 0,
        "agent_messages": [],
        "last_response": "",
        "parsed_call": None,
        "nonwrite_calls_on_tile": 0,
        "done": False,
        "solved": False,
    }
    log(f"Model: {MODEL} | Budget: {BUDGET}")
    log("Initializing Knowledge Base with Sudoku constraints...")
    kb_consistent = kb.is_consistent()
    log(f"KB consistency check: {kb_consistent}")
    board.printBoard()
    # recursion_limit: 3 nodes per call, plus headroom
    final = build_graph().invoke(state, {"recursion_limit": BUDGET * 3 + 20})
    elapsed_time = time.time() - start_time
    final_report(final, elapsed_time)


if __name__ == "__main__":
    main()
