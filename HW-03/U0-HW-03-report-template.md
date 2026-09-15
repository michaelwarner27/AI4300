# Planning Cloud Service Recovery

**Student:** Michael Warner 
**Repository URL:** [Private GitHub repository URL]  
**Repository access:** [Confirm that `fractal13` has at least read access]  
**Submitted commit:** [Commit hash]

## 1. PEAS Assessment

### Performance Measure

The agent's goal is to go from the given starting state to the goal state where the storage, database, auth, api, traffic, and validation are all in the positive state. A high performing algorithm minimizes path cost. The agent must only take legal steps to get there. An additional metric is the computation cost.
Illegal actions, ending on any state that isn't the goal or a plan that exceeds 25 steps fails.
An example of a plan that fails is one that calls RESTART_STORAGE while traffic is in the normal state. That is an illegal action so it fails.
### Environment

Each of 7 variables (storage, database, auth, api, traffic, backup, validation) have 2-3 states that range from down to healthy. Each of these variable represent the state of our services.

### Actuators

There are 10 actions that change the states of the 7 variables. Every action has preconditions in able to perform.
Every legal action changes the recovery state deterministically. Some examples of the limits to what actions are legal are: 
You can only restart storage when traffic is isolated and storage is down
You can only restart the database when traffic is down, storage is healthy, and the database is down.
You can only restart the API when it is down or faulty and auth is healthy.
### Sensors

At the start and after every action performed by the agent, it receives the current RecoveryState. This supplies the agent with the states of all 7 variables (storage, database, auth, api, traffic, backup, validation). This allows the agent to test for the goal state and determine legal actions.

### Environment Classification

| Dimension | Classification | Case-specific justification |
|---|---|---|
| Observability | Fully | The state is fully observable because after every action the agent receives a complete recovery state which contains all of the information about the environment. |
| Outcomes | Deterministic | Each of the 10 actions the agent can perform has exactly 1 outcome state, given the input state. |
| Temporal structure | sequential | Every state depends on the previous state with the action the agent took. |
| Dynamics | Static | The environment only changes when the agent acts on it. |
| State and actions | Discrete | There are a finite number of possible states. |
| Multiplicity | Single-agent | Only our agent is able to act on the system |
| Knowledge | Known | The environment's rules are completely laid out in the instructions so the agent will have complete knowledge of the environment. |

### Modeling Limitation

A real situation would not be static. It would be possible for states like the database condition or storage conditions could change without the agent acting. The network condition would also change based on other traffic or the condition of hardware. 
This would prevent the agent from making a plan because the states would change between the time it made the plan and the time it executed it.

## 2. Problem Formulation

### Initial State

[Explain how an episode supplies its initial `RecoveryState`.]

### Actions

[Explain how `actions(state)` identifies legal actions and preserves the
required order.]

### Transition Model

[Explain how `result(state, action)` produces a new immutable state and handles
an illegal action.]

### Goal Test

[State the complete goal condition.]
Storage, database, auth, and api are all healthy. Traffic is normal and the validation passed.

### Path-Cost Function

BFS searches for the shortest path not the cheapest. If the frontier contains edges that cost 7 and 2, BFS doesn't care which one is taken first while UCF will take the edge that costs 2. If that edge reveals edges that cost 3 and 8 it will take the edge that costs 3 and continue down that path until the total cost is greater than 7 before it will ever explore that first 7.

## 3. Uniform-Cost Search

[Explain frontier priority, deterministic tie-breaking, `best_g`, stale-entry
handling, reopening, parent links, and plan reconstruction. Include a short code
excerpt only if it helps explain a decision.]
When looking at the frontier the next path taken is determined by the cheapest total path cost. Tie breaks go in Action order as declared in recovery.py. Stale-entries (when an node that has already been found is found again with a cheeper path) are handled by updating the node in the frontier priority queue rather than adding it a second time. I will keep track of parent links by storing where the path came from as we pop nodes off the priority queue. This way I can reconstruct the path from start to goal simply.

## 4. A* and Heuristic

### Heuristic Definition

[Define `h(state)` precisely enough that another programmer could implement it.]
I didn't really get to this part but the heuristic should prioritize states that need to happen for other state changes to occur. Isolating the network should have the highest value followed by storage, database... turns out this is just Action order. I think giving actions a heuristic based on the defined Action order would work fairly well.
### Admissibility Justification

[Describe the relaxed problem or unavoidable costs used as a lower bound.
Explain why the heuristic cannot overestimate and why it is zero at a goal.
Address any possible double counting.]


### A* Implementation

[Explain how A* uses `f = g + h` while retaining the required graph-search and
reopening behavior.]

## 5. Aggregate Results

**Experiment command:** `[exact command]`  
**Evaluation scenarios:** [Confirm all 50 supplied scenarios were included]  
**Execution limit:** 25 actions

Use `N/A` where a metric does not apply. Do not replace failed runs with missing
values.

| Agent | Success rate | Mean cost | Mean actions | Mean generated | Mean expanded | Mean peak frontier | Mean planning time |
|---|---:|---:|---:|---:|---:|---:|---:|
| Random | [ ] | [ ] | [ ] | N/A | N/A | N/A | N/A |
| Uniform-cost | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| A* | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |

### Paired Optimal-Cost Check

**Number of UCS/A* cost mismatches:** [ ]

[If the value is zero, state that clearly. Otherwise, identify and investigate
every mismatched scenario rather than removing it from the results.]

## 6. Selected-Scenario Comparison

**Scenario:** [Identifier or seed]  
**Initial state:** `[RecoveryState(...)]`

| Agent | Returned plan | Plan cost | Generated | Expanded | Peak frontier | Planning time |
|---|---|---:|---:|---:|---:|---:|
| Uniform-cost | [Actions] | [ ] | [ ] | [ ] | [ ] | [ ] |
| A* | [Actions] | [ ] | [ ] | [ ] | [ ] | [ ] |

[Explain how the heuristic changed frontier priorities and why the expanded-node
counts differ.]

## 7. Analysis

### Effectiveness

[Did UCS and A* differ in success or solution cost? What does the evidence say
about their effectiveness on these scenarios?]

### Efficiency

[Compare generated nodes, expanded nodes, peak frontier, and runtime.]

### Runtime and Node Counts

[Explain why runtime can disagree with node-count measurements on this small
problem.]

### Random Baseline

[Explain what the random baseline demonstrates and what it cannot establish
about search optimality.]

### External Validity

[Explain one limitation of the deterministic simulation that would matter in a
real outage.]

## 8. Testing and Reproducibility

**Test command:** `[exact command]`

[Summarize your action/transition, goal-test, UCS optimal-cost, and heuristic
tests. Report the result of the full test run. Explain why the expected
non-goal heuristic values used in your tests are admissible.]

## 9. AI-Assistance Disclosure

**Tools used:** Gemini, Open Code: Big Pickle
**Material effect:** Debug and writing tediously long if statements based on the example one that I wrote
**Verification:** 
I checked that every statement matched the instructions and what I wanted.

## Submission Checklist

- [ ] The report includes every required section, and aggregate results include
      all 50 scenarios.
- [ ] The repository URL, access confirmation, and submitted commit are listed.
- [ ] GitHub user `fractal13` has at least read access.
- [ ] The submitted commit passes the complete test command.
- [ ] UCS and A* cost mismatches are reported and investigated.
- [ ] The PDF has selectable text, real headings, logical reading order, and
      identified table headers.
- [ ] Charts, if any, are explained in text and do not rely on color alone.
- [ ] The AI-assistance disclosure is complete.
- [ ] No credentials, keys, or access tokens are present in the repository.
