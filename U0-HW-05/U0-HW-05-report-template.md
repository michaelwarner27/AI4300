# Designing and Evaluating Jetan Agents

**Student:** [Michael Warner]<br>
**Private repository:** [URL]<br>
**Access:** [Confirm `fractal13` has read access]<br>
**Submitted commit:** [Hash]

## 1. Deterministic Evaluation Functions

### Reading-Based Design Principles

[Cite at least one assigned paper. Explain the principles you used and one
principle you did not adopt.]

### Evaluation 1

**Definition:** [0.99 * tanh(material_advantage / 20)]<br>
**Information used and intentionally ignored:** [The value of each surviving piece as described in the wikipedia page. Using 19 for the Chief and 21 for the princess. Ignores piece mobility, position, and present danger of attack. ]<br>
**Scaling/range:** [Piece value ranges 0-21 and is scaled down to -1 & 1. ]<br>
**Expected relationship to utility:** [Important for utility because winning is largely a matter of retaining more pieces than your opponent so this will aid in finding a win. ]<br>
**Computational cost:** [A single pass over all 100 squares of the board. ]<br>
**Known weakness:** [Blind to danger, it cannot see when its pieces are threatened and will trade its princess for marginal gain. ]

### Evaluation 2 and Motivation

**Definition:** [0.99 * tanh((material + 0.2 * move_advantage + 0.15 * advance_advantage) / 20)]<br>
**Information used and intentionally ignored:** [In addition to the piece values used in eval 1, eval 2 uses piece position to account for mobility. It still ignores piece safety and royal safety. ]<br>
**Scaling/range:** [Mobility is based on the distance away from starting rank plus the number of legal moves from current position.]<br>
**Expected relationship to utility:** [This greatly adds to the utility because developing your pieces is a fundamental strategy of chess and Jetan. Pieces are unlikely to be able to make a capture if they are stuck on their starting rank. ]<br>
**Computational cost:** [One pass over all 100 squares of the board plus two legal move enumeration. ]<br>
**Known weakness:** [It still is blind to attack. It fails to recognize the difference between a free and contested attack. ]<br>
**Revision evidence:** [Added piece mobility to this eval because eval one didn't move pieces with any purpose. Eval two now intentionally develops the pieces into better board position.]

### Evaluation 3 and Motivation

**Definition:** [0.99 * tanh((material + 0.2 * move_advantage + 0.15 * advance_advantage + 0.5 * threat_advantage + 1.0 * escape_advantage) / 20)]<br>
**Information used and intentionally ignored:** [Eval 3 adds the ability to see the difference between a free attack vs an attack that is merely threatened. Additionally it can now see when the princess is threatened. It also adds a term for when the princess' escape is spent. It doesn't have any built in game strategies like forks or pins though.]<br>
**Scaling/range:** [Still scaled to be bounded by -1 & 1. Threats are scaled off of piece value and using a princess escape subtracts 1 from the position score.]<br>
**Expected relationship to utility:** [This eval is able to distinguish positions where a princess has spent it's escape which changes a seemingly even board to a very clearly advantaged one.  ]<br>
**Computational cost:** [Same as eval 2 ]<br>
**Known weakness:** [This eval breaks down when two of the same pieces are put head to head. The agent's piece can capture the opponent's but the opponent's can also capture the agent's this leads to a zeroed score which is not accurate to good strategy. There also is no concept of piece shelter where one piece protects another. This is a fundamental strategy to allow your pieces to advance with less fear of being captured and this evaluator ignores it.]<br>
**Revision evidence:** [This version add the threat term. This is a crucial addition to avoid being captured. It is based off the value of the piece being threatened and includes consideration for the princess using their escape. These changes are based off of eval 1's blindness ot danger and eval 2's blindness to attack.]

### Hand-Checked Positions and Tests

| Position | Perspective | Eval 1 | Eval 2 | Eval 3 | Why these values are reasonable |
|---|---|---:|---:|---:|---|
| [ **A** — O Chief `c2`; B Padwar `h7`; turn Orange; no escapes spent ] | [Orange ] | [0.691836592 ] | [0.804596340  ] | [0.804596340  ] | [ A lone Chief against a lone Padwar is an overwhelming edge, so ~+0.69 is proportionate. Eval 2 rises because Orange has 35 legal actions to Black's 8 (worth +5.4 raw units). Eval 3 is bit-identical to Eval 2 — that equality is the point: nothing on `c2` attacks anything on `h7` and no escape is spent, so both new terms must contribute exactly 0. It proves they don't fire spuriously.] |

## 2. LLM Cutoff Evaluator

**Exact model tag:** [Gemma-4-26B-A4B-it-oQ4e-mtp]<br>
**Endpoint category:** [Do not record API-key values]<br>
**Temperature:** [0 ]<br>
**Requested seed and observed reproducibility:** [ ]<br>
**Deterministic fallback evaluator:** [Falls back on evaluate_position_3 ]

### Prompt and Response Contract

[The system message includes the rules, rubric, and contract. While the user message includes the current position. Uses a single JSON object `{"score": 0.25}`. Rejections are counted by fallback_calls when the llm fails and falls back on eval 3. The entire response in consumed rejecting valid responses followed by unecisarry fluff llms love to give. It also rejects interior newlines that llms love to add.]

### Prompt Revision

| Version | Representative response and limitation | Exact change | Evidence after change |
|---|---|---|---|
| Initial | [See P1 below ] | [Mirrors thought proccess of evaluator 1. Basic game description and piece values ] | [ ] |
| Revised | [See P2 below ] | [ ] | [ ] |
P1: 


## 3. Direct LLM Move Agent

### Prompt, Sensors, and Response Contract

<!-- [Provide the exact final prompt or appendix reference. Explain board, legal
moves, escape state, recent history, `{"move": "..."}`, validation, and the
deterministic legal fallback.] -->
[The same system user split prompt as described above. The model is never asked to construct a move, only to select one. Every move the agent selects is checked against the legal board actions. The captures are listed separately from legal moves to prevent invalid responses that describe the capture. The move prompt has a curated view and a single model call per move, while the cutoff evaluator receives one call per possible move ~100 moves. ]

### Prompt Revision

| Version | Representative response and limitation | Exact change | Evidence after change |
|---|---|---|---|
| Initial | [ ] | [ ] | [ ] |
| Revised | [ ] | [ ] | [ ] |

You are choosing one move for a Jetan agent. You are shown a position and the \
complete list of legal moves, and you must choose one of them.

BOARD
- The board is 10 by 10. Columns are a to j. Rows are 0 to 9.
- The diagram below prints row 9 first and row 0 last.
- A capital letter is an Orange piece. A lower-case letter is a Black piece.
  A dot is an empty square.
- Orange advances toward row 9. Black advances toward row 0.
- Letters: W Warrior, A Padwar, D Dwar, F Flier, C Chief, R Princess,
  T Thoat, P Panthan.
- A move is written as SOURCE-DESTINATION, for example d0-e3.

HOW THE GAME ENDS
- Capturing a Princess ends the match immediately in the capturer's favour.
- Capturing a Chief ends the match only if the capturing piece is a Chief.
- A player with no legal move loses.
- A Princess may once per match move more than 3 squares in a line. That move
  spends its escape permanently.

PIECE VALUES
Padwar 1.7, Warrior 1.9, Panthan 2.0, Dwar 4.9, Thoat 5.6, Flier 9.5,
Chief 19.0, Princess 21.0.

HOW TO CHOOSE
In rough order of priority:
1. If any move captures the opposing Princess, take it. It wins immediately.
2. Otherwise take a capture, preferring the most valuable piece captured.
3. Otherwise develop and centre your pieces, and avoid moves that leave your
   own pieces attacked with no support.

RESPONSE CONTRACT
Reply with exactly one raw JSON object and nothing else:

{"move": "a1-b2"}

The object must have exactly one key, "move", whose value is a JSON string
holding one of the legal move tokens listed below, copied exactly. No Markdown
code fences. No text before or after. No second line. No extra keys. Do not
invent a move that is not in the list, and do not add a capture suffix or
explanation to the token. Any other output is rejected and a deterministic
fallback chooses the move instead.


P2:
You are choosing one move for a Jetan agent. You are shown a position and the \
complete list of legal moves, and you must choose one of them.

BOARD
- The board is 10 by 10. Columns are a to j. Rows are 0 to 9.
- The diagram below prints row 9 first and row 0 last.
- A capital letter is an Orange piece. A lower-case letter is a Black piece.
  A dot is an empty square.
- Orange advances toward row 9. Black advances toward row 0.
- Letters: W Warrior, A Padwar, D Dwar, F Flier, C Chief, R Princess,
  T Thoat, P Panthan.
- A move is written as SOURCE-DESTINATION, for example d0-e3.

HOW THE GAME ENDS
- Capturing a Princess ends the match immediately in the capturer's favour.
- Capturing a Chief ends the match only if the capturing piece is a Chief.
- A player with no legal move loses.
- A Princess may once per match move more than 3 squares in a line. That move
  spends its escape permanently.

PIECE VALUES
Padwar 1.7, Warrior 1.9, Panthan 2.0, Dwar 4.9, Thoat 5.6, Flier 9.5,
Chief 19.0, Princess 21.0.

HOW TO CHOOSE
In rough order of priority:
1. If any move captures the opposing Princess, take it. It wins immediately.
2. Otherwise take a capture, preferring the most valuable piece captured.
3. Otherwise develop and centre your pieces, and avoid moves that leave your
   own pieces attacked with no support.
4. As you advance pieces advance other pieces next to it so that one protects the other.
5. When possible move a piece to attack two opponents pieces at once creating a fork.

RESPONSE CONTRACT
Reply with exactly one raw JSON object and nothing else:

{"move": "a1-b2"}

The object must have exactly one key, "move", whose value is a JSON string
holding one of the legal move tokens listed below, copied exactly. No Markdown
code fences. No text before or after. No second line. No extra keys. Do not
invent a move that is not in the list, and do not add a capture suffix or
explanation to the token. Any other output is rejected and a deterministic
fallback chooses the move instead.

## 4. Stage A: Deterministic Evaluator and Depth Development

### Stage A1: Evaluation Functions

**Depth:** 1<br>
**Opponent:** supplied random agent<br>
**Seeds:** 0, 1<br>
**Colors:** both<br>
**Maximum plies:** 100<br>
**Other command/configuration details:** [ ]<br>
**Depth verification from `maximum_depth`:** [All 12 rows report a max depth of when on check with maximum depth.]

For every table below, “Mean think time” means the focal tested agent's
color-specific cumulative time: `orange_time` when it is Orange and `black_time`
when it is Black. Retain all scheduled attempts. Mark unavailable metrics as
`N/A`, never as zero, and state the contributing attempt count for every mean.

| Agent | Games | W | D | L | Mean utility | Mean plies | Mean think time | Mean generated actions | Mean evaluated states | Failures |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `minimax_1` | 4 | [ 4] | [2 ] | [2 ] | [0 ] | [66 ] | [0.305 ] | [1788.25 ] | [1788.25 ] | [0 ] |
| `minimax_2` | 4 | [ 4] | [0 ] | [0 ] | [1 ] | [15 ] | [0.495 ] | [670.75 ] | [670.5 ] | [0 ] |
| `minimax_3` | 4 | [4 ] | [0 ] | [0 ] | [1 ] | [5.5 ] | [.121 ] | [178 ] | [178 ] | [0 ] |

### Provisional Evaluator Selection

[minimax_3 with a depth of 1. When it comes to utility both 2 and 3 have a value of 1 but 3 only uses 178 generated actions compared to 671 actions that 2 computes. Additionally it finishes in 5.5 plies compared to 2's 15. The fewer plies is the kicker here. The shorter match leaves more budget left over.]

### Stage A2: Depths 1 and 2

**Selected evaluator:** [minimax 3 ]<br>
**Opponent:** supplied random agent<br>
**Seeds:** 0, 1<br>
**Colors:** both<br>
**Maximum plies:** 100<br>
**Depth verification from `maximum_depth`:** [ 4/4 rows reported max depth of 2]

| Depth | Games | W | D | L | Mean utility | Mean plies | Mean think time | Mean generated actions | Mean evaluated states | Failures |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 4 | [ 4] | [0 ] | [0 ] | [1 ] | [5.5 ] | [0.121 ] | [178 ] | [178 ] | [0 ] |
| 2 | 4 | [ 4] | [0 ] | [0 ] | [1 ] | [9.0 ] | [60.146 ] | [20775 ] | [20442 ] | [0 ] |

Reuse the selected evaluator's depth-1 results from Stage A1. Describe one
position or match segment in which depth changed the selected move or backed-up
value. Explain the effectiveness and decision-cost tradeoff.
There was a significant number of focal decisions that changed with depth but despite the orders of magnitude increase of resources used there was no outcome gain. It is simply infeasible for the llm cutoff evaluator with the budget that we have.

### Final Deterministic Configuration

**Evaluator:** [ evaluate_position_3 minimax 3]<br>
**Depth (`1` or `2`):** [ 1]<br>
**Selection justification using the supplied performance priorities:** [This decision came down to cost. The depth 2 did not improve the outcome enough to be worth the time it takes. ]

## 5. Stage B: Final Modality Comparison

**Seeds:** 0, 1<br>
**Colors:** both<br>
**Maximum plies:** 60<br>
**Cumulative think-time limit per player:** 3600 seconds or documented override<br>
**Selected deterministic depth:** [1 ]<br>
**LLM cutoff-evaluator depth:** 1<br>
**LLM-evaluator request limit:** 100 per move search<br>
**Commands and machine/runtime context:** [python run_experiments.py b --evaluator minimax_3 --depth 1 --output results/b.csv  ]<br>
**Depth verification from `maximum_depth`:** [Both deterministic and llm evaluator confirm max depth of 1 ]

| Agent | Depth | Games | W | D | L | Mean utility | Mean plies | Mean think time | Mean generated actions | Mean evaluated states | Termination reasons/failures |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Selected deterministic minimax | [1 or 2] | 4 | [4 ] | [0 ] | [0 ] | [1 ] | [5.5 ] | [.197 ] | [178 ] | [178 ] | [4 princess captured] |
| LLM cutoff evaluator | 1 | 4 | [ 0] | [0 ] | [4 ] | [-1 ] | [56 ] | [3706 ] | [2053 ] | [2053 ] | [4 Time limit ] |
| Direct LLM move agent | N/A | 4 | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | N/A | N/A | [ ] |

| Mean per-match LLM metric | Cutoff evaluator | Direct move agent | Denominator |
|---|---:|---:|---|
| Model calls | [ ] | [ ] | All 4 matches |
| Fallback calls | [ ] | [ ] | All 4 matches |
| Cache hits | [ ] | N/A | All 4 matches |

For each prompt revision, show at least one representative rejected response if
one occurred and identify its validation category. Exhaustive rejection-category
counts are not required because the starter exposes aggregate fallback counts.

Retain every match and failure. Cite the generated summary denominator fields
and explain unavailable metrics. Explain that the two LLM modalities do not
receive equal model-call budgets or perform equivalent work. Also account for
any depth difference between the selected deterministic agent and the depth-1
LLM cutoff evaluator.

## 6. Stage C: Deterministic Versus Direct LLM

**Deterministic evaluator:** `evaluate_position_3` minimax (server agent `minimax_3`)<br>
**Deterministic depth:** 1<br>
**Direct LLM prompt version:** Final revised v2 = `MOVE_PROMPT_VERSION "v2"` (`_MOVE_INSTRUCTIONS_V2`; closing-brace/final-character contract), frozen before Stage C<br>
**Model seeds:** 0, 1<br>
**Colors:** both<br>
**Maximum plies:** 60<br>
**Cumulative think-time limit per player:** 3600 seconds per player (default; no override used)<br>
**Depth verification from `maximum_depth`:** all four `results/c.csv` rows report `maximum_depth=1` for the deterministic agent; the direct LLM performs no search (`N/A`)<br>
**Commands and machine/runtime context:** `python run_experiments.py c --evaluator minimax_3 --depth 1 --output results/c.csv` — 4 matches (2 colors x 2 seeds), live LLM endpoint (self-hosted Tailscale `golem:8000`, `Gemma-4-26B-A4B-it-oQ4e-mtp`, temperature 0), incremental per-match writes to `results/c.csv` and aggregate `results/c-summary.csv`; every mean below has denominator 4<br>
**Seed/color configuration evidence:** the four Stage C rows record the deterministic agent's configured seed, but determinism holds across seeds: the two Black-LLM rows (seed 0 and seed 1) each end at plies 13 with identical 414 generated/evaluated states and identical move sequences, so the configured seed is never consumed by the deterministic agent — seeded behavior applies to the LLM side only

| Direct LLM color | Model seed | Deterministic utility | Direct LLM utility | Plies | Termination reason/failure |
|---|---:|---:|---:|---:|---|
| Orange | 0 | +1 | -1 | 4 | Loss — minimax wins by ply 4; LLM took 2 moves, 1 contract-rejected (`fallback_calls=1`) |
| Orange | 1 | +1 | -1 | 48 | Loss — longest game (48 plies, 24 LLM moves, 1 fallback) |
| Black | 0 | +1 | -1 | 13 | Loss — minimax (Orange) wins at ply 13 |
| Black | 1 | +1 | -1 | 13 | Loss — minimax (Orange) wins at ply 13 |

Record each utility from the named agent's perspective: `+1` for that agent's
win, `0` for a draw, and `-1` for that agent's loss. Compute each aggregate W/D/L
record and mean utility from the perspective of the agent named in that row.

| Agent | Games | W | D | L | Mean utility | Mean own-agent think time | Mean generated actions | Mean evaluated states | Mean model calls | Mean fallback calls |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Selected deterministic minimax | 4 | 4 | 0 | 0 | +1.00 | 1.06 s | 577.25 | 577.25 | N/A | N/A |
| Final direct LLM | 4 | 0 | 0 | 4 | -1.00 | 24.72 s | N/A | N/A | 9.5 | 0.5 |

Denominator = 4 for every mean (all four matches produced results; zero failures).
Mean think time uses each agent's color-specific time (`orange_time`/`black_time`).
Per-match values: LLM think 12.71 / 16.02 / 57.89 / 12.25 s; LLM calls 2 / 6 / 24 / 6;
LLM fallbacks 1 / 0 / 1 / 0; minimax think 0.42 / 0.98 / 1.93 / 0.92 s; minimax
generated = evaluated 113 / 414 / 1368 / 414. The deterministic row shows N/A for
model/fallback calls (no LLM involved); the LLM row shows N/A for generated/evaluated
states (the direct LLM performs no search).

Head-to-head segment: Match 1 (LLM Orange, seed 0) ends at ply 4 — minimax captures
the Princess within four plies while the LLM produced only 2 moves, one of which was
contract-rejected (`model_calls=2`, `fallback_calls=1`). Match 3 (LLM Orange, seed 1)
is the fairest comparison: 48 plies, 24 LLM moves, 23 of 24 v2 responses accepted —
the revised prompt held its response contract, yet the model's play still lost to
depth-1 minimax. Conclusions are limited to this pairing: minimax_3 (depth 1) vs the
final direct LLM, seeds 0 and 1, both colors, 60 max plies, 3600 s think limit; they
do not extend to other depths, prompts, evaluators, or opponents.

Use each agent's color-specific time. Mark unavailable values as `N/A` and
cite the generated denominator for each mean. Explain one position or match
segment that helps account for the head-to-head result. Limit conclusions to
this pairing, these seeds, and these match conditions.

## 7. Analysis Questions

1. [The evaluator 3 with a depth of 1 was the most effective. Its ability to account for pieces being threatened vs a free capture and seeing more valuable captures set it apart from evaluators 1 and 2. The llm had less effective results in significantly longer amount of time.]
2. [My changes didn't seem to do to much. My original prompt was AI generated and already very thorough. I tried to encourage some basic chess strategies that I assume also work in Jetan but it didn't change the results significantly.]
3. [What does Stage B show about the three modalities against random? Account
   for the distribution across seeds and colors, strategic disagreements,
   unequal depth, and model-call patterns.]
4. [What does Stage C show, and which claims remain limited by the pairing,
   seed, colors, move limit, or number of matches?]
5. [Using the supplied PEAS performance measure, which criteria were measured
   adequately, and what further evidence is needed?]

## 8. Testing and Reproducibility

**Test command/result:** [ ]<br>
**Offline/live separation:** [ ]<br>
**Registered agent names confirmed:** [ ]

[Summarize evaluator, parser, validation, fallback, budget, and scripted-client
tests.]

## 9. AI-Assistance Disclosure

**Tools:** [Names or `No AI assistance used`]<br>
**Material effect:** [ ]<br>
**Verification:** [ ]

## Submission Checklist

- [ ] Accessible `lastname-firstname-jetan-agents.pdf` with selectable text and
      semantic headings/tables.
- [ ] Three deterministic evaluation functions, depth comparison, and Stage A
      results.
- [ ] Initial and revised prompts for both LLM modalities.
- [ ] Complete Stage B results, failures, and summary denominator fields.
- [ ] Complete Stage C head-to-head results and bounded interpretation.
- [ ] Five analysis questions answered from evidence.
- [ ] Raw and summary CSV files for Stages A1, A2, B, and C retained in the
      repository.
- [ ] Private repository URL, submitted commit, and `fractal13` read access.
- [ ] Tests pass; `.env`, credentials, and secrets are absent from the repository.
- [ ] References and AI-assistance disclosure are complete.
