# AI HW-07: Rescue Rover Report

# Michael Warner
# Github link https://github.com/michaelwarner27/AI4300/tree/main/U0-HW-07
# Commit Hash
## 1. Terrain Encoding (At-Least-One and Pairwise Exclusion)

Every tile has 4 base clauses added to the Knowledge Base (KB) before any scans take place:

* **At-Least-One Clause:** Declares that each tile must be in at least one of the three possible states (`stable`, `slippery`, or `fragile`). For the KB to remain consistent, at least one of these three literals in the first clause must evaluate to true.
* **Pairwise Exclusion Clauses:** The next three clauses create mutual exclusions to ensure a tile can only occupy one state at a time. The disjunctions $(\neg\text{stable} \lor \neg\text{slippery})$, $(\neg\text{stable} \lor \neg\text{fragile})$, and $(\neg\text{slippery} \lor \neg\text{fragile})$ verify that the tile is not in two states simultaneously.

### Code Excerpt (`agents.py`, lines 187–220)

```python
for row in range(percept.rows):
    for column in range(percept.columns):
        # At least one category is true
        self.knowledge.add_clause(
            Literal(f"stable_{row}_{column}", True),
            Literal(f"slippery_{row}_{column}", True),
            Literal(f"fragile_{row}_{column}", True),
        )
        
        # Mutually exclusive pairs (using add_clause for disjunctions)
        # ~stable OR ~slippery
        self.knowledge.add_clause(
            Literal(f"stable_{row}_{column}", False),
            Literal(f"slippery_{row}_{column}", False),
        )
        # ~stable OR ~fragile
        self.knowledge.add_clause(
            Literal(f"stable_{row}_{column}", False),
            Literal(f"fragile_{row}_{column}", False),
        )
        # ~slippery OR ~fragile
        self.knowledge.add_clause(
            Literal(f"slippery_{row}_{column}", False),
            Literal(f"fragile_{row}_{column}", False),
        )
```

---

## 2. Inference from Overlapping Scans

Consider a simplified $3 \times 3$ grid:

* **Row 0:** [Fragile, Stable, Stable]
* **Row 1:** [Stable, Stable, Stable]
* **Row 2:** [Stable, Stable, Stable]

Scanning from top-left to right and then down yields the following structural scan returns:

1. **Clause 1 (Scan 1):** $(\text{Fragile}_{0,2} \lor \text{Fragile}_{1,2} \lor \text{Fragile}_{0,1} \lor \text{Fragile}_{1,1})$
   * *Analysis:* On its own, this clause only indicates that at least one tile in the group is fragile.
2. **Clause 2 (Scan 2):** $\neg(\text{Fragile}_{1,2} \lor \text{Fragile}_{2,2} \lor \text{Fragile}_{1,1} \lor \text{Fragile}_{2,1})$
   * *Analysis:* Helpful alone; proves none of tiles $(1,2)$, $(2,2)$, $(1,1)$, or $(2,1)$ are fragile.
3. **Clause 3 (Scan 3):** $\neg(\text{Fragile}_{0,1} \lor \text{Fragile}_{0,0} \lor \text{Fragile}_{1,1} \lor \text{Fragile}_{1,0})$
   * *Analysis:* Helpful alone; proves none of tiles $(0,1)$, $(0,0)$, $(1,1)$, or $(1,0)$ are fragile.

### Entailed Conclusion
Combining these three clauses, Scans 2 and 3 eliminate tiles $(1,2)$, $(1,1)$, and $(0,1)$ from being fragile. Since the KB requires at least one tile among the first group to be fragile, eliminating the other three confirms that tile $(0,2)$ is **Fragile**.

---

## 3. Observation Processing, Consistency Maintenance, and Reset Behavior

* **Single-Processing of Observations:** The `act()` function only scans when the outcome to the last scan exists and the count is greater than the previously processed action, preventing replayed percepts from adding duplicate clauses. Scanned windows are stored as sorted tuples of coordinates to skip already scanned regions.
* **Consistency Verification:** `knowledge.is_consistent()` is executed after every scan and prior to action selection.
* **Episode Reset (`reset()`):** Re-initializes a new KB instance and sets all tracking values to default state.

---

## 4. Search Formulation and Trace Analysis

### Problem Formulation
* **Search State:** The rover position as `(row, column)`.
* **Search Graph Construction:** Built exclusively from tiles that the KB entails as **not fragile**. Fragile and unknown tiles are excluded.
* **Successor Function:** Up to 4 tiles orthogonally adjacent to the current position that are present in the search graph.
* **Goal Test:** Checks if the rover position equals the target location (the module or exit).
* **Path-Cost & Heuristic:** $g(n) = 3$ per move, and $h(n)$ is the Manhattan distance to the current goal. Traction cost is not currently included in the search cost calculation.

### Planned Actions Trace
* **Scenario Info:** Training scenario 0, agent seed 4300 ($4 \times 5$ map; start `(0,0)`, module `(2,2)`, exit `(3,4)`).
* **Execution Summary:** Status: Success, Score: 9908, 43 total actions (30 scans: 22 structural + 8 surface; 13 execution actions).

| Step | Action | Score Change | Notes |
| :--- | :--- | :--- | :--- |
| 31 | enable traction | -1 | |
| 32 | move (1,0) | -6 | Stable tile entered with traction |
| 33 | disable traction | -1 | |
| 34 | move (2,0) | -3 | |
| 35 | move (2,1) | -3 | |
| 36 | enable traction | -1 | |
| 37 | move (2,2) | -3 | Slippery tile, no surcharge |
| 38 | retrieve | -1 | |
| 39 | move (3,2) | -6 | |
| 40 | disable traction | -1 | |
| 41 | move (3,3) | -3 | |
| 42 | move (3,4) | -3 | |
| 43 | stop | +10000 | |

---

## 5. Agent Performance Comparison

| Measure | Random | Logic |
| :--- | :--- | :--- |
| **Success** | 0/900 (0.0%) | 900/900 (100%) |
| **Stopped** | 496 (55.1%) | 0 |
| **Timeout** | 308 (34.2%) | 0 |
| **Destroyed** | 96 (10.7%) | 0 |
| **Mean Score (min, max)** | -342.7 (-1479, -100) | 9807.9 (9779, 9832) |
| **Mean Actions** | 55.7 | 89.7 (81–100) |
| **Mean Moves** | 1.72 | 9.07 |
| **Mean Scans** | 52.3 | 76.5 (70–82) |
| **Mean Traction-Enabled Moves** | 0.30 | 5.50 |
| **Mean Traction Toggles** | 0.64 | 2.17 |
| **Unprotected Slips (total)** | 298 (mean 0.33) | 0 |
| **Invalid Actions (total)** | 464 (mean 0.52) | 0 |

### Performance Analysis & Failure Explanation
* **Random Agent Failures:** The random agent regularly selects `stop` with no underlying reason. It also frequently fails due to timeouts, destruction, unprotected slips, and invalid actions.
* **Logic Agent Rules:** The Logic agent only stops when it possesses the module and has arrived at the exit. It incurs zero unprotected slips by defaulting traction to ON whenever stepping onto unknown tiles.
* **Score Comparison:** The score difference is dominated by room completion. The Logic agent successfully plans safe routes to the module and exit without crossing fragile tiles.

---

## 6. Policy Limitations & Proposed Revision

* **Current Limitation:** The agent scans every single tile in the room for structural integrity, pushing total action counts near the maximum limit on every run.
* **Proposed Revision:**
  1. Begin by scanning tiles along a straight path toward the goal.
  2. Run $A^*$ search to check if a valid path exists.
  3. If no path exists, expand the scan region outward by one tile on either side of the straight path at a time, re-running $A^*$ after each set of scans.
* **Expected Impact:**
  * **Reduced Scans:** Limits structural scans needed to find a valid path.
  * **Traction Budget:** Allows budget to execute surface scans on path tiles instead of defaulting to slippery assumptions.
  * **Potential Trade-off:** Direct path scanning rapidly finds paths but may occasionally miss shorter optimal paths outside the initial scan window.

---

## 7. Suite Scope & Limitations

* **Supported Conclusions:** The fixed suite confirms the effectiveness of propositional logic and $A^*$ search. Under seed 4300, the Logic agent completed all 900 runs with zero unprotected slips and zero invalid actions, while the Random agent completed none.
* **Required Testing:** Testing on larger maps or different distributions requires additional map suites. The current logic implementation is inefficient and would likely exhaust move budgets on larger environments.

## 8. AI Disclosure

* **Tools Used** Opencode, Claude, and Gemini
* **Effect** I maintained in control of the project informing my agents of what I needed implemented such as the requirements and implementation overview for the propositional logic checks. My agents largely had control over creating and running the tests, but I verified the output confirming the tests did what they were supposed to. This report was written by me except for the tables which I had my agent generate base on a example we traced and the data from the tests. Lastly this document was reformatted by Gemini to make it readable markdown, but the words are mine.