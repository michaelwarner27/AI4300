# ReAct Sudoku Solver Agent Requirements

## Overview

Build a ReAct agent that solves Sudoku puzzles by making tool calls to read, validate, and modify the board. The agent will not modify the board directly—all changes must happen through tool calls. Implementation will use LangGraph.

### Scope: Version 1 (Prototype)

This is a scrappy prototype primarily designed to test the agent's ability to make tool calls correctly. Success is modest: fill in **10 correct tiles within budget**, and the board must pass validation with those tiles filled.

### Scope: Version 2 (Knowledge Base Solver)

This version adds a propositional logic knowledge base (KB) to reduce LLM calls and guide the agent's decision-making. The agent will encode Sudoku constraints into the KB, then query it to deduce cell values before writing. Success metric: **solve complete puzzles 100% correctly and pass validation**.

---

## Agent Architecture

### Implementation Framework
- **Framework**: LangGraph
- **Model**: Use `MODELS[0]` from `.env` (API_KEY, URL stored in environment)
- **Knowledge Base**: Persistent proplogic KB per puzzle (initialized at start, cleared after puzzle validation or when new puzzle begins)

### LangGraph Node Structure

```
Prompt Message
    ↓
Initialize/Update Knowledge Base (before LLM decision)
    ↓
LLM Decision/Response (only node making API calls)
    ↓
Response Parser (validate tool call format)
    ↓
Execute Tool Call
    ↓
Tool Result + validateIncompleteBoard() (only after writes)
    ↓
Return to LLM Decision → Loop
```

**Knowledge Base Initialization**: At the start of each puzzle, the KB is populated with fundamental Sudoku logic constraints:
- **Cell Constraints**: Each tile contains exactly one value (mutual exclusion: `cell_r_c_has_1 AND NOT cell_r_c_has_2 AND ... AND NOT cell_r_c_has_9`, etc.)
- **Uniqueness Constraints**: For each row, column, and block: no two positions can contain the same value (e.g., `NOT (cell_0_0_has_1 AND cell_0_1_has_1)`)
- **Initial Facts**: Add as facts any clues from the puzzle (e.g., `cell_0_0_has_5` is true for starter tiles)

These constraints are added upfront via `kb_add_clause()` or `kb_add_formula()` to establish the logical foundation, reducing the need for the agent to re-derive basic Sudoku rules.

**Invalid tool calls** loop back to LLM without executing (still count toward budget).
Error message includes: invalid call attempted, list of all tool descriptions, and proper call format.
**validateIncompleteBoard() results** (only after writes) are reported to agent; validation failures inform agent to try again.

---

## Agent Prompt & Behavior

### System Prompt

```
You are a Sudoku solving bot, using the given tools with the current board make tool calls to solve the puzzle.
{Board State}
{Tool descriptions}
Output a single tool call as JSON {"tool": "...", "parameters": ...}
```

The agent receives the current board state in each iteration and must make a single tool call per turn.

### Decision Logic

1. **Tile Traversal**: Start at tile index 0 (top-left). Automatically skip non-writable tiles and already-filled tiles.
2. **Knowledge Base Query**: For the current tile at row `r`, column `c`:
   - Query the KB for each candidate value 1-9: "Does KB entail `cell_r_c_has_v`?"
   - Count how many candidates the KB entails as valid
3. **Write Decision**: Only write when the KB entails exactly one valid candidate for the tile
   - Constraint: **The agent MUST NOT write a value unless the KB entails it as the only possibility**
   - This ensures writes are logically sound and reduces invalid moves
4. **Candidate Discovery**: If fewer than 9 candidates remain, use `kb_entails()` to test each candidate and gather deductions
5. **Board Updates**: When a tile is written:
   - Add it to the KB as a fact: `kb_add_fact({"symbol": "cell_r_c_has_v", "positive": true})`
   - This propagates constraints through the KB for future queries
6. **Loop**: Move to the next tile in reading order (0-80). After tile 80, wrap to tile 0 and continue.
   - The controller owns `current_tile_index` and shows it in the prompt as "Current tile: (row, col)". It is a guide; the agent is not blocked from acting on other tiles.
   - The index advances after a `writeTile` or `eraseTile` call, or after 9 valid non-write calls without advancing (so the agent can explore every candidate on a tile). Non-writable and already-filled tiles are skipped.
7. **Completion Check**: When the agent believes the board is solved, call `validatePuzzle()`
8. **Inconsistency Detection**: If `kb_is_consistent()` returns false at any point, an error state has been reached (contradiction in KB or board state); report to agent and use `eraseTile()` to recover.

### Validation & Board State

After every write call:
- Call `validateIncompleteBoard()` and report result to agent
- If validation fails, inform agent: "Validation failed, try again"
- Optionally check KB consistency with `kb_is_consistent()` to detect logical conflicts early

`readBoard()` returns only the grid (no metadata about writability or notes).

---

## Available Tools

### Tool Call Format

All tool calls must be JSON with this structure:
```json
{"tool": "toolName", "parameters": {"param1": value1, "param2": value2}}
```

### Read/Write Tools

| Tool | Parameters | Purpose |
|------|------------|---------|
| `readBoard()` | `{}` | Returns grid (9x9 list of lists) for agent to analyze |
| `readTile()` | `{"row": 0-8, "column": 0-8}` | Returns int (1-9) or None |
| `readRow()` | `{"row": 0-8}` | Returns list of 9 values from the specified row in left-to-right order; values are 1-9 or None |
| `readColumn()` | `{"column": 0-8}` | Returns list of 9 values from the specified column in top-to-bottom order; values are 1-9 or None |
| `readBlock()` | `{"block": 0-8}` | Returns list of 9 values from the specified block (0-8) in reading order (left to right, top to bottom); values are 1-9 or None |
| `writeTile()` | `{"row": 0-8, "column": 0-8, "value": 1-9}` | Writes value to writable tiles; returns bool |
| `eraseTile()` | `{"row": 0-8, "column": 0-8}` | Erases a tile (sets to None); returns bool |

### Validation Tools

| Tool | Parameters | Purpose |
|------|------------|---------|
| `checkTileCandidate()` | `{"row": 0-8, "column": 0-8, "candidate": 1-9}` | Returns bool: True if the candidate is valid (not already in the tile's row, column, or block), False if it conflicts |
| `validateRow()` | `{"row": 0-8}` | Returns bool: no duplicate non-empty values in row |
| `validateColumn()` | `{"column": 0-8}` | Returns bool: no duplicate non-empty values in column |
| `validateBlock()` | `{"block": 0-8}` | Returns bool: no duplicate non-empty values in block |
| `validateIncompleteBoard()` | `{}` | Validates entire board allowing empty tiles; returns bool |
| `validatePuzzle()` | `{}` | Validates entire board with no empty tiles; returns bool |

### Utility Tools

| Tool | Parameters | Purpose |
|------|------------|---------|
| `locateBlock()` | `{"row": 0-8, "column": 0-8}` | Returns block index (0-8) for given tile location |
| `checkIfInRow()` | `{"row": 0-8, "value": 1-9}` | Returns bool: whether value exists in row |
| `checkIfInColumn()` | `{"column": 0-8, "value": 1-9}` | Returns bool: whether value exists in column |
| `checkIfInBlock()` | `{"block": 0-8, "value": 1-9}` | Returns bool: whether value exists in block |

### Propositional Logic Tools

| Tool | Parameters | Purpose |
|------|------------|---------|
| `kb_add_formula()` | `{"formula": "string"}` | Add a formula to the knowledge base (parsed and converted to CNF). Useful for encoding Sudoku constraints like "cell_0_0_must_be_1 OR cell_0_0_must_be_2 OR ...". Returns bool: True if added successfully |
| `kb_add_clause()` | `{"literals": [{"symbol": "string", "positive": bool}, ...]}` | Add a disjunctive clause (OR) of literals directly to the knowledge base when constraints are already in CNF form. More efficient than formulas. Returns bool: True if added successfully |
| `kb_add_fact()` | `{"symbol": "string", "positive": bool}` | Add a definite fact to the knowledge base. Use when the agent deduces that a specific cell has a specific value (e.g., cell_0_0_has_value_5 is True). Returns bool: True if added successfully |
| `kb_entails()` | `{"query": "string"}` | Query the knowledge base to check if a deduction is entailed. Use to discover which cell values can be logically deduced from current constraints (e.g., "Does KB entail that cell_1_5_has_value_3?"). Returns bool: True if KB entails the query |
| `kb_is_consistent()` | `{}` | Check if the knowledge base is consistent (contains no contradictions). Essential for validating that partial Sudoku solutions remain valid. Returns bool: True if consistent, False if contradictions exist |

---

## Execution Constraints

### Tool Call Budget
- **Total Budget**: 150 tool calls
- **Tracking**: Maintain separate counters for invalid and total calls
- **Invalid Calls**: Count toward budget

### Termination Conditions

1. **Puzzle Solved**: Agent calls `validatePuzzle()` and receives success
2. **Budget Exhausted**: Reaches 150 total tool calls

### Success Criteria

- **Primary**: Agent solves the puzzle **100% completely** with all 81 tiles filled correctly
- **Validation**: Final board must pass `validatePuzzle()` (complete and valid)
- **Secondary**: Puzzle must be solved **within the tool call budget** (150 calls)
- **Logic Soundness**: All writes must be preceded by KB entailment queries; no writes without KB justification

---

## Error Handling

### Invalid Tool Calls
- Provide agent with list of allowed tools
- Allow agent to retry
- Invalid calls count toward budget
- Track separately in `invalid_calls_count`

### Invalid Puzzle State
- Agent should detect inconsistencies
- Use `eraseTile()` to remove conflicting values and recover
- Continue solving

---

## Agent State & Observability

### Stored State Variables
- `board_state`: Current puzzle board
- `knowledge_base`: Proplogic KnowledgeBase instance (persistent per puzzle)
- `current_tile_index`: Position tracking through empty tiles
- `tiles_filled`: Count of filled tiles
- `invalid_calls_count`: Separate counter for invalid tool calls
- `total_calls_count`: Total tool calls made
- `agent_messages`: Full trace of LLM responses and tool results

### Debug Output
- Print full trace of agent responses to show thought process
- Include every LLM response (valid and invalid)
- Include all tool results
- Show validation outcomes after each write

### Final Report
When agent terminates, print a report containing:
- **Final Board State**: Display the solved/final puzzle board
- **Total Tool Calls**: Number of tool calls made
- **Invalid Tool Calls**: Number of invalid tool calls
- **Tiles Filled**: Count of tiles filled by the agent
- **Board Validation**: Whether `validateIncompleteBoard()` and `validatePuzzle()` pass for final state
- **KB Consistency**: Final state of knowledge base consistency
- **Puzzle Solved**: Whether agent successfully completed and validated the puzzle (100% correct)

---

## Test Configuration

- **Test Puzzle**: `sudoku_puzzle` in `Example_Board.py` (loaded into `Board` from `board.py`)
- **Model**: `MODELS[0]` from environment variables

---