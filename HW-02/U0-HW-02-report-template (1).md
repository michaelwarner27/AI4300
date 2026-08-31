# Agents in the Delivery Hallway

**Student:** Michael Warner
**GitHub repository:**  
https://github.com/michaelwarner27/AI4300/tree/main/HW-02
**Repository access:** [Confirm that `fractal13` has at least read access]

## 1. Agent Designs

### Simple Reflex Agent

List your condition-action rules. Explain how each rule uses only the current percept and confirm that the agent retains no state between actions.

If the robot is in the same location of the package and not already carrying it, pick it up. Percepts: robot carrying, package here.
If the robot is carrying the package and in the same location as the destination, drop off. Percepts: robot carrying, destination here.
If the robot location is A or E it will move right and left respectively as these are the only valid directions.
For locations B,C,D I arbitrarily chose to move the robot right. Because the robot doesn't remember if it just came from the left or the right there is no way to keep moving in a singular direction.

### Model-Based Reflex Agent

Describe the internal state your agent maintains, how each percept updates that state, and how its condition-action rules use the updated state. Identify the environment transition assumptions represented by your model.

The Model agent stores the location of the destination if it has seen it, and the current direction of movement. If the robot senses the destination is in its current location it sets self.destination to percept.location. This allows for the robot to move toward the destination immediately after finding the package, if it has already seen the destination.
The robot uses the current direction of movement to keep moving in a single direction, until hitting a wall and switching, guaranteeing the package and destination are found.
This model assumes that the destination does not change.

## 2. Experimental Results

Run each agent on seeds 0 through 99. Record the output from `experiment.py`.

State whether you modified the supplied experiment procedure. If so, explain exactly what changed and why.
Experiment not Changed
Random:
   delivery rate: 16.0%
   mean reward: -47.08
   mean actions: 28.22
   mean invalid actions: 12.73
Reflex:
   delivery rate: 26.0%
   mean reward: -14.52
   mean actions: 23.57
   mean invalid actions: 0.00
Model:
   delivery rate: 100.0%
   mean reward: 22.86
   mean actions: 7.14
   mean invalid actions: 0.00
## 3. Trace Comparison

Choose one seed for which the agents behave differently. Present or summarize the three traces. Identify the first action at which behavior differs and explain which percept or remembered state caused that difference.
Seed = 4
Random: delivered=True steps=10 reward=14 invalid=3
   This seed for random demonstrates that the reflex agent isn't always better. Because the random agent isn't limited to a single direction for locations b,c,d it can deliver the package in situations where the reflex might not be able to.
Reflex: delivered=False steps=30 reward=-25 invalid=0
   This first differs from the Model agent on step 6. This agent always moves to the right when in locations b,c,d so after it hits e and moves left it gets caught in an endless loop and never delivers the package. However there are no invalid actions because the reflex agent uses its precepts to only pick up and deliver when able. Also it only moves in a valid direction when at the edges of the hallway because it know's its location.
Model: delivered=True steps=9 reward=21 invalid=0
   Unlike the Reflex agent this one remembers which direction it needs to go so after hitting e it sets its direction to left and moves left across the hall until it delivers the package.
## 4. Analysis

In approximately 300-500 words:

1. Explain why the random agent's behavior is not a percept-to-action policy.
The random agent's behavior is not changed based on percepts so they might as well not be there.
2. Explain one strength and one limitation of your simple reflex rules.
One strength is that it will never perform an invalid action. A limitation is that because it doesn't remember which direction it came from it will often end up in an infinite loop in locations D and E, resulting in the package never being delivered.
3. Use your results and trace to explain what the internal model changes.
Having an internal model allows us to travel in a single direction until our goal is met or we reach the end of the hall and need to turn around. It also allows for a slight optimization of remembering where the destination is and traveling towards it once the package has been collected.
4. Describe one situation in which stored state could be stale or incorrect and how that could harm performance.
If the destination changes this could harbor performance but that won't happen in this environment.
An improvement that could be made to the stored state is changing the starting direction based on starting location. The state stored in current direction starts stale because it is arbitrarily set to right.
5. State whether your evidence supports the claim that the model-based reflex agent performs better in this environment. Refer to measured results rather than relying only on intuition.
Yes my evidence supports that the model-based agent performs the best of the 3 in this environment. It has a completion rate of 100% compared to 16% and 26% of the random and reflex models. Additionally, it completes the problem in fewer steps, average of ~7 compared to ~28 and ~23, and with a higher reward ~22, compared to ~-47 and ~-14 of the random and reflex models.

## 5. AI-Assistance Disclosure

**Tools used:** [Tool names, or "No AI assistance used."]
No AI tools used in this assignment

**Material effect on this work:** [Describe generated code, debugging,
explanations, editing, or other assistance. Explain how you checked it.]

## Submission Check

- [ ] The repository URL opens for GitHub user `fractal13`.
- [ ] The latest code and report evidence are pushed to the repository.
- [ ] `python3 -m unittest -v` passes.
- [ ] The PDF contains selectable text and all required sections.
