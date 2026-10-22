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
import openai

from board import Board
from Example_Board import sudoku_puzzle
from proplogic import KnowledgeBase, Literal, atom

BUDGET = 350
HISTORY_WINDOW = 40  # most recent 20 messages = ~10 tool calls (assistant response + user feedback)
SUCCESS_TILES = 10


# --------------------------------------------------------------------------
# Environment / LLM access
# --------------------------------------------------------------------------
load_dotenv()
API_KEY = os.environ["API_KEY"]
URL = os.environ["URL"].rstrip("/")
MODEL = os.environ["MODELS"].split(",")[0].strip()  # MODELS[0]

client = openai.OpenAI(api_key=API_KEY, base_url=URL, timeout=120)


def call_llm(messages) -> str:
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
     "getCellCandidates": ({"row": (0, 8), "column": (0, 8)},
                            "Returns a list of all legally placeable values (1-9) for the given cell based on Sudoku constraints.",
                            lambda b, row, column: b.getCandidates(row, column)),
     "appendNote": ({"row": (0, 8), "column": (0, 8), "values": (0, 0)},  # special: list param
                    "Appends notes to a tile's notes list. Parameter 'values' is a list of integers (1-9).",
                    lambda b, row, column, values: (b.appendNote(row, column, values), True)[1]),
     "removeNote": ({"row": (0, 8), "column": (0, 8), "values": (0, 0)},  # special: list param
                    "Removes notes from a tile's notes list. Parameter 'values' is a list of integers (1-9). Returns true if all values were successfully removed, false if any didn't exist.",
                    lambda b, row, column, values: b.removeNote(row, column, values)),
     "listNotes": ({"row": (0, 8), "column": (0, 8)},
                   "Returns the list of notes for the tile at the specified location.",
                   lambda b, row, column: b.listNotes(row, column)),
     # Disabled tools - use getCellCandidates and kb_entails for constraint solving
    # "checkTileCandidate": ({"row": (0, 8), "column": (0, 8), "candidate": (1, 9)},
    #                        "Returns true if the candidate is valid for the tile (not already in its "
    #                        "row, column or block), false if it conflicts.",
    #                        lambda b, row, column, candidate: b.checkTileCandidate(row, column, candidate)),
    # "validateRow": ({"row": (0, 8)}, "Returns true if the row has no duplicate values.",
    #                 lambda b, row: b.validateRow(row)),
    # "validateColumn": ({"column": (0, 8)}, "Returns true if the column has no duplicate values.",
    #                    lambda b, column: b.validateColumn(column)),
    # "validateBlock": ({"block": (0, 8)}, "Returns true if the block has no duplicate values.",
    #                   lambda b, block: b.validateBlock(block)),
    "validateIncompleteBoard": ({}, "Returns true if the board has no duplicates (empty tiles allowed).",
                                 lambda b: b.validateIncompleteBoard()),
    "validatePuzzle": ({}, "Returns true if the board is completely filled and valid. "
                            "Call this when you believe the puzzle is complete.",
                        lambda b: b.validatePuzzle()),
    # "locateBlock": ({"row": (0, 8), "column": (0, 8)}, "Returns the block index (0-8) of the tile.",
    #                  lambda b, row, column: b.locateBlock(row, column)),
    # "checkIfInRow": ({"row": (0, 8), "value": (1, 9)}, "Returns true if value exists in the row.",
    #                  lambda b, row, value: b.checkIfInRow(row, value)),
    # "checkIfInColumn": ({"column": (0, 8), "value": (1, 9)}, "Returns true if value exists in the column.",
    #                     lambda b, column, value: b.checkIfInColumn(column, value)),
    # "checkIfInBlock": ({"block": (0, 8), "value": (1, 9)}, "Returns true if value exists in the block.",
    #                    lambda b, block, value: b.checkIfInBlock(block, value)),
    # Note: KB tools require special handling in execute_tool() to access KB from state
    # "kb_add_formula": ({"formula": (0, 0)},  # special: string param, validation handled separately
    #                    "Add a formula to the knowledge base (parsed and converted to CNF). Useful for encoding Sudoku constraints. Returns true if added successfully.",
    #                    lambda b, formula: True),  # implemented in execute_tool()
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


def tool_list() -> str:
    """Return a simple list of available tool names (no descriptions)."""
    return "\n".join(f"- {name}" for name in TOOLS.keys())


CALL_FORMAT = '{"tool": "toolName", "parameters": {"param1": value1, "param2": value2}}'


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------
class AgentState(TypedDict):
    board_state: Board
    knowledge_base: KnowledgeBase
    tiles_filled: int
    invalid_calls_count: int
    total_calls_count: int
    agent_messages: list[dict]   # full trace: {"role", "content"}
    tool_calls: list[dict]        # track all tool calls: {"type", "name", "params", "result"}
    # internal plumbing between nodes
    last_response: str
    parsed_call: dict | None
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


def render_board(board: Board) -> str:
    """Render the board as a formatted string with block separators."""
    out = ["   0 1 2 | 3 4 5 | 6 7 8", "   ------+-------+------"]
    for r in range(9):
        row_str = f"{r} | "
        for c in range(9):
            v = board.readTile(r, c)
            row_str += "." if v is None else str(v)
            row_str += " "
            # Add separators between blocks
            if c == 2 or c == 5:
                row_str += "| "
        out.append(row_str)
        # Add separators between block rows
        if r == 2 or r == 5:
            out.append("  ------+-------+------")
    return "\n".join(out)


def build_system_prompt() -> str:
    """
    Build the system prompt with foundational instructions, strategy, and tool reference.
    This is sent once and establishes the agent's role and behavior.
    """
    return (
        "# Sudoku Constraint Solver Agent\n\n"
        "## ROLE\n"
        "You are a Sudoku constraint solver. Your goal: for each empty cell, find and write the correct value using logical reasoning.\n"
        "Do NOT try to solve the entire puzzle at once. Focus on one cell at a time.\n\n"
        "## OUTPUT FORMAT\n"
        "You must output ONLY a single JSON tool call and nothing else. No explanation, reasoning, or text.\n"
        f"Format: {CALL_FORMAT}\n\n"
        "## THE KNOWLEDGE BASE (KB)\n"
        "Your primary engine is a Propositional Logic Knowledge Base.\n"
        "1. **Pre-loaded:** The KB is already initiated with the fundamental rules of Sudoku and the starting board state.\n"
        "2. **Auto-updating:** Whenever you call `writeTile()`, the KB is automatically updated with that new absolute fact and propagates the constraints. You do not need to manually add solved cells to the KB.\n\n"
        "## PHASE 0: PREREQUISITE - BUILD THE KNOWLEDGE BASE (First ~20 Calls)\n"
        "Invest your first ~20 tool calls in strengthening the knowledge base with narrow constraints before solving individual cells.\n\n"
         "### Phase 0 Strategy: Board Scanning & Clause Addition\n"
         "1. Scan rows, columns, and blocks. For each unit (row, column, or block), call `listNotes(row, column)` for each empty tile to see its tracked candidates.\n"
         "2. After reading all empty tiles in a unit, exhaustively add clauses for ALL possible candidate values (1-9):\n"
         "   - For EACH candidate value in that unit, check which empty cells can have it.\n"
         "   - If multiple cells in the unit can have the value, call `kb_add_clause()` to add that constraint to the KB.\n"
         "   - Do NOT skip any candidate values—process ALL values that appear in that unit's empty cells.\n"
         "3. Example workflow for row 5:\n"
         "   - `listNotes(5, 1)` → [2, 4, 6]\n"
         "   - `listNotes(5, 2)` → [3, 6]\n"
         "   - `listNotes(5, 7)` → [1, 6]\n"
         "   - For value 1: only cell (5,7) can have it → `writeTile(5, 7, 1)` (immediate solution).\n"
         "   - For value 2: only cell (5,1) can have it → `writeTile(5, 1, 2)` (immediate solution).\n"
         "   - For value 3: only cell (5,2) can have it → `writeTile(5, 2, 3)` (immediate solution).\n"
         "   - For value 4: only cell (5,1) can have it (already filled, skip if so).\n"
         "   - For value 6: cells (5,1), (5,2), and (5,7) can have it → `kb_add_clause((cell_5_1_has_6 OR cell_5_2_has_6 OR cell_5_7_has_6))`.\n"
         "4. Repeat this exhaustive process for other rows, columns, and blocks.\n\n"
        "### Phase 0 Benefits\n"
        "- Each `kb_add_clause()` encodes a constraint that the KB cannot easily derive on its own.\n"
        "- These clauses enable `kb_entails()` queries to find solutions in cells with 2-9 candidates.\n"
        "- The upfront investment reduces total tool calls needed to solve the puzzle.\n"
        "- You may solve many tiles immediately when a constraint unit has a value with only one option.\n\n"
        "### How to Call kb_add_clause()\n"
        "- Parameter: literals (list of objects with 'symbol' and 'positive' fields)\n"
        "- Example JSON call for row constraint:\n"
        "  {\"tool\": \"kb_add_clause\", \"parameters\": {\"literals\": [{\"symbol\": \"cell_2_1_has_5\", \"positive\": true}, {\"symbol\": \"cell_2_4_has_5\", \"positive\": true}, {\"symbol\": \"cell_2_7_has_5\", \"positive\": true}]}}\n\n"
        "## SOLVER WORKFLOW\n\n"
        "### STEP 1: TARGET & FILTER\n"
        "- Pick a row, column, or block (prioritize ones that are mostly full).\n"
        "- Call `listNotes(row, column)` for an empty tile to see its tracked candidates.\n"
        "- If the list has EXACTLY ONE value: proceed directly to STEP 4.\n"
        "- If the list has MULTIPLE values: proceed to STEP 2.\n\n"
         "### STEP 2: STRENGTHEN THE KB (Exhaustive Constraint Addition)\n"
         "Before using entailment, help the KB understand all constraints from your unit analysis.\n"
         "- Call `listNotes()` for all empty cells in the same unit (row/col/block) to gather all candidates.\n"
         "- For EVERY candidate value that appears in the unit's empty cells, check which cells can have it.\n"
         "- If multiple cells in the unit can have the value, call `kb_add_clause()` to add that constraint to the KB.\n"
         "- Process all values exhaustively—do not skip candidates just because there are many of them.\n"
         "- Call `kb_add_clause()`. Example for a row constraint where 5 can be in col 2 or 8:\n"
         "  {\"tool\": \"kb_add_clause\", \"parameters\": {\"literals\": [{\"symbol\": \"cell_5_2_has_5\", \"positive\": true}, {\"symbol\": \"cell_5_8_has_5\", \"positive\": true}]}}\n"
         "- Or for broader constraints, such as 6 being in one of 8 cells in a row:\n"
         "  {\"tool\": \"kb_add_clause\", \"parameters\": {\"literals\": [{\"symbol\": \"cell_5_1_has_6\", \"positive\": true}, {\"symbol\": \"cell_5_2_has_6\", \"positive\": true}, {\"symbol\": \"cell_5_3_has_6\", \"positive\": true}, ...]}}\n"
         "- Any such clause allows the KB to perform advanced deductions.\n\n"
        "### STEP 3: LOGICAL DEDUCTION (Positive & Negative Entailment)\n"
        "Once the KB is primed with local constraints, query it for cells with multiple tracked candidates.\n"
        "- **Positive Entailment:** Call `kb_entails()` with query `cell_{row}_{col}_has_{value}`.\n"
        "  - If TRUE: The KB logically guarantees this is the answer. Proceed to STEP 4.\n"
        "- **Negative Entailment:** Call `kb_entails()` with a negative query: use the symbol `cell_{row}_{col}_has_{value}` with `positive: false`.\n"
        "  - If TRUE: The KB logically proves this value is IMPOSSIBLE here. The value can be eliminated from this cell's candidates.\n"
        "  - Call `removeNote(row, column, value)` to permanently remove this candidate from the cell's notes.\n"
        "  - Repeat for other candidates. If you eliminate all but one, proceed to STEP 4 with the remaining candidate.\n"
        "- **Review Candidates:** Use `listNotes(row, column)` to see the current tracked candidates after elimination.\n\n"
        "### STEP 4: WRITE THE TILE\n"
        "- Call `writeTile(row, column, value)` ONLY when you have proven a cell has exactly one valid candidate via:\n"
        "  - `listNotes()` showing exactly one candidate, OR\n"
        "  - Positive `kb_entails()` confirming the value, OR\n"
        "  - Negative entailment eliminating all other candidates via `removeNote()`.\n"
        "- You'll need to remove that candidate from intersecting cells."
        "- Remember: The KB updates automatically after this call. Adding the value as a fact.\n\n"
        "### STEP 5: REPEAT\n"
        "- Move to the next empty cell or unit.\n"
        "- When all 81 tiles are filled, call `validatePuzzle()`.\n"
        "- If a logical conflict occurs, call `kb_is_consistent()` to check for contradictions.\n\n"
        "## CANDIDATE TRACKING WITH NOTES\n"
        "The notes on each tile are pre-populated with all legally placeable candidates at puzzle start.\n"
        "- **Review candidates:** Use `listNotes(row, column)` to see which values are still possible for a cell.\n"
        "- **Eliminate candidates:** When `kb_entails()` with a negative query proves a value is impossible, use `removeNote(row, column, value)` to eliminate it.\n"
        "- **Persistent elimination:** Removed candidates stay removed for future queries on that cell, allowing you to progressively narrow down options.\n"
        "- **Combine with entailment:** After eliminating candidates via negative entailment, use `kb_entails()` with positive queries on the remaining candidates to find definite solutions.\n\n"
        "## CRITICAL RULES\n"
        "- **No Duplicate Calls:** Check conversation history. Do NOT call the same tool with the exact same parameters twice in a row.\n"
        "- **No Guessing:** You must strictly prove a value is the only valid option before calling `writeTile()`. Adding clauses via `kb_add_clause()` strengthens reasoning but does not authorize writing by itself.\n\n"
         "## KEY INSIGHTS\n"
         "- Every constraint you encode via `kb_add_clause()` helps the KB make better deductions.\n"
         "- Both broad constraints (a value across many cells) and narrow constraints (a value across few cells) strengthen the KB.\n"
         "- Use notes to track progress, negative entailment to eliminate impossibilities, and positive entailment to confirm solutions.\n"
         "- The combination of `listNotes()`, `removeNote()`, and `kb_entails()` enables powerful constraint propagation.\n\n"
        "## AVAILABLE TOOLS\n"
        f"{tool_descriptions()}\n"
    )


def build_initial_message(state: AgentState) -> str:
    """Build the initial user message for the first API call."""
    return "Begin solving. Call readBoard to see the puzzle state and start your first tool call."


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
    
    # Build messages: system prompt first, then history window, then current user prompt
    system_msg = {"role": "system", "content": build_system_prompt()}
    
    if n == 1:
        # First call: system + initial prompt
        messages = [
            system_msg,
            {"role": "user", "content": build_initial_message(state)}
        ]
    else:
        # Subsequent calls: system + recent history + continuation prompt
        messages = [system_msg] + history[-HISTORY_WINDOW:]
        if not history or history[-1]["role"] == "assistant":
            messages.append({"role": "user", "content": "Make your next tool call."})

    log(f"\n===== Call {n}/{BUDGET} | filled {state['tiles_filled']} =====")
    response = call_llm(messages)
    log(f"[LLM response]\n{response}")
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

    # Track rejected call
    invalid = state["invalid_calls_count"] + 1
    attempted = json.dumps(call) if call is not None else state["last_response"].strip()
    state["tool_calls"].append({
        "type": "rejected",
        "call": attempted,
        "reason": reason
    })
    
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
    
    # Execute tool and track for failures
    try:
        # Handle proplogic tools specially (they need access to KB from state)
        if name == "kb_add_formula":
            kb.add_formula(params["formula"])
            result = True
        elif name == "kb_add_clause":
            # Convert list of {symbol, positive} dicts to Literal objects
            literals = [Literal(lit["symbol"], lit["positive"]) for lit in params["literals"]]
            kb.add_clause(*literals)
            result = True
        elif name == "kb_add_fact":
            kb.add_fact(Literal(params["symbol"], params["positive"]))
            result = True
        elif name == "kb_entails":
            result = kb.entails(params["query"])
        elif name == "kb_is_consistent":
            result = kb.is_consistent()
        else:
            # Non-proplogic tools use the standard tool function
            result = TOOLS[name][2](board, **params)
        
        execution_failed = False
    except Exception as e:
        result = None
        execution_failed = True
        error_msg = str(e)
    
    log(f"**{name}**({params}) -> {result}")

    feedback = f"Tool result for {name}({json.dumps(params)}): {json.dumps(result)}"
    updates: dict = {}

    if name == "writeTile":
        # Add the written tile to the KB as a fact
        row, col, value = params["row"], params["column"], params["value"]
        kb.add_fact(Literal(f"cell_{row}_{col}_has_{value}"))
        
        # Check board validation
        ok = board.validateIncompleteBoard()
        if not ok:
            log(f"[validateIncompleteBoard after write] {ok}")
        feedback += ("\nBoard validation passed." if ok else
                     "\nValidation failed, try again. The board now contains a duplicate "
                     "value; use eraseTile to correct it.")
        
        # Check KB consistency
        kb_consistent = kb.is_consistent()
        if not kb_consistent:
            log(f"[KB consistency check] {kb_consistent}")
        if not kb_consistent:
            feedback += "\nWARNING: Knowledge base is inconsistent. There may be a logical contradiction."
    
    elif name == "eraseTile":
        # Remove all value facts for this tile from the KB
        row, col = params["row"], params["column"]
        for value in range(1, 10):
            # Add negative facts to the KB to indicate these values are no longer there
            kb.add_fact(Literal(f"cell_{row}_{col}_has_{value}", False))

    filled = count_filled(board)
    solved = name == "validatePuzzle" and result is True

    # Log board state every 5 tiles filled
    prev_filled = state["tiles_filled"]
    if filled > 0 and filled % 5 == 0 and filled != prev_filled:
        log(f"\n--- Board state after {filled} tiles filled ---")
        log(render_board(board))

    # Track tool call (successful or failed)
    if execution_failed:
        state["tool_calls"].append({
            "type": "failed",
            "name": name,
            "params": params,
            "error": error_msg
        })
    else:
        state["tool_calls"].append({
            "type": "executed",
            "name": name,
            "params": params,
            "result": result
        })
    
    record(state, "user", feedback)
    updates.update(tiles_filled=filled, solved=solved, done=solved)
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


def format_params(params: dict) -> str:
    """Format parameters as a simple string: param1,param2,param3"""
    if not params:
        return ""
    return ",".join(str(v) for v in params.values())


def generate_tool_calls_report(state: AgentState) -> tuple[str, dict]:
    """
    Generate a detailed report of all tool calls and return (report_text, summary_dict)
    Report includes:
    - Ordered list of all tool calls with parameters
    - Count of each tool type
    - Categorization by status (executed, failed, rejected)
    """
    tool_calls = state["tool_calls"]
    
    # Build the ordered list section
    lines = ["TOOL CALLS - ORDERED EXECUTION LOG:\n"]
    lines.append("=" * 70)
    
    for i, call in enumerate(tool_calls, 1):
        if call["type"] == "executed":
            tool_name = call["name"]
            params_str = format_params(call["params"])
            result = call["result"]
            lines.append(f"{i}. {tool_name}({params_str}) -> {result}")
        elif call["type"] == "failed":
            tool_name = call["name"]
            params_str = format_params(call["params"])
            error = call["error"]
            lines.append(f"{i}. {tool_name}({params_str}) [FAILED: {error}]")
        elif call["type"] == "rejected":
            call_json = call["call"]
            reason = call["reason"]
            lines.append(f"{i}. [REJECTED] {call_json}")
            lines.append(f"   Reason: {reason}")
    
    lines.append("=" * 70)
    
    # Count statistics
    executed_count = sum(1 for c in tool_calls if c["type"] == "executed")
    failed_count = sum(1 for c in tool_calls if c["type"] == "failed")
    rejected_count = sum(1 for c in tool_calls if c["type"] == "rejected")
    
    # Tool usage summary (only for executed calls)
    tool_usage = {}
    for call in tool_calls:
        if call["type"] == "executed":
            tool_name = call["name"]
            tool_usage[tool_name] = tool_usage.get(tool_name, 0) + 1
    
    lines.append("\nTOOL CALL SUMMARY:\n")
    lines.append(f"Total calls made: {len(tool_calls)}")
    lines.append(f"  - Executed: {executed_count}")
    lines.append(f"  - Failed: {failed_count}")
    lines.append(f"  - Rejected: {rejected_count}")
    
    lines.append("\nCALLS BY TOOL:\n")
    for tool_name in sorted(tool_usage.keys()):
        count = tool_usage[tool_name]
        lines.append(f"  {tool_name}: {count}")
    
    report_text = "\n".join(lines)
    summary_dict = {
        "total": len(tool_calls),
        "executed": executed_count,
        "failed": failed_count,
        "rejected": rejected_count,
        "by_tool": tool_usage
    }
    
    return report_text, summary_dict


def final_report(state: AgentState, elapsed_time: float, puzzle=None, quiet: bool = False):
    board = state["board_state"]
    kb = state["knowledge_base"]
    valid_incomplete = board.validateIncompleteBoard()
    valid_complete = board.validatePuzzle()
    kb_consistent = kb.is_consistent()
    puzzle = puzzle if puzzle is not None else sudoku_puzzle
    ref = solve_reference(puzzle)
    correct = None
    if ref:
        correct = sum(1 for r in range(9) for c in range(9)
                      if board.grid[r][c].writable and board.readTile(r, c) == ref[r][c])
    
    # Generate tool calls report
    tool_calls_report, tool_summary = generate_tool_calls_report(state)
    
    # Build full report text
    report_lines = []
    report_lines.append("\n" + "=" * 60 + "\nFINAL REPORT (Version 2 - KB Solver)\n" + "=" * 60)
    report_lines.append("Final board state:")
    
    # Capture board output
    board_output = []
    for r in range(9):
        row_str = f"{r} | "
        for c in range(9):
            v = board.readTile(r, c)
            row_str += "." if v is None else str(v)
            row_str += " "
            if c == 2 or c == 5:
                row_str += "| "
        board_output.append(row_str)
        if r == 2 or r == 5:
            board_output.append("  ------+-------+------")
    board_output.insert(0, "   0 1 2 | 3 4 5 | 6 7 8")
    board_output.insert(1, "   ------+-------+------")
    
    report_lines.append("\n".join(board_output))
    report_lines.append(f"\nExecution Metrics:")
    report_lines.append(f"  Total tool calls:              {state['total_calls_count']}")
    report_lines.append(f"  Invalid tool calls:            {state['invalid_calls_count']}")
    report_lines.append(f"  Tiles filled by agent:         {state['tiles_filled']}")
    report_lines.append(f"  Elapsed time:                  {elapsed_time:.2f}s")
    report_lines.append(f"\nBoard Validation:")
    report_lines.append(f"  validateIncompleteBoard():     {'PASS' if valid_incomplete else 'FAIL'}")
    report_lines.append(f"  validatePuzzle():              {'PASS' if valid_complete else 'FAIL'}")
    report_lines.append(f"\nKnowledge Base Status:")
    report_lines.append(f"  KB consistency:                {'CONSISTENT' if kb_consistent else 'INCONSISTENT'}")
    report_lines.append(f"\nPuzzle Solve Status:")
    report_lines.append(f"  Puzzle solved (100% complete): {state['solved']}")
    if correct is not None:
        report_lines.append(f"  Tiles correct (vs. solution):  {correct}/81")
    
    # Version 2 success criteria: 100% complete puzzle, valid, and within budget
    success = valid_complete and state['solved'] and kb_consistent
    report_lines.append(f"\n{'SUCCESS' if success else 'INCOMPLETE'}")
    report_lines.append(f"  All 81 tiles filled correctly:  {state['solved']}")
    report_lines.append(f"  validatePuzzle() passed:        {valid_complete}")
    report_lines.append(f"  KB remained consistent:         {kb_consistent}")
    report_lines.append("=" * 60)
    
    # Add tool calls report
    report_lines.append("\n" + tool_calls_report)
    
    # Combine and print
    full_report = "\n".join(report_lines)
    if not quiet:
        print(full_report)
    
    # Write to timestamped file
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_dir = "output"
    os.makedirs(output_dir, exist_ok=True)
    output_file = f"{output_dir}/output_{timestamp}.txt"
    
    try:
        with open(output_file, "w") as f:
            f.write(full_report)
        print(f"\n✓ Report written to: {output_file}")
    except Exception as e:
        print(f"\n✗ Error writing to file: {e}")
        output_file = None
    return full_report, output_file


def run_agent(puzzle=None, budget=None):
    """Run the agent on `puzzle` (default: sudoku_puzzle).

    Returns (final_state, elapsed_seconds, report_text, report_path).
    """
    global BUDGET
    puzzle = puzzle if puzzle is not None else sudoku_puzzle
    if budget is not None:
        BUDGET = budget
    start_time = time.time()
    board = Board(puzzle)
    kb = initialize_knowledge_base(board)
    state: AgentState = {
        "board_state": board,
        "knowledge_base": kb,
        "tiles_filled": 0,
        "invalid_calls_count": 0,
        "total_calls_count": 0,
        "agent_messages": [],
        "tool_calls": [],
        "last_response": "",
        "parsed_call": None,
        "done": False,
        "solved": False,
    }
    log(f"Model: {MODEL} | Budget: {BUDGET}")
    log("Initializing Knowledge Base with Sudoku constraints...")
    log(f"KB consistency check: {kb.is_consistent()}")
    board.printBoard()
    final = build_graph().invoke(state, {"recursion_limit": BUDGET * 3 + 20})
    elapsed_time = time.time() - start_time
    text, path = final_report(final, elapsed_time, puzzle=puzzle)
    return final, elapsed_time, text, path


def main() -> None:
    run_agent(sudoku_puzzle)


if __name__ == "__main__":
    main()
