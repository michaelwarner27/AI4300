# ReAct Sudoku Solver Agent Requirements

## Overview

Build a ReAct agent that solves Sudoku puzzles by making tool calls to read, validate, and modify the board. The agent will not modify the board directly—all changes must happen through tool calls. Implementation will use LangGraph.

### Scope: Version 1 (Prototype)

This is a scrappy prototype primarily designed to test the agent's ability to make tool calls correctly. Success is modest: fill in **10 correct tiles within budget**, and the board must pass validation with those tiles filled.

---

## Agent Architecture

### Implementation Framework
- **Framework**: LangGraph
- **Model**: Use `MODELS[0]` from `.env` (API_KEY, URL stored in environment)

### LangGraph Node Structure

```
Prompt Message
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

1. **Tile Traversal**: Start at tile index 0 (top-left). Automatically skip non-writable tiles.
2. **Constraint Analysis**: Decide if the tile is constrained enough (only one candidate) to write
3. **Write Decision**: Only write when there is exactly one valid candidate left
4. **Candidate Finding**: Use either `readBoard()` to analyze the full board or `checkTileCandidate()` to test candidates
5. **Loop**: Move to the next tile in reading order (0-80). After tile 80, wrap to tile 0 and continue.
6. **Completion Check**: When the agent believes the board is solved, call `validatePuzzle()`
   - The controller owns `current_tile_index` and shows it in the prompt as "Current tile: (row, col)". It is a guide; the agent is not blocked from acting on other tiles.
   - The index advances after a `writeTile` or `eraseTile` call, or after 9 valid non-write calls without advancing (so the agent can test every candidate on a tile). Non-writable and already-filled tiles are skipped.
7. **Stuck State**: Agent should continue reading and checking tiles through entire budget. There is no stuck detection—let budget enforce termination.

### Validation & Board State

After every write call:
- Call `validateIncompleteBoard()` and report result to agent
- If validation fails, inform agent: "Validation failed, try again"

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

- **Minimum**: Fill in **10 correct tiles** and pass `validateIncompleteBoard()` validation
- **Expectation**: Agent may not solve the puzzle completely; prototype focuses on correct tool usage
- **Board State**: Must be valid (pass checkIncompleteBoard) with ≥10 filled tiles

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
- `current_tile_index`: Position tracking through empty tiles
- `tiles_filled`: Count of filled tiles (success metric)
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
- **Board Validation**: Whether `validateIncompleteBoard()` passes for final state

---

## Test Configuration

- **Test Puzzle**: `sudoku_puzzle` in `Example_Board.py` (loaded into `Board` from `board.py`)
- **Model**: `MODELS[0]` from environment variables

---

## Future: Version 2

Propositional logic tools will be added to aid in decision-making beyond simple constraint checking.
