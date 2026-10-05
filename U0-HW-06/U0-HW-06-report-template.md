# Alpha-Beta Pruning and Move Ordering in Jetan

**Student:** [Michael Warner]  
**Private repository:** [URL]  
**Access:** [Confirm `fractal13` has read access]  
**Submitted commit:** [Hash]  
**Inherited U0-HW-05 commit:** [Hash]

## 1. Selected U0-HW-05 Evaluation Function

**Definition:** [evaluate_position_3 returns 0.99 * tanh(S / 20).  S = material + 0.2*mobility + 0.15*advance + 0.5*threat + 1.0*escape. Every term is the difference between self and opponent. 

Material is set between 1.7 for Padwar to 21 for the princess. Those scores are based on those given in the Jetan wikipedia page. 

Mobility is the difference in legal moves. 

Advance is a term the counts pieces developing off of their home position.

Threat is the difference between pieces the opponent can attack to piece perspective can attack. 

Escape is the difference of both sides princess' using their escape.]  
**Range and perspective:** [tanh is bounded and odd, maintaining the [-.99, .99] range. The score is fixed to the perspective player's view.]  
**Existing function and registration:** [The function is evaluate_position_3 in student_strategies.py. The HW 5 minimax registration is minimax_3. hw06_config.py imports it as SELECTED_EVALUATOR]  
**Unchanged-behavior verification:** [Run experiments takes the SELECTED_EVALUATOR to measure minimax and hw06 registration passes the same object to both of my alpha beta variants.]

## 2. Alpha-Beta Implementation

[Recurrence: every call counts the visit, then checks if it a terminal. If it is it returns its utility. If it is non-terminal at the max depth it returns the evaluator value. Otherwise it calls board.actions() counts them and orders them with self.ordering().

Max: keeps track of the highest value accessible from child nodes.

Min:  keeps track of the lowest value availible to the min node and lowers the beta cuttoff accordingly.

Pruning Count:  on a cuttoff it adds the number of moves left. These are the unvisited sibling nodes.

Fixed Perspective: Perspective remains the same to preserve the score.

Root Ties: chooses canonical first action on a tie.
]

### Student-Created Alpha-Cutoff Test

**Depth-2 tree:** [The root is ORANGE (MAX) with three BLACK (MIN) children, a, b and c, each with two terminal leaves. Traversal order is root, a, a1, a2, b, b1, (b2 skipped), c, c1, c2.]  
**Leaf values:** [a1 0.6, a2 0.8, b1 0.4, b2 0.9, c1 0.7, c2 0.9. ]  
**Alpha established by the first root branch:** [ alpha = 0.6]  
**Later MIN-node value that triggers the cutoff:** [ at b, b1 = 0.4 sets beta = 0.4, and 0.6 ≥ 0.4.]  
**Sibling skipped by the cutoff:** [ b2]  
**Test assertion and observed result:** [ The move is c and the value is 0.7, matching measured minimax.
Metrics are (visited, expanded, generated, evaluated, pruned, max_depth) = (9, 4, 9, 5, 1, 2).
Without pruning, 10 states would be visited.]

## 3. Move-Ordering Strategies

### Ordering 1

**Ranking procedure:** [Based on the victim's piece value. ]  
**Expected cutoff relationship:** [A valuable capture will create a large alpha or beta causing an earlier cuttoff. ]  
**Computational cost:** [O(1) per action. One board lookup and one dict lookup ]  
**Canonical tie rule:** [First in canonical order. ]  
**Likely weakness:** [Quiet positions without captures. In the early game every move is quiet so it orders soley on canonical order. ]

### Ordering 2

**Ranking procedure:** [This ordering ranks the numer of squares the piece will be able to attack. ]  
**Expected cutoff relationship:** [Ordering based on a pieces position prioritizes aggressive moves that have the abillity to make large captures. This tightes alpha and beta with larger utilities causing a cuttoff sooner. ]  
**Computational cost:** [Significantly more than ordering 1. It calls result and two attack-sets scans at every root node. Work is repeated for every child node]  
**Canonical tie rule:** [The same stable sort ]  
**Likely weakness:** [This ordering ignores material so a capture can get sorted behind a quiet move. Also because attacked_locations ignores the princess this ordering ignores her too. ]

**Distinctness evidence:** [The two orderings return different sequences in all six positions and at both perspectives, for example middle-capture, the Princess capture e4-f5 is first under Ordering 1 but not under Ordering 2, which starts with c3-d4. Both are permutations containing every legal action exactly once. test_orderings_differ_on_at_least_one_supplied_position and the permutation test both pass.]

## 4. Stage A: Minimax Equivalence

**Combined command:** `python3 hw06_run_experiments.py --output hw06_results`  
**Rows used here:** Stage A rows from the combined output  
**Positions:** Six supplied positions  
**Depth:** 2  
**Scheduled searches:** 18

| Position | Completed agent searches / 3 | Same root value? | Same selected move? | Maximum-depth check | Timeouts/errors |
|---|---:|---|---|---|---|
| initial | 3 | Yes | Yes | Yes, all 2 | 0 |
| early-a | 3 | Yes | Yes | Yes, all 2 | 0 |
| early-b | 3 | Yes | Yes | Yes, all 2 | 0 |
| middle-capture | 3 | Yes | Yes | Yes, all 2 | 0 |
| middle-threat | 3 | Yes | Yes | Yes, all 2 | 0 |
| reduced | 3 | Yes | Yes | Yes, all 2 | 0 |

[All 18 of the depth 2 searches completed with no errors of timeouts. Each of the 6 positions, measured minimax, ordering 1, and ordering 2 all returned the same root value and selected move. This is correct because alpha beta is meant to return the same move as minimax while removing some of the work. This shows that pruning did not change the outcome for this example. The cost savings were significant, minimax visited 2,736 states, while Ordering 1 visited 718, and ordering 2 visited 404.]

| Position | Agent | Status/error | Visited states | Expanded nodes | Generated actions | Evaluated states | Pruned actions | Max depth | Seconds |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| initial | Measured minimax | ok | 2736 | 53 | 2735 | 2683 | 0 | 2 | 1.487 |
| initial | Ordering 1 | ok | 718 | 53 | 2735 | 665 | 2018 | 2 | 0.416 |
| initial | Ordering 2 | ok | 404 | 53 | 2735 | 351 | 2332 | 2 | 0.834 |
| early-a | Measured minimax | ok | 3171 | 57 | 3170 | 3114 | 0 | 2 | 2.234 |
| early-a | Ordering 1 | ok | 1087 | 57 | 3170 | 1030 | 2084 | 2 | 0.726 |
| early-a | Ordering 2 | ok | 406 | 57 | 3170 | 349 | 2765 | 2 | 1.248 |
| early-b | Measured minimax | ok | 4008 | 62 | 4007 | 3946 | 0 | 2 | 3.104 |
| early-b | Ordering 1 | ok | 2070 | 62 | 4007 | 2008 | 1938 | 2 | 1.532 |
| early-b | Ordering 2 | ok | 580 | 62 | 4007 | 518 | 3428 | 2 | 1.638 |
| middle-capture | Measured minimax | ok | 3473 | 31 | 3472 | 3442 | 0 | 2 | 1.564 |
| middle-capture | Ordering 1 | ok | 2413 | 31 | 3472 | 2382 | 1060 | 2 | 1.070 |
| middle-capture | Ordering 2 | ok | 554 | 31 | 3472 | 523 | 2919 | 2 | 0.756 |
| middle-threat | Measured minimax | ok | 6096 | 79 | 6095 | 6017 | 0 | 2 | 4.815 |
| middle-threat | Ordering 1 | ok | 423 | 79 | 6095 | 344 | 5673 | 2 | 0.253 |
| middle-threat | Ordering 2 | ok | 423 | 79 | 6095 | 344 | 5673 | 2 | 2.594 |
| reduced | Measured minimax | ok | 755 | 27 | 754 | 728 | 0 | 2 | 0.100 |
| reduced | Ordering 1 | ok | 340 | 27 | 754 | 313 | 415 | 2 | 0.043 |
| reduced | Ordering 2 | ok | 284 | 27 | 754 | 257 | 471 | 2 | 0.060 |

## 5. Stage B: Depth-3 Ordering Comparison

**Combined command:** `python3 hw06_run_experiments.py --output hw06_results`  
**Rows used here:** Stage B rows from the combined output  
**Per-search limit:** 30 seconds  
**Timing repetitions:** 3  
**Scheduled attempts:** 36

Use one row for each position and ordering. Mark unavailable values `N/A`.

| Position | Ordering | Completed / 3 | Move | Root value | Visited states | Expanded nodes | Generated actions | Evaluated states | Pruned actions | Max depth | Median seconds | Timeouts/errors |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| initial | Ordering 1 | 3 | d0-c3 | 0.5999 | 9854 | 384 | 21947 | 9470 | 12094 | 3 | 7.40 | None |
| initial | Ordering 2 | 3 | d0-c3 | 0.5999 | 3785 | 310 | 17479 | 3475 | 13695 | 3 | 8.28 | None |
| early-a | Ordering 1 | 3 | d0-c3 | 0.6188 | 10524 | 454 | 26435 | 10070 | 15912 | 3 | 7.75 | None |
| early-a | Ordering 2 | 3 | d0-c3 | 0.6188 | 4963 | 400 | 23098 | 4563 | 18136 | 3 | 11.42 | None |
| early-b | Ordering 1 | 2 | g0-f3 | 0.2656 | 36449 | 1360 | 90863 | 35089 | 54415 | 3 | 23.58 | 1 timeout (SearchTimeout) |
| early-b | Ordering 2 | 3 | g0-f3 | 0.2656 | 7556 | 600 | 39618 | 6956 | 32063 | 3 | 17.68 | None |
| middle-capture | Ordering 1 | 3 | e4-f5 | 1.0000 | 19877 | 1096 | 48786 | 18781 | 28910 | 3 | 9.48 | None |
| middle-capture | Ordering 2 | 3 | e4-f5 | 1.0000 | 19326 | 1521 | 63319 | 17805 | 43994 | 3 | 20.74 | None |
| middle-threat | Ordering 1 | 3 | e4-d3 | 1.0000 | 1468 | 355 | 24513 | 1113 | 23046 | 3 | 1.23 | None |
| middle-threat | Ordering 2 | 3 | e4-d3 | 1.0000 | 1670 | 361 | 24715 | 1309 | 23046 | 3 | 10.91 | None |
| reduced | Ordering 1 | 3 | a0-d3 | 0.0462 | 6492 | 443 | 14391 | 6049 | 7900 | 3 | 0.86 | None |
| reduced | Ordering 2 | 3 | a0-d3 | 0.0462 | 4286 | 251 | 8335 | 4035 | 4050 | 3 | 1.09 | None |

### Relative Expanded-Node Change

Use the `summary.csv` field
`relative_expanded_nodes_change_order2_vs_order1`, calculated as
`(Ordering 1 - Ordering 2) / Ordering 1`. Ordering 1 supplies the denominator;
it is not an unpruned reference.

| Position | Ordering 1 expanded nodes | Ordering 2 expanded nodes | Relative change | Interpretation |
|---|---:|---:|---:|---|
| initial | 384 | 310 | +0.193 | Ordering 2 expanded fewer nodes. |
| early-a | 454 | 400 | +0.119 | Ordering 2 expanded fewer nodes. |
| early-b | 1360 | 600 | N/A | Not computed in `summary.csv`: Ordering 1 completed 2 of 3 attempts (1 timeout). Counts shown come from completed searches; Ordering 2 expanded fewer. |
| middle-capture | 1096 | 1521 | -0.388 | Ordering 2 expanded more nodes. |
| middle-threat | 355 | 361 | -0.017 | Nearly equal; Ordering 2 expanded 6 more nodes. |
| reduced | 443 | 251 | +0.433 | Ordering 2 expanded fewer nodes. |

## 6. Analysis Questions

1. [Stage A supports alpha-beta correctness by getting the same result as minimax. alpha-beta is only meant to prune unneeded work not change the outcome. ]
2. [Cutoffs occur earlier when there are large alpha and beta (large negative) values. Ordering 1 uses piece value for this cutoff which works great when there is a possible capture but doesn't do well in the early game when there aren't captures available. Ordering 2 improves on this by accounting for the number of opposing pieces the piece's position can threaten. Moves that threaten a large number of squares create larger alpha and beta values. Allowing for it to prune early]
3. [Ordering 2 expanded fewer nodes as expected. 3,443 nodes compared to Ordering 1's 4092 summed over all the tests. In middle-capture 1 actually explored fewer nodes at 1521 compared to 2's 1096. Also in middle threat they were about equal at 361 and 355. The advantages reverse where captures matter because ordering 2 ignores material.]
4. [Not really. Because ordering 2 is so much more computationally heavy it often took longer despite exploring fewer nodes. However ordering 1 did timeout in early-b so some of the data is a little skewed. A great example is middle-threat where both 1 and 2 explored a similar number of nodes but 1 took 1.23 seconds adn 2 took 10.91 seconds. 2 Is not better at wall clock time.]
5. [Both minimax and alpha-beta are O(b^d) in the worst case but alpha-beta can approach O(b^(d/2)). In my tests alpha beta did prune heavily but did not approach that best case big O. Plus alpha-beta maintains a space occupancy of O(bd)]
6. [Use more positions per game phase, and run depth 2 to 4 and raise the time limit. Then report node counts per position as a distribution, with a paired comparison instead of a single total. ]

## 7. Testing and Reproducibility

**Python version and machine context:** Python 3.12.3 (standard library only), Linux 6.18 on WSL2 (x86_64), 12th Gen Intel Core i7-1255U (12 logical CPUs), about 7.9 GB RAM. Searches ran sequentially in one process per attempt.  
**Test command:** `python3 -m unittest -v test_hw06_framework test_hw06_student`  
**Test result:** Ran 18 tests, all OK (framework and student, including the student alpha-cutoff test).  
**Raw CSV:** `hw06_results/raw.csv`  
**Summary CSV:** `hw06_results/summary.csv`  
**Timeout/error handling verified:** The framework test `test_search_timeout_terminates_child_process` passes, and the CLI rejects a non-positive `--timeout`. In the final run, one attempt (early-b, Ordering 1, repetition 3) hit the 30 s limit. It is retained in `raw.csv` with status `timeout`, error type `SearchTimeout`, and `N/A` for all unavailable values. `summary.csv` shows 2 of 3 completed searches for that row and `N/A` for its relative change. There were no other timeouts or errors.

## 8. References

[List sources used for search, pruning, ordering, or evaluation design.]

## 9. AI-Assistance Disclosure

**Tools:** [opencode with Anthropic's Claude Sonnet 5.5]  
**Material effect:** [Assisted with development, material understanding, testing, and data reading. ]  
**Verification:** [I revoked any changes I didn't want and wrote the report based on a compilation of my understanding and Claude's summary of the project. ]

## Submission Checklist

- [ ] Accessible `lastname-firstname-alpha-beta.pdf`.
- [ ] Selected U0-HW-05 evaluator and inherited commit identified.
- [ ] Alpha-beta and two distinct ordering functions implemented.
- [ ] All required tests pass.
- [ ] All Stage A and B attempts, statuses, and denominators reported.
- [ ] Raw and summary CSV files retained.
- [ ] Six analysis questions answered with bounded claims.
- [ ] Repository URL, submitted commit, and `fractal13` access included.
- [ ] References and AI-assistance disclosure completed.
- [ ] No credentials, `.env` files, or private endpoint details committed.
