# Agents in the Delivery Hallway

**Student:** [Your name]  
**GitHub repository:** [Private repository URL]  
**Repository access:** [Confirm that `fractal13` has at least read access]

## 1. Agent Designs

### Simple Reflex Agent

List your condition-action rules. Explain how each rule uses only the current
percept and confirm that the agent retains no state between actions.

### Model-Based Reflex Agent

Describe the internal state your agent maintains, how each percept updates that
state, and how its condition-action rules use the updated state. Identify the
environment transition assumptions represented by your model.

## 2. Experimental Results

Run each agent on seeds 0 through 99. Record the output from `experiment.py`.

| Agent | Delivery rate | Mean reward | Mean actions | Mean invalid actions |
|---|---:|---:|---:|---:|
| Random | | | | |
| Simple reflex | | | | |
| Model-based reflex | | | | |

State whether you modified the supplied experiment procedure. If so, explain
exactly what changed and why.

## 3. Trace Comparison

Choose one seed for which the agents behave differently. Present or summarize
the three traces. Identify the first action at which behavior differs and
explain which percept or remembered state caused that difference.

## 4. Analysis

In approximately 300-500 words:

1. Explain why the random agent's behavior is not a percept-to-action policy.
2. Explain one strength and one limitation of your simple reflex rules.
3. Use your results and trace to explain what the internal model changes.
4. Describe one situation in which stored state could be stale or incorrect and
   how that could harm performance.
5. State whether your evidence supports the claim that the model-based reflex
   agent performs better in this environment. Refer to measured results rather
   than relying only on intuition.

## 5. AI-Assistance Disclosure

**Tools used:** [Tool names, or "No AI assistance used."]

**Material effect on this work:** [Describe generated code, debugging,
explanations, editing, or other assistance. Explain how you checked it.]

## Submission Check

- [ ] The repository URL opens for GitHub user `fractal13`.
- [ ] The latest code and report evidence are pushed to the repository.
- [ ] `python3 -m unittest -v` passes.
- [ ] The PDF contains selectable text and all required sections.
