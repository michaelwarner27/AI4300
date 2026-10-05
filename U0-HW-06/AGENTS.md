# AGENTS.md — U0-HW-06: Alpha-Beta Search for Jetan

Guidance for AI coding agents working in this directory.

## Project Overview

This is a completed U0-HW-05 Jetan (Martian chess) repository extended with the
U0-HW-06 alpha-beta package. The assignment: implement alpha-beta pruning with
two deterministic move-ordering strategies, verify equivalence with exhaustive
minimax at depth 2, and measure move-ordering effects at depth 3.

Everything is local and deterministic. **No MCTS, no LLM calls, no network
access, no randomness** in any HW06 code path. (LLM files like
`llm_client.py`, `direct_llm_agent.py` are HW05 leftovers — leave them alone.)

Python 3.12, standard library only. No build step.

## Where Student Work Goes

| File | Task |
|---|---|
| `alpha_beta_agent.py` | Implement `AlphaBetaAgent._value` (the alpha-beta recursion) |
| `alpha_beta_ordering.py` | Implement `ordering_key_1` and `ordering_key_2` |
| `test_hw06_student.py` | Replace the `test_student_alpha_cutoff_case` placeholder; add more tests as needed |

Already done — do not redo:
- `hw06_config.py` selects `evaluate_position_3` (the final HW05 evaluator). **Correct as-is; do not change.**
- `student_agents.py` already ends with `import hw06_registration`.

## Do NOT Modify

- `jetan.py` (engine; `JetanBoard` is immutable and authoritative)
- `hw06_positions.py` (the six fixed positions — identifiers and definitions)
- `hw06_measured_minimax.py` (measured minimax reference)
- `test_hw06_framework.py`
- The metric definitions or their meanings
- `student_strategies.py` evaluator behavior (`evaluate_position_3` must stay
  unchanged while collecting final evidence; do not copy, wrap, or migrate it)
- Any other HW05 file unless explicitly asked

## Commands

```bash
# Framework tests (pass on the unmodified extension)
python3 -m unittest -v test_hw06_framework.py

# Student tests (fail until student work is complete)
python3 -m unittest -v test_hw06_student.py

# Bounded trial: one depth-3 search (~1 min)
python3 hw06_run_experiments.py --stage B --position reduced --agent alpha_beta_order_1 --repeats 1 --timeout 30 --output hw06_trial_one

# Bounded trial: all Stage B configs once (~7 min)
python3 hw06_run_experiments.py --stage B --repeats 1 --timeout 30 --output hw06_trial_stage_b

# Required full matrix — final evidence (~30 min, 54 attempts)
python3 hw06_run_experiments.py --output hw06_results

# Diagnostic single game (not part of required evidence)
python3 play.py --view text --orange-agent alpha_beta_order_1 --black-agent random --orange-depth 2 --max-plies 20 --max-think-seconds 60
python3 play.py --view text --orange-agent alpha_beta_order_2 --black-agent minimax_3 --orange-depth 2 --black-depth 2 --max-plies 20 --max-think-seconds 60
```

Run all commands from this directory (`U0-HW-06/`). `minimax_3` is the HW05
registration that uses the selected evaluator.

## Alpha-Beta Contract (`AlphaBetaAgent._value`)

- Support every depth ≥ 1 ply. Depth = plies from root; root is depth 0.
- **Check terminal status before the depth cutoff.**
- Terminal states: exact utility (+1, 0, −1 from the root player's perspective).
- Nonterminal cutoff states: call `hw06_config.SELECTED_EVALUATOR` only here.
- Root player's evaluation perspective stays fixed through the whole search.
- Standard MAX/MIN alpha-beta recurrences; stop visiting siblings once the
  bound proves they cannot change the result.
- **Root uses the original `board.actions()` order — no ordering at the root.**
  Apply the experimental ordering at every expanded node below the root.
- Return the **first** canonical root action attaining the best value (strict
  tie handling; never use a pruned bound as an exact value for root ties).
- Out of scope: transposition tables, iterative deepening, quiescence,
  parallelism, evaluator changes.

## Move-Ordering Contract (`ordering_key_1`, `ordering_key_2`)

Interface (via the supplied stable wrapper):
`order_actions(board, actions, perspective) -> tuple[Move, ...]`

Each ordering must:
- return every supplied action exactly once; deterministic for identical input;
- use a stable sort so equal-ranked actions keep canonical input order;
- put likely-cutoff-causing moves first **for the player acting at `board`**
  (works at both MAX and MIN nodes relative to the fixed root perspective);
- differ from the other strategy on at least one supplied position.

May inspect: current board, move properties, one successor state, the selected
evaluator. May NOT: call `board.actions()`, use randomness, wall-clock time,
stored games, hard-coded full positions, or later traversal results.

## Metrics (use exactly these meanings)

| Metric | Definition |
|---|---|
| `visited_states` | Every recursive state entry, incl. terminal/cutoff; root counts once |
| `expanded_nodes` | Every nonterminal, non-cutoff state where actions are generated, incl. root |
| `generated_actions` | Sum of legal-action list lengths at expanded nodes |
| `evaluated_states` | Terminal states + nonterminal cutoff states whose values are returned |
| `pruned_actions` | Entries left in an already generated action list after a cutoff |
| `maximum_depth` | Greatest visited depth in plies (root = 0) |
| `thinking_time` | Wall-clock time in `choose_action`, incl. ordering-key cost |

`expanded_nodes` is the primary ordering comparison; timing is supporting
evidence only.

## Required Alpha-Cutoff Test Case

In `test_hw06_student.py`, replace `test_student_alpha_cutoff_case` with a
depth-2 `ToyBoard` tree that triggers an **alpha cutoff at a MIN node**:
- the first root branch establishes alpha;
- in a later MIN branch, the first child returns a value ≤ alpha, pruning the
  remaining sibling.
No separate beta-cutoff case is required.

## Experiment Evidence Rules

- Stage A: 18 depth-2 searches (minimax + both orderings × 6 positions).
  All three must agree on root value and selected move per position.
- Stage B: 36 depth-3 attempts (both orderings × 6 positions × 3 repeats,
  30 s per-search timeout).
- Final evidence lives in `hw06_results/` — **commit `raw.csv` and
  `summary.csv`**. Directories starting with `hw06_trial` are git-ignored and
  never count as evidence.
- Retain every attempt; record timeouts/errors as-is; use `N/A`, never 0, for
  unavailable values.
- Relative change column: `(order1 − order2) / order1`; positive means
  Ordering 2 expanded fewer nodes. Ordering 1 is a denominator baseline, not
  an unpruned reference.
- If the evaluator or ordering code changes after a final run, re-run the full
  matrix — stale CSVs are invalid evidence.

## Report

Use `U0-HW-06-report-template.md`. The report must identify the selected
evaluator (`evaluate_position_3`) and the U0-HW-05 commit, document both
ordering strategies (ranking procedure, expected cutoff relationship, cost,
tie rule, weak position type), show the alpha-cutoff tree/traversal, answer
the analysis questions from CSV evidence, and include an AI-assistance
disclosure.

Complexity framing: minimax and alpha-beta worst case `O(b^d)` time; ideal
ordering near `O(b^(d/2))`; both use `O(bd)` space. State explicitly that the
assignment did **not** measure memory. Experiments measure search efficiency,
not move quality or game outcomes.
