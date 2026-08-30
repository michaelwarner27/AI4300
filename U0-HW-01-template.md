# PEAS Environment Assessment

**Student:** Michael Warner
**Environment:** Rubix Cube Robot
**Agent:** Rubix Cube Solver

## Executive Summary

This agent finds the shortest path from a random state of a rubix cube to the solved state. The environment is an isolated robot that is able to turn every face of a rubix cube. The agent assumes there will be no partial turns where a face of the cube is out of line. Performance is based on if the cube is solved and how many step it took to get there. It should be less than 20. The environment is the robot that is turning the cube. No other agents are able to act on it. The actuators are the motors of the robot. The sensors are color sensors and rotation sensors in the motors. These allow us to know the state of the cube, and if the robot is ready for the next instruction. This environment is fully observable, deterministic, sequential, static, discrete, and single agent.

## 1. Agent, Goal, and Scope

### Agent and Goal

This agent's primary goal is to take a rubix cube from a scrambled state to a solved one. The users are anyone who has a scrambled rubix cube they'd like solved. This agent is in control of the robotic mechanism that are able to physically move or change state of the rubix cube.

### Scope and Assumptions

The tasks begins when the agent is fed a scrambled rubix cube.
The task ends when the rubix cube has been solved

- **Assumption 1:** There are no partial turns. Any change in the rubix cube's state will produce a valid state. There is no way the cube ends up with a partial turn.
- **Assumption 2:** The environment will be single agent. Only this agent is able to change the state of the rubix cube.

## 2. PEAS Assessment

### Performance Measure

1. **[Solved]:** Are all 6 faces of the cube solved?
2. **[Steps]:** How many state changes did it take to get there?
3. **[Time]:** Was the algorithm to solve the cube calculated quickly?

**Tradeoff or priority:** 

The cube being solved is the absolute top priority. It being solved in the fewest steps possible is next. The amount of time taken to calculate is just a final optimization.

**Unacceptable outcome:** 
The cube not being solved, or the algorithm times out.

### Environment

- **People or other agents:** The person feeding the scrambled rubix cube to the agent, but that occurs before the agent needs to exist.
- **Objects and resources:** The motors that turn the physical cube.
- **Rules and constraints:** Only one state change is able to happen at a time. There are no in between states. The motors have a max speed.
- **Changing conditions:** Only the action this agent takes will affect the environment.

### Actuators

- **[Actuator or action]:** The motors are able to turn each face of the cube: left, right, top, bottom, front, back
- **[Actuator or action]:** Possible that motors can overheat, but that is mostly out of scope.

**Control limitation:** 
The agent is not able to make illegal state changes such as turning a corner, disassembling the cube, or switching between two states that are not adjacent.

### Sensors

- **[Color]:** Color sensor to detect the colors of every piece of the rubix cube. Requires good light.
- **[Motor]:** Motor degrees turned. The motors may not be as precise as we need them to be.

**Information limitation:** 

## 3. Environment Classification

Give a classification and case-specific justification for every dimension. If
the environment is mixed, state the dominant classification and explain the
important exception.

### Observability: [Fully observable / Partially observable]

Fully observable. Once the agent scans every side of the rubix cube it knows the position of every piece, because no other agents are able to act on this cube.

### Outcomes: [Deterministic / Stochastic]

Deterministic. The next state of the rubix cube is entirely determined by the current state with the single action the agent takes.

### Temporal Structure: [Episodic / Sequential]

Sequential. Every movement of the cube is relies on the previous state changes to move the pieces of the cube correctly.

### Dynamics: [Static / Dynamic]

Static. Only the actions of this agent are able to change the environment.

### State and Actions: [Discrete / Continuous / Mixed]

Discrete. There are a finite number of rubix cube states. 

### Multiplicity: [Single-agent / Multi-agent]

Single-agent. From the time the agent is given the rubix cube to the time it returns a solve one no other agents are able to act on it.

## 4. Agent-Environment Loop

Trace one concrete interaction cycle.

1. **Perceive:** [What percepts arrive through which sensors?]
2. **Decide:** [What decision must the agent make?]
3. **Act:** [Which actuator or output does the agent use?]
4. **Environment changes:** [What changes, including effects outside the agent's control?]
5. **Feedback:** [How does the agent or evaluator observe performance?]

1. Color sensors determine the current state of the rubix cube.
2. What is the shortest path from the current state to the solved state.
3. The agent commands the motors to turn the faces of the cube
4. Only the state of the cube changes to a legal adjacent state
5. A final scan of the cube to confirm it is solved

## 5. Design Evaluation

### Consequential Mismatch or Risk

Attempting to solve an unsolvable cube could lead to the agent running forever if not properly accounted for.
Trying to solve the cube in as little time as possible could lead the motors being less accurate
### Proposed Improvement

Focus on solving the cube in as few moves as possible.

## 6. AI-Assistance Disclosure

**Tools used:** 
No AI assistance used.
**Material effect on this report:** [Describe brainstorming, critique,
organization, editing, or other assistance. State how you checked the resulting
analysis and factual claims.]

## References

List sources for factual claims and borrowed ideas in a consistent citation
format. Course slides should be cited when they supply definitions or the
classification framework.
