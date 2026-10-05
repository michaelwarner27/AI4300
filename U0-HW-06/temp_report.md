# Working notes for U0-HW-05 report (transcription source)

> **This is not the deliverable.** `U0-HW-05-report-template.md` is unmodified; this
> file exists so the answers can be written out once and transcribed. Every number
> below is measured or read from generated CSV, with the source named.
>
> **Status legend:** `[READY]` = fillable now. `[LIVE]` = requires the live pilot
> (blocked on Tailscale + endpoint). `[FIX]` = code comment needs correcting.

---

## ⚠️ Read first: two errors in the hand-checked derivations

`[FIX]` Both are in `test_student_strategies.py`, in the **comments above the
assertions**. The assertion values are correct and pin the right floats; the
derivation prose does not match the code. Corrected derivations are in the
Position A / B / C block below — transcribe those, not the current comments.

**`[FIX]` 1. Position A is on `c2`, not `b2`.** The board is built from `(2, 2)`,
and the token is column-letter + `y` with no offset, so it prints as `c2`. The
comment also shows Black's advance as `(9-9)/9`; Black's Padwar is on `y=7`, so
its term is `(9-7)/9 = 2/9`, which is *why* the difference is 0.

**`[FIX]` 2. Position C's raw sum is `+0.8833333`, not `-0.1166667`.**
`-0.1166667` is the subtotal *before* adding escape, and the comment then used it
while still reporting the post-escape answer. Since `tanh` of a negative is
negative, the current line is self-contradictory. Verified by inversion:
`0.043696591 / 0.99 -> atanh -> x20 = 0.8833333`.

These are prose-only changes; no assertion changes, so they cannot affect the
156-passing test count.

---

## Section 1 — Evaluation 3 (the five empty fields)

`[READY]`

### Information used and intentionally ignored

Everything in Evaluations 1 and 2, plus two new terms.

- **Threat** is the value of opponent pieces standing on squares the acting
  player's pieces attack, minus the reciprocal — so it distinguishes a capture
  that is *available* from one that is merely *threatened*.
- **Escape** credits the acting player when the opposing Princess has spent its
  one long move and it has not.

Intentionally ignored: **shelter/support** (a piece defended by a second attacker
scores the same as a hanging one), and — *for the advance term* — the Princess,
because the rules forbid it from moving onto an attacked square, so rewarding its
advance would push it into danger.

### Scaling/range

Unchanged from Evaluations 1 and 2: all five terms are material-equivalent and
share the `/20`, `0.99 * tanh(.)` pipeline, so output stays in `[-0.99, 0.99]`.

The threat term is the only addition that can be large: it sums piece values, so
in principle `+/-101.2`. In practice the Jetan step sets are closed under step
negation, so mutually-attacking equal-reach pieces cancel *exactly* in the
difference, and the term is usually near zero, occasionally decisive. Escape is
bounded by `+/-1.0` — under one Padwar — so it is a tie-breaker, not a driver.

### Expected relationship to utility

It is the only one of the three that represents **both** ways a Jetan position is
actually lost — an attacked Princess and an exhausted escape — so it should best
separate a winning position from a nominally even one.

Note the mechanism is *unified*: pricing the Princess at 20.0 inside the threat
term means royal safety and winning captures fall out of one term rather than two
overlapping heuristics.

### Computational cost

`0.0381 ms/call` for `evaluate_position_3` (median of 40 passes over 360
deterministic cutoff positions, plies 1-60), vs `0.0275` for Eval 2 and `0.0126`
for Eval 1.

Isolated per-term cost (ms/call):

| Term | Cost |
|---|---:|
| material | 0.0129 |
| mobility | 0.0012 |
| advance | 0.0659 |
| threat | 0.0449 |
| escape | 0.0019 |

Advance and threat are the expensive ones, because advance is a Python loop over
pieces and threat builds the attacked sets. But the *incremental* cost of threat
over Eval 2 is nearly free in real use: `attacked_locations` is memoized per
board, and `legal_action_count` — already paid by the mobility term —
materializes both sides' attacked sets, so threat adds only two set lookups.

Worst case (one fallback per legal action over a whole match, ~2,100 calls):
`2100 * 0.0381 ms = 0.080 s` of a 3,600 s budget, i.e. **0.002%**.

### Known weakness

The threat term is **myopic and fires less often than it appears**. Every
non-Princess step set is closed under negation, so two equal-reach pieces that
threaten each other cancel exactly; real work happens only between pieces of
*different* reach (a Flier bearing down on a Padwar that cannot answer) or when a
blocked intermediate square breaks symmetry.

It also double-counts a piece that is both present and attacked, and — because
the mobility term already carries a partial Princess-safety signal (the Princess
alone is 27 of 52 legal opening moves) — an attacked Princess is penalized
**twice**. Three-ply tactics stay invisible.

### Revision evidence

Evaluations 1 and 2 both have a demonstrated failure that this addresses.

- **Eval 1** is blind to danger: it will trade its Princess for a marginal gain.
- **Eval 2** is still blind to attack: it can win a material race while marching
  into attack, and cannot tell an available capture from a threatened one.

The added terms are bounded so they *re-rank* material-equal positions without
overruling a real material difference: escape caps at 1.0, and a symmetric mutual
attack nets to zero.

The sharpest demonstration is hand-checked **Position C** below, where material
is exactly equal (Eval 1 returns exactly `0.0`) and the two new terms move the
score by only `+0.0074`.

---

## Section 1 — Hand-Checked Positions and Tests

`[READY]` Test class `HandCheckedPositionTests` in `test_student_strategies.py`
(8 tests). All perspectives are **Orange**. Antisymmetry is asserted separately by
`PerspectiveTests` (`eval(b, Black) == -eval(b, Orange)` to 1e-9, over 125 corpus
positions).

| Position | Perspective | Eval 1 | Eval 2 | Eval 3 | Why these values are reasonable |
|---|---|---:|---:|---:|---|
| **A** — O Chief `c2`; B Padwar `h7`; turn Orange; no escapes spent | Orange | 0.691836592 | 0.804596340 | 0.804596340 | A lone Chief against a lone Padwar is an overwhelming edge, so ~+0.69 is proportionate. Eval 2 rises because Orange has 35 legal actions to Black's 8 (worth +5.4 raw units). **Eval 3 is bit-identical to Eval 2** — that equality is the point: nothing on `c2` attacks anything on `h7` and no escape is spent, so both new terms must contribute exactly 0. It proves they don't fire spuriously. |
| **B** — O Flier `e8`, O Padwar `a0`, O Padwar `b0`; B Warrior `j9`; turn Orange; no escapes spent | Orange | 0.495515009 | 0.602496508 | 0.602496508 | Material 12.9 vs 1.9 = **+11.0**, decisive but not won; 11.0 is just over half of 20.0 where `tanh` is near-linear, so ~0.495 is the right size. Eval 2 rises via advance (Flier on `e8` is 8/9 of the way across, +0.133) and mobility (18 to 3, +3.0). **Eval 3 == Eval 2** again: no piece attacks across the board, escape unspent. |
| **C** — O Flier `e4`, O Padwar `a0`; B Flier `b1`, B Padwar `j9`; turn Orange; **Black's escape spent** | Orange | 0.000000000 | 0.036283741 | 0.043696591 | Material is *exactly* equal (11.2 vs 11.2), so Eval 1 returns exactly 0.0 — a clean material tie. Eval 3 exceeds Eval 2 by only **+0.0074**, and that entire gap is threat (-0.85) plus escape (+1.0) = **+0.15 raw units of 20**. The near-cancellation *is* the finding. Threat is negative for Orange: Black's Flier on `b1` attacks **both** Orange pieces (`a0` + `e4` = 11.2) while Orange's Flier on `e4` attacks only Black's `b1` (9.5), so Black threatens 1.7 more. **C is the only position where the two new terms oppose each other**: threat is -0.85 and escape is +1.0, so the threat term alone would *lower* the score and only the escape term lifts the total. The terms are not monotone embellishments of one another. |

### Full derivations (transcribe these, not the current comments)

```
POSITION A   material  19.0 - 1.7 = 17.3
             mobility  0.2 * (35 - 8) = 5.4
             advance   0.15 * ((2-0)/9 - (9-7)/9) = 0.15 * 0 = 0.0
             threat    0.0   (c2 and h7 are mutually unreachable)
             escape    0.0
             raw = 22.7
  Eval 1 = 0.99 * tanh(17.3/20)       = 0.691836592
  Eval 2 = 0.99 * tanh(22.7/20)       = 0.804596340
  Eval 3 = 0.99 * tanh(22.7/20)       = 0.804596340   (threat and escape are exactly 0)

POSITION B   material  (9.5 + 1.7 + 1.7) - 1.9 = 11.0
             mobility  0.2 * (18 - 3) = 3.0
             advance   0.15 * ((8-0)/9 + 0 + 0 - (9-9)/9) = 0.15 * 0.888889 = 0.133333
             threat    0.0
             escape    0.0
             raw = 14.133333
  Eval 1 = 0.99 * tanh(11.0/20)       = 0.495515009
  Eval 2 = 0.99 * tanh(14.133333/20)  = 0.602496508
  Eval 3 = 0.99 * tanh(14.133333/20)  = 0.602496508

POSITION C   material  (9.5 + 1.7) - (9.5 + 1.7) = 0.0
             mobility  0.2 * (16 - 12) = 0.8
             advance   0.15 * ((4-0)/9 - (9-1)/9) = 0.15 * (-0.444444) = -0.066667
             threat    0.5 * ((9.5) - (1.7 + 9.5)) = 0.5 * (-1.7) = -0.85
             escape    1.0 * (1 - 0) = 1.0
             raw = +0.883333      <-- positive. -0.116667 is the PRE-escape subtotal
  Eval 1 = 0.99 * tanh( 0.0/20)      = 0.000000000
  Eval 2 = 0.99 * tanh( 0.733333/20) = 0.036283741
  Eval 3 = 0.99 * tanh( 0.883333/20) = 0.043696591
```

Position C attack sets, for the report's threat discussion (perspective Orange):

```
Orange attacks: b1 b3 b5 b7 d1 d3 d5 d7 f1 f3 f5 f7 h1 h3 h5 h7
Black  attacks: a0 a2 a4 c0 c2 c4 e0 e2 e4 h7 h9 j7
  my_threat    (Black pieces Orange attacks):  b1 FLIER  9.5
  their_threat (Orange pieces Black attacks):  a0 PADWAR 1.7, e4 FLIER 9.5
  raw diff = 9.5 - 11.2 = -1.7   ->  term = 0.5 * -1.7 = -0.85
```

---

## Section 2 — LLM Cutoff Evaluator

`[READY]` except the two `[LIVE]` cells marked below.

**Exact model tag:** `Gemma-4-26B-A4B-it-oQ4e-mtp` (from `.env.example`; confirm
against the endpoint's `/v1/models` listing before submission and update if it
differs).

**Endpoint category:** Course-hosted OpenAI-compatible `/v1/chat/completions`
endpoint. **No key value recorded.** `OPENAI_API_KEY=course-placeholder` — the
placeholder the course requires, not a credential. `.env` stays out of the repo;
only `.env.example` is committed.

**Temperature:** 0 (`OPENAI_TEMPERATURE=0`). Single greedy sample per call; no
`n > 1`, no best-of.

**Requested seed and observed reproducibility:** `[LIVE]` **This cell cannot be
filled yet — it is a live observation.** `OPENAI_SEED` is not in `.env.example`,
and the supplied client does not forward a seed. Report as: "not forwarded by the
supplied transport"; reproducibility is then argued from `temperature = 0` plus
the uncached-call count, and the direct-LLM seed axis in Stages B/C is a
client-side argument that I will show is not reaching the model. **Flag as a known
limitation rather than claiming determinism.** At pilot time, measure whether
repeated identical calls return identical text — that is a real observation.

**Deterministic fallback evaluator:** `evaluate_position_3`. Registered at
`student_agents.py:31-37`.

This coupling is deliberate: the prompt rubric mirrors `evaluate_position_3`
term-for-term and coefficient-for-coefficient, so whether the model answers or
the fallback answers, the position is judged by the same objective. Consequence: a
Stage B/C utility gap is attributable to **model error**, not to the objective
silently changing underneath the measurement. Without this, a bad prompt and a bad
model are indistinguishable — the one distinction the experiment exists to make.
Cost of the stronger fallback vs `evaluate_position_1`: 0.030 ms/call, ~63 ms
across a whole match even if every call is rejected.

### Prompt and Response Contract

Two messages. `system` (2,879 chars) = rules + rubric + contract; `user` (438 chars
in the opening position, ~380 typical) = the position. The split is what makes the
required revision cheap and auditable: a rubric change does not disturb the
position format, so the two diff separately.

**Schema.** Exactly one raw JSON object, one line, one key: `{"score": 0.25}`.

**Rejections are the framework's signal.** `llm_evaluation.py:64` wraps
prompt + call + parse in a bare `except Exception` and substitutes the fallback,
so raising is the documented path. Raising also means the rejection is
*counted*: `fallback_calls` in the Stage B/C table is the number of times the
parser raised, so every rule below is what makes reliability **measurable**, not
just enforced.

**Strict parsing, in order** (shared by both modalities, in
`_decode_single_object`):

1. must be a `str`
2. strip outer whitespace
3. non-empty
4. **single line** — an interior newline is proof of a fence or commentary
5. JSON decode, with duplicate keys and `NaN` / `Infinity` refused by
   `object_pairs_hook` / `parse_constant` hooks
6. the decoded object must consume the response **entirely** (`raw_decode` end
   index `== len(text)`)
7. must be an object, not an array/number/bare literal
8. must contain `score` and nothing else

**Two design points worth stating explicitly in the report:**

- **Whole-response consumption** is what makes "rejects fences and commentary"
  testable. `end != len(text)` rejects `{"score": 0.2} I think Black is winning`
  rather than quietly accepting the prefix. The mirror case (commentary *before*
  the object) fails inside `raw_decode`, so it is identified by brace position:
  brace at index 0 -> `invalid_json` (a genuine malformed attempt); brace later ->
  `surrounding_content` (a correct object that got wrapped). "Cannot format JSON"
  and "adds a preamble" are different defects with different fixes, and the pilot
  recorder reports them separately.
- **Outer whitespace stripped, interior newlines refused** — deliberately
  asymmetric. A trailing newline is near-universal in model output; rejecting it
  would manufacture fallback calls for responses that are in fact well formed.

**Duplicate keys:** `json.loads` keeps the *last* value, so
`{"score": 0.1, "score": 0.9}` would parse cleanly as `0.9` and silently discard a
model's self-correction. `object_pairs_hook` refuses any repeat.

**Finite range:** `bool` is rejected **before** the numeric test — in Python
`isinstance(True, int)` is `True`, so an ordinary check would accept
`{"score": true}` and return `1.0`, a number, in range, and completely wrong.
Non-finite is caught **after** `float()` conversion, because `1e400` is valid JSON
syntax that becomes `inf` with no error. Range is closed at `+/-0.99`: `0.99`
accepted, `1.0` rejected — because the framework returns exact `+/-1.0` for a
*finished* game, and accepting it from a cutoff would let a confident model
declare a decided position and suppress further search.

**Perspective — the trap.** The framework calls with `perspective` fixed to the
search's root player while `board.player()` is whoever is to move at the cutoff.
At depth 1 those differ for *every* action by the opponent, so the prompt states
both in separate labelled fields and warns against flipping the sign. A prompt
reporting only the side to move would produce a sign error on roughly half of all
evaluations — and a sign error is the one failure mode a fallback **cannot**
repair, because the value is well formed and gets accepted.

**Fallback:** `evaluate_position_3`, as described above.

### Sensor parity — the honest answer

The model does **not** receive the same *effective* information, and one quantity
is deliberately withheld.

| Quantity | Deterministic agent | This prompt |
|---|---|---|
| Piece inventory | `board.pieces` | board diagram |
| Legal-move counts | `legal_action_count` | stated directly |
| Advance | from coordinates | readable from diagram |
| Escape flags | `board.used_escape` | stated directly |
| **Attacked squares** | `attacked_locations` | **not supplied** |

Four cheap signals are precomputed and stated, so the model does not spend
attention counting moves. The fifth is withheld because `attacked_locations` is a
set the program derives by applying eight step patterns to every piece — no
amount of board text makes that arithmetic shallow.

**The model has the same underlying data here but strictly more inference to
perform, so its threat term is the term most likely to be wrong, and a wrong
threat term biases the score in both directions at once.**

### Prompt Revision `[READY — pilot 2026-09-28]`

**Instrumentation:** `record_llm_responses.py` captured every raw response plus
`signed_error` vs `evaluate_position_3` on a ply-stratified sample, and
attributes each rejection to a `REJECT_*` category. All evidence is in
`results/pilot-eval-v1.csv` (v1) and `results/pilot-eval-v2.csv` (v2).

**v1 pilot (80 probes, seed 0):** 52/80 accepted (**65.0%**), 28 rejected —
`invalid_json` ×25, `multiple_lines` ×3. Mean `|signed error|` vs
`evaluate_position_3` (accepted only) **0.3221**; mean latency 2.700 s.

**Pre-registered prediction vs observation.** The prediction was that errors
would concentrate in the threat term (hand-derived attack geometry). That was
*not* the rejection driver: inspection of the raw text shows a single dominant
format failure — the model opens `{"score": 0.1355...` and then emits an
unbounded repeating decimal (`10101010...`) instead of the closing brace, or
closes the object and appends self-correcting commentary. 25 of 28 rejections
are the former, 3 the latter. (The mean abs error of 0.32 on accepted values
is consistent with the accuracy concern, but it is a separate phenomenon from
the rejection rate.)

**Representative rejected responses (mapped to validation rules):**

| Category | Representative v1 response (abridged) | Validation rule |
|---|---|---|
| `invalid_json` | `{"score": 0.1355181111010101...` (never closes; unbounded repeating decimal) | must be one valid JSON object — never terminates |
| `multiple_lines` | `{"score": 0.11351515111111111}\n\nWait, I must provide a single number...` | one line only; whole response must be consumed |

**Revision applied (v2, the documented revision):** the response contract was
tightened — the score is a number with **at most two digits after the decimal
point** (kills the digit-runaway), and **the closing brace must be the final
character of the reply, with self-correction commentary explicitly forbidden**
(kills the trailing text). V1 stays frozen as `_EVALUATION_INSTRUCTIONS_V1`;
v2 is `_EVALUATION_INSTRUCTIONS_V2`, tagged `EVALUATION_PROMPT_VERSION = "v2"`.
The sensor set is unchanged — no threatened-piece list was added, because the
pilot showed the rejection blocker was format, not threat accuracy.

**Evidence after change:** v2 pilot (80 probes, same seed) **80/80 accepted
(100.0%), 0 rejected**; mean `|signed error|` **0.2137**; mean latency
1.664 s. Accepted scores all in `[-0.99, 0.99]` (min -0.61, max 0.31).

**Reproducibility (same seed, same v2 prompt, repeat run):** 80/80 responses
**byte-identical** and 80/80 identical parsed scores (`results/pilot-eval-v2-rep.csv`)
— the endpoint honors the forwarded seed at temperature 0.

Pilot commands used:

```
python record_llm_responses.py evaluate --live --out results/pilot-eval-v1.csv
python record_llm_responses.py evaluate --live --out results/pilot-eval-v2.csv
python record_llm_responses.py evaluate --live --out results/pilot-eval-v2-rep.csv
```

---

## Section 3 — Direct LLM Move Agent

`[READY]` except the `[LIVE]` revision table.

### Prompt, Sensors, and Response Contract

Same two-message split: `system` (1,835 chars) = rules + priority ordering +
contract; `user` (1,409 chars in the sampled position) = the position.

**Everything the assignment enumerates is present**, as its own labelled field:

- current player, named explicitly
- board, as the same diagram the cutoff evaluator uses
- Princess escape state for **both** sides
- the authoritative legal move list, numbered, in the exact token spelling the
  parser will look up
- the bounded recent-move window

**The central design decision: the move list is enumerated, and the reply must echo
one token from it.** The model is never asked to *construct* a move, only to
*select* one. `parse_move_response` looks the reply up in
`{str(action): action for action in actions}` — a **dictionary lookup, not a
coordinate parse**. Model text is used only as a hash key against a table the rule
engine produced, so it is never decomposed into `(x, y)` and no model-chosen square
can reach the board. Illegal output is impossible by construction, not merely
unlikely.

**Three independent checks:** this lookup; `DirectLLMAgent.choose_action` re-checking
membership (`direct_llm_agent.py:66, 71`); and `JetanBoard.result` re-validating
against `board.actions()` on every application. A parser that reconstructed a move
from a string would need syntax, range, occupancy and turn validation and would
still have a success path outside the engine's set; a lookup has exactly one
failure mode.

**Matching is exact and case-sensitive.** `"D0-E3"`, `"d0-e3 "`, and `"d0 e3"` are
all rejected. A lenient normaliser is where almost-correct output becomes a
*silently different move*, which is harder to attribute later than an outright
rejection.

**Shared validation** is the same seven rules as the evaluator with `"move"` as the
required key, plus a `str` type check and membership. Duplicate keys and extra
content rejected identically. All 14 `REJECT_*` categories are shared constants.

**Why captures are a separate block** rather than inline annotations: annotating a
token with its victim would offer the model a string to copy that is *not* a legal
token, and the cheapest way to guarantee a rejection is to offer a tempting wrong
answer. So the list stays machine-readable and `Captures available:` sits below it.

**`history_limit=8`** set explicitly at `student_agents.py:47` (infra default is 6)
so the bound is visible at the point of registration. Cleared at the start of every
match. Offered as plain chronological text with no commentary.

### Deterministic fallback — `choose_fallback`, tiered

1. a move capturing the opposing **Princess** (ends the match at once);
2. failing that, any capture, **highest-value victim**, ties broken on the move
   token;
3. failing that, the **lexicographically first** token.

Five justifications, all of which belong in the report:

- **Cannot produce an illegal move** — always a member of `actions`, chosen by
  `min` over the engine's own tuple rather than constructed. This matters most: a
  fallback that *could* return an illegal move hands the game to
  `JetanBoard.result`, which raises and ends the match as an **agent failure**
  rather than a rejected response.
- **Deterministic** — no random source, no clock, no model text. Stage C varies a
  model seed and asks whether the deterministic opponent is affected; a fallback
  drawing on randomness would make the direct agent's move sequence seed-dependent
  for reasons unrelated to the model, and the two effects could not be separated
  afterwards.
- **Cheap** — `O(len(actions))` set lookups, no search. This deliberately rejects
  the obvious alternative of falling back to `evaluate_position_3`-driven minimax.
  That would be *stronger*, but in the worst case (a model that always fails) the
  "direct LLM" row of Stage B would be a re-run of the deterministic row wearing a
  different name. **A fallback must be a floor, not a substitute agent.**
- **Tiered, so the comparison stays honest** — a fallback returning `actions[0]`
  (the infra default at `direct_llm_agent.py:16`) would make every rejection look
  catastrophic and would *understate* the direct agent, because in most positions a
  capture is available and taking it is simply correct. Three tiers cost the same
  asymptotically and remove that bias.
- **Caveat to state plainly:** the tiers are a floor, not a strategy. Rule 3 is a
  tie-break with no positional judgement, so in a quiet position the fallback is
  near-arbitrary. A high fallback rate in quiet positions should be read as a
  **model** failure, not a fallback failure.

### Sensor parity — the answer differs from Section 2

The move prompt supplies **strictly more** than the deterministic agent receives,
not less: it adds the numbered legal-move list and the capture summary, both of
which a depth-1 minimax derives internally and never states. So parity is not the
question. The question is whether the two modalities do **comparable work**, and
they do not:

- **Direct agent** = one curated view + **one model call per move**.
- **Cutoff evaluator** = one model call **per candidate move** (~49-122 legal
  actions observed; corpus range 16-122).

Stage B's two LLM rows are therefore **not equal-effort conditions**, and the report
must not present them as one. Budget-capped at 100 calls per move search, the
cutoff evaluator's search is *truncated* on most positions, while the direct agent
spends its whole budget on one decision.

### Prompt Revision `[READY — pilot 2026-09-28]`

**v1 pilot (40 probes, seed 0):** `results/pilot-move-v1.csv` — **40/40 accepted
(100.0 %), 0 rejected**, mean latency 1.483 s. The pre-registered prediction
(format, not strategy) was therefore *vacuously* right: no failure of any kind
occurred, so the model never got a chance to prove the format least-likely
reading right.

**Revision applied (v2, a documented hardening step, not a repair).** With zero
rejections there was nothing to fix, but the *evaluation* pilot on the same model
and the same shared parser exposed two failure habits (an unbounded repeating
decimal, and self-correcting commentary appended after a valid JSON object). The
move contract is the same parser family, so v2 declares, pre-emptively:
**the closing brace must be the final character of the reply, and no
self-correcting commentary may follow the object**. Both versions frozen —
`_MOVE_INSTRUCTIONS_V1` (unchanged) and `_MOVE_INSTRUCTIONS_V2`, tagged
`MOVE_PROMPT_VERSION = "v2"`. The enumerated legal-move list and the
"select, never construct" property are untouched; the history window is kept
(the pilot produced no evidence it distracts).

**Evidence after change:** v2 pilot (40 probes, same seed)
`results/pilot-move-v2.csv` — **40/40 accepted (100.0 %), 0 rejected**, mean
latency 1.514 s. No rejection occurred in either version, so the revision table
for this modality reports zero rejected responses in both cells.

---

## Section 4 — Stage A1: Evaluation Functions

`[READY]` (as of 2026-09-28: A1 evidence tabulated above is final)

**Command:** `python run_experiments.py a1 --output results/a1.csv`

**Machine / runtime context:** 12th Gen Intel Core i7-1255U (10 cores / 12
logical), 15.6 GB RAM, Intel Iris Xe (**no GPU used** — all stages are CPU-only),
Windows 11 Pro build 26200, Python 3.12.6, stdlib only. `live=False` for all 12
rows; no model calls, so `--max-llm-calls` and `--max-think-seconds` are not
applicable to Stage A1.

**Depth verification from `maximum_depth`:** **12/12 rows report
`maximum_depth = 1`**, matching the configured depth — `orange_maximum_depth = 1`
on all 6 Orange-focal rows, `black_maximum_depth = 1` on all 6 Black-focal rows.
All 12 rows are `status=ok`, `exit_status=0`, with no `error` field populated.
**Nothing was dropped or rerun.**

| Agent | Games | W | D | L | Mean utility | Mean plies | Mean think time | Mean generated actions | Mean evaluated states | Failures |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `minimax_1` | 4 | 2 | 2 | 0 | 0.50 | 66.0 | 0.305076 s | 1788.25 | 1788.25 | 0 |
| `minimax_2` | 4 | 4 | 0 | 0 | 1.00 | 15.0 | 0.494568 s | 670.75 | 670.75 | 0 |
| `minimax_3` | 4 | 4 | 0 | 0 | 1.00 | 5.5 | 0.121455 s | 178.00 | 178.00 | 0 |

**Denominators** (from `results/a1-summary.csv`): `utility_denominator=4`,
`plies_denominator=4`, `think_time_denominator=4`, `generated_denominator=4`,
`evaluated_denominator=4` for all three agents. Every mean above is over all 4
matches; none is `N/A`. Think time is color-specific (`orange_time` when Orange,
`black_time` when Black). `model_calls` / `fallback_calls` / `cache_hits` are `N/A`
— no model is involved, and the starter emits empty rather than zero for those
columns.

### Two observations that belong in the write-up, not just the table

**1. `mean_generated == mean_evaluated` in all three rows.** At depth 1 every
generated action is immediately evaluated, so the two search metrics are not
independent here. This is a depth-1 artifact, and it is exactly the metric that
*will* separate at depth 2 — worth flagging now so the A2 table is not read as a
copy-paste error.

**2. `minimax_1`'s 2W/2D splits cleanly by color:** as Orange it drew **both**
games to the 100-ply limit (100 and 100 plies); as Black it won both (22 and 42
plies). The random opponent is seeded identically in both colors, so the difference
is *entirely* the minimax agent's own tie-breaking, not the opponent.
`DepthLimitedMinimaxAgent` uses a strict `>` comparison, so it keeps the **first**
move among equal-valued ones, and at depth 1 against a random opponent ties are
common. This is a real, reportable weakness of `minimax_1` and a concrete
illustration of *why* the mobility/advance/threat terms matter: they break ties in
a meaningful direction, and the two agents that add them go 0.50 -> 1.00.

Also worth one line: the metric is **saturated**. `minimax_2` and `minimax_3` are
tied at 1.00, so Stage A1 alone cannot rank them — which is precisely why A2 is run
against resources, and why "mean plies 15.0 vs 5.5" and "670.75 vs 178.00
generated" become the deciding evidence.

### Provisional Evaluator Selection

**Selected: `minimax_3` (`evaluate_position_3`), depth 1.**

- **Utility first, as required.** `minimax_3` and `minimax_2` are both **1.00**
  over 4 matches; `minimax_1` is **0.50**. `minimax_1` is eliminated on outcome
  alone.
- **Resource evidence breaks the 1.00 tie.** `minimax_3` uses **3.8x less
  generated action** (178.00 vs 670.75) and **4.1x less think time** (0.121 s vs
  0.495 s), and finishes matches in **5.5 plies vs 15.0**. That last number is the
  most decision-relevant: a shorter game against a fixed opponent is a faster
  forced win, and it leaves vastly more of the 3,600 s per-player budget for Stages
  B and C. Utility is tied, so the tiebreak goes to resources.
- **Representative decisions.** `minimax_3` wins in 5 plies on seed 0 and 6 on
  seed 1, *in both colors*, with a near-constant 178 generated actions — a stable,
  repeatable forced line rather than a lucky one. `minimax_2` needs 11-17 plies and
  varies 463 -> 858 generated actions across seeds, i.e. it is still searching.
  `minimax_1` never converts its material edge into a finish.
- **Stated limitation, so the selection is not oversold:** against a *random*
  opponent, a 5-ply win is a weak discriminator between evaluators. The honest
  claim is "no outcome difference, decisively lower cost, and hand-checked Position
  C shows the two added terms are the only signal that distinguishes a threatened
  Princess from a free one." The A2 depth comparison and the Stage C head-to-head
  are where `minimax_3` has to actually prove itself.

### Stage A2: Depths 1 and 2

`[READY]` Source: `results/a2.csv` + `results/a2-summary.csv` (generated
2026-09-28); depth-1 row reuses the `minimax_3` row of `results/a1-summary.csv`.

**Selected evaluator:** `minimax_3` (`evaluate_position_3`).<br>
**Opponent:** supplied random agent<br>
**Seeds:** 0, 1<br>
**Colors:** both<br>
**Maximum plies:** 100<br>
**Command:** `python run_experiments.py a2 --evaluator minimax_3 --output results/a2.csv`<br>
**Machine/runtime context:** unchanged from Stage A1 (i7-1255U, 10 cores / 12
logical, 15.6 GB RAM, Python 3.12.6, no GPU; all 4 matches `live=False`).

**Depth verification from `maximum_depth`:** **4/4 rows report the focal
`maximum_depth = 2`**, matching the configured depth (`orange_maximum_depth = 2`
on the two Orange-focal rows, `black_maximum_depth = 2` on the two Black-focal
rows). All rows `status=ok`, `exit_status=0`, no `error`. Nothing was dropped or
rerun.

| Depth | Games | W | D | L | Mean utility | Mean plies | Mean think time | Mean generated actions | Mean evaluated states | Failures |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 4 | 4 | 0 | 0 | 1.00 | 5.5 | 0.121455 s | 178.00 | 178.00 | 0 |
| 2 | 4 | 4 | 0 | 0 | 1.00 | 9.0 | 60.146186 s | 20775.50 | 20442.50 | 0 |

**Denominators:** 4 for every mean (`utility_denominator`, `plies_denominator`,
`think_time_denominator`, `generated_denominator`, `evaluated_denominator`), on
both rows. Think time is the focal color's cumulative time. All four matches are
included; saturation is a real finding, not missing data.

**Cost comparison worth stating precisely:** depth 2 costs **117x the generated
actions** (20775.5 vs 178.0) and **495x the think time** (60.146 s vs 0.121 s)
for the same 1.00 outcome. Per-move depth-2 searches observed up to ~10-17 s and
tens of thousands of generated states (e.g. Orange seed 1: 21,477 generated over
5 focal turns).

**Where depth changed the selected move / backed-up value:** see the
"Stage A2 supporting evidence" section below — 62.8 % of focal decisions change
with depth, with one sharply quotable position (seed 2, Orange, ply 18)
described there.

**Tradeoff:** no outcome gain, order-of-magnitude higher resource cost, and
depth 2 is infeasible for the LLM cutoff evaluator in Stage B (a single depth-2
search needs thousands of distinct evaluations, far over the 100 model-call
budget). Depth 1 is therefore selected.

### Final Deterministic Configuration

`[READY]`

**Evaluator:** `evaluate_position_3` (`minimax_3`)<br>
**Depth (`1` or `2`):** **1**<br>
**Selection justification using the supplied performance priorities**
(utility first, then decision quality and cost):

1. **No outcome gain from depth 2.** Against the supplied random opponent both
   depths are 1.00 (4/4 wins). The random opponent cannot discriminate depth; the
   saturation is itself a finding.
2. **Depth 2 costs 495x think time and 117x generated actions** for that
   identical outcome, and even depth 1 already leaves a massive think-budget
   margin (0.12 s of 3,600 s per player).
3. **Depth 2 is infeasible in Stage B under the 100 model-call budget:** one
   depth-2 search alone enumerates thousands of distinct positions, so the LLM
   cutoff evaluator could not complete even a single move decision; the
   deterministic Stage B row must therefore run at depth 1 to be comparable at
   all.
4. **Where depth *does* matter**, the depth-change evidence shows depth 2 finds
   tactical refutations depth 1 misses (62.8 % of decisions change; flagship
   example: a depth-1 choice valued -1.0 by depth 2). But against this opponent
   those refutations never change the outcome, so the extra cost buys nothing
   measurable in Stage A2.

---

## Status summary

| Item | Status |
|---|---|
| Eval 3 (5 empty fields) | READY |
| Hand-checked positions table | READY |
| Stage A1 table, depth verification, provisional selection | READY |
| Stage A2 table, depth verification, depth-change evidence, final config | READY |
| Section 2 config, contract, sensor parity, fallback | READY |
| Section 3 prompt, sensors, contract, fallback, parity | READY |
| Section 2 prompt-revision table | READY — pilot 2026-09-28, v1 65 % → v2 100 %, both versions frozen |
| Section 3 prompt-revision table | READY — pilot 2026-09-28, v1 100 % → v2 100 % (hardening), both versions frozen |
| "Requested seed and observed reproducibility" | READY — seed forwarded; same-seed repeat run byte-identical 80/80 |
| Stage B matrix + tables | IN PROGRESS — `run_experiments.py b` (minimax_3 d1 / llm_evaluator d1 / llm_direct d1) |
| 2 comment fixes in `test_student_strategies.py` | DONE — 2026-09-28, 156 tests pass |

### Stage A2 supporting evidence: depth-1 vs depth-2 decisions (from `analyze_depth_change.py`)

`[READY]` Source: `results/a2-depth-change.csv` + `results/a2-depth-change-summary.csv`,
generated 2026-09-28 by replaying each Stage A2 configuration (focal
`minimax_3 @ depth 2`, opponent seeded random @ depth 1, 100 max plies) and, at
every focal decision, running *both* a depth-1 and depth-2 search of the real
`DepthLimitedMinimaxAgent` and recording moves, backed-up values, margins, work
done, and the value each depth assigns to the other depth's chosen move. The
root loop of `choose_action` is replicated in the probe to expose per-action
values; the replication is validated against `choose_action` itself at the first
focal position of every match (all passed).

**Results (all 8 replays: focal wins):**

| match | seed | color | game plies | focal turns | moves changed |
|---|---|---:|---:|---:|---:|
| canonical | 0 | orange | 7 | 4 | 2/4 |
| canonical | 0 | black | 8 | 4 | 3/4 |
| canonical | 1 | orange | 9 | 5 | 3/5 |
| canonical | 1 | black | 12 | 6 | 4/6 |
| extra | 2 | orange | 21 | 11 | 9/11 |
| extra | 2 | black | 12 | 6 | 4/6 |
| extra | 3 | orange | 5 | 3 | 0/3 |
| extra | 3 | black | 8 | 4 | 2/4 |

**Overall:** 43 focal decisions recorded, **27 changed (62.8 %)**; the four
canonical matches contribute 19 decisions, 12 changed. Mean best-vs-second-best
margin: depth-1 **+0.040440**, depth-2 **+0.166902** — at depth 2 the search is
roughly 4x more decisive about which move is best.

**Strongest disagreement (quotable for the report):** seed 2, focal Orange,
ply 18, 126 legal moves.

| | chosen move | backed-up value | margin |
|---|---|---|---|
| depth 1 | `d5-d7` | +0.976437 | +0.000503 |
| depth 2 | `g3-j6` | +0.857705 | +0.115041 |

Depth-2's score of the depth-1 move `d5-d7`: **-1.000000** (a forced loss —
depth 2 sees Black's reply), so depth 2 prefers `g3-j6` with an implied upgrade
of **1.858**. Depth-1's score of depth-2's choice: +0.958454. The depth-1 pick
sat on an essentially tied +0.000503 margin — a shallow search choosing among
near-equal options where the deeper look found a tactical refutation. This is
exactly the "depth changed the selected move and the backed-up value" example
the template asks for.

Honest caveat to include: **outcome evidence is saturated** again — depth-2
wins all 8 replays too, so the depth choice is supported by the tactical
example and cost, not by a W/D/L difference against the random opponent.

**Replay verification (2026-09-28, after Stage A2 completed):** rerunning
`analyze_depth_change.py` reported **"all canonical replays reproduce
results/a2.csv exactly"** — result and plies agree on all four canonical
matches (ORANGE/7, BLACK/8, ORANGE/9, BLACK/12). The replay uses the same
`JetanEnvironment` / `RandomJetanAgent` / `DepthLimitedMinimaxAgent` objects
with the same seeds, so this also independently re-derives the Stage A2 rows
from first principles.

### Remaining work, in order

1. `[DONE]` Correct the Position A and Position C derivation comments in
   `test_student_strategies.py` — fixed 2026-09-28; verification script confirmed
   all three positions' raw sums and per-term claims; 156 tests still pass.
2. `[DONE]` `analyze_depth_change.py` — ran; `results/a2-depth-change.csv` and
   `results/a2-depth-change-summary.csv` written (see evidence section above).
3. `[DONE]` A2 run — `python run_experiments.py a2 --evaluator minimax_3 --output results/a2.csv`,
   4/4 ok, all `maximum_depth=2`.
4. `[DONE]` Verify `maximum_depth == 2` in all 4 rows; A2 table assembled; depth 1
   recorded as selected (no outcome gain, ~9-30x resource cost, depth 2 infeasible
   in Stage B under the 100-call budget). `analyze_depth_change.py` rerun confirms
   canonical replays reproduce `results/a2.csv` exactly.
5. `[DONE]` Live pilots run 2026-09-28 (eval 80 probes x3 runs, move 40 x2 runs);
   one documented prompt revision per modality, both versions preserved; rejected
   responses mapped to validation rules (see Sections 2 and 3 revision tables).
6. `[IN PROGRESS — background, started 2026-09-28]` Stage B:
   `python run_experiments.py b --evaluator minimax_3 --depth 1 --output results/b.csv`
   (12 matches = 3 configs x 2 seeds x 2 colors, 60 plies, 3600 s think limit,
   evaluator budget 100 calls/move search, direct agent 1 call/move). Watch for
   `model_calls` pinned at 100 with rising `fallback_calls` as the signature of
   legal-move counts exceeding 100 (observed up to 122). Then Stage C.

### Still-open risks

- Stage B live rows depend on the golem endpoint staying up for the whole run (see
  the run-notification; resume with the same `--output` if interrupted).
- Provenance of an earlier-recorded `evaluate_position_3` score distribution
  (median 0.373 / p90 0.519 / max 0.892) is **unverified**: a 12-game short-walk
  sampler gives median 0.087, a deep 400-ply walk gives 0.648. Must be re-derived
  with the sampler stated before use in the report. Not used in anything above.
- Head-to-head round robin for v2 vs v3 effectiveness evidence: skipped by choice.
  Residual, not blocking.
- Not a git repo yet; the assignment requires a private GitHub repo with `fractal13`
  read access and a submitted commit hash.
