# ReAct Sudoku Solver Agent Requirements

*Last Updated: Version 2.1 - With Agent Autonomy & Tool Tracking*

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

The system prompt establishes:
- **Agent role**: Sudoku constraint solver (board not presented in prompt)
- **Output format**: Single JSON tool call per turn (no explanation or reasoning)
- **Comprehensive solving strategy**: Detailed 5-step workflow for constraint solving

**Prompt Structure:**
- **System message** (sent once): Role, output format, complete 5-step strategy, tool descriptions, and key insights
- **Initial user message**: "Begin solving. Call readBoard to see puzzle state"
- **Agent must call readBoard()** on first move to discover board state
- **Subsequent messages**: Recent message history + "Make your next tool call"

**System Prompt Contents:**
- Step 1: Call getCellCandidates() to identify candidate values
- Step 2: Evaluate candidate list size
- Step 3 (if multiple candidates): Use kb_entails() to test each candidate
- Step 4: Write tiles only when KB entails exactly one candidate
- Step 5: Repeat until puzzle solved or budget exhausted
- Key warning: Avoid duplicate tool calls to maximize budget efficiency

### Agent Autonomy

The agent has **FULL AUTONOMY** in tile selection and exploration strategy:
- No forced tile progression (unlike previous versions)
- No "controller" directing which tile to work on
- Agent chooses freely which tiles to explore and in what order
- Agent controls own exploration depth per tile (no artificial limits)
- Only constraint: 150-call budget enforced globally

### Decision Logic

1. **Board Discovery**: On first move, call readBoard() to see puzzle state
2. **Tile Selection**: Choose any empty, writable tile to work on
3. **Candidate Analysis**:
   - Call getCellCandidates(row, col) to get all legal values
   - Evaluate candidate list size
4. **Candidate Validation**:
   - If exactly 1 candidate: Proceed to write (Step 5)
   - If 0 candidates: Choose different cell
   - If 2-9 candidates: Either use kb_entails() to test each, or move to different cell
5. **Write Decision**: Only write when KB entails exactly one candidate
   - Constraint: **The agent MUST NOT write unless KB entails it as the only possibility**
   - This ensures logical soundness and reduces invalid moves
6. **Board Updates**: When tile is written:
   - KB automatically updates with new fact
   - Constraints propagate to other cells
7. **Completion Check**: When agent believes puzzle solved, call validatePuzzle()
8. **Error Recovery**: If kb_is_consistent() returns false:
   - Indicates logical contradiction
   - Use eraseTile() to remove conflicting values
   - Continue solving

### Validation & Board State

After every write call:
- Call `validateIncompleteBoard()` and report result to agent
- If validation fails, inform agent: "Validation failed, try again"
- Optionally check KB consistency with `kb_is_consistent()` to detect logical conflicts early

`readBoard()` returns only the grid (no metadata about writability or notes).

---

## Tool Call Tracking & Reporting

### Tool Call Recording

Every tool call is tracked in the state's `tool_calls` list with:
- **Type**: One of "executed", "failed", or "rejected"
- **Name**: Tool name (for executed/failed calls)
- **Parameters**: Input parameters (for executed/failed calls)
- **Result/Error**: Result value or error message
- **Call/Reason**: Full JSON and rejection reason (for rejected calls)

### Call Types

- **Executed**: Tool ran successfully and returned a result
- **Failed**: Tool was parsed correctly but threw an exception during execution
- **Rejected**: Tool call was invalid JSON or violated parameter constraints

### Final Report Tool Analysis

At termination, generate comprehensive tool call analysis:
1. Ordered execution log showing all calls in sequence
2. Summary statistics (total, executed, failed, rejected)
3. Per-tool usage breakdown (count for each tool)

This enables analysis of:
- Agent's call patterns and exploration strategy
- Efficiency of tool usage
- Distribution across available tools
- Error rates and failure modes

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
- `tiles_filled`: Count of filled tiles
- `invalid_calls_count`: Separate counter for invalid tool calls
- `total_calls_count`: Total tool calls made
- `agent_messages`: Full trace of LLM responses and tool results
- `tool_calls`: Ordered log of all tool executions with type, name, params, and results
  (tracks executed, failed, and rejected calls for analysis)

### Debug Output
- Print full trace of agent responses to show thought process
- Include every LLM response (valid and invalid)
- Include all tool results
- Show validation outcomes after each write

### Final Report

When agent terminates, print and save a report containing:

**Board and Metrics**:
- Final Board State: Display the solved/final puzzle board
- Total Tool Calls: Number of tool calls made
- Invalid Tool Calls: Number of invalid tool calls
- Tiles Filled: Count of tiles filled by the agent
- Elapsed Time: Total execution time

**Validation Results**:
- validateIncompleteBoard(): Whether board is valid with empty tiles allowed
- validatePuzzle(): Whether board is 100% complete and valid

**Knowledge Base Status**:
- KB Consistency: Whether knowledge base is consistent (no contradictions)

**Tool Call Analysis** (NEW):
- Ordered Execution Log: List of all tool calls in order with parameters
  - Executed calls: toolName(param1,param2,...) -> result
  - Failed calls: toolName(...) [FAILED: error_message]
  - Rejected calls: Full JSON + rejection reason
- Summary Statistics:
  - Total calls made (executed + failed + rejected)
  - Count of executed, failed, and rejected calls
  - Breakdown by tool (count per tool name)

**Puzzle Solve Status**:
- Puzzle Solved: Whether agent completed and validated the puzzle (100% correct)
- Tiles Correct: Number of tiles matching reference solution

**Output File** (NEW):
- Report is written to timestamped file: output/output_YYYY-MM-DD_HH-MM-SS.txt
- Allows persistent analysis of agent behavior across multiple runs

---

## Test Configuration

- **Test Puzzle**: `sudoku_puzzle` in `Example_Board.py` (loaded into `Board` from `board.py`)
- **Model**: `MODELS[0]` from environment variables

---