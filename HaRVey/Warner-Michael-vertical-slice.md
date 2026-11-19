# HaRVey Vertical Slice

## 1. Submission Details

- **Name:** Michael Warner
- **Repository URL:** <!-- TODO -->
- **Submitted commit:** <!-- TODO: full hash. Note: query_main.py, src/query/*, tests/* currently have uncommitted changes; commit before recording the hash -->

## 2. Commands

### Setup
```bash
pip install -r src/requirements.txt
cp example.env.example .env
# Edit .env: set EMBEDDING_API_KEY and LLM_API_KEY (services at http://golem:4000/v1)
```

### Ingestion
```bash
python ingestion_main.py
# Optional: list ingested documents
python manage_documents.py --list
```

### Tests
```bash
python tests/verify_ingestion.py      # Ingestion checks (fixture PDF, temp Chroma, fake embeddings; no servers needed)
python tests/verify_query_loop.py     # Query agent-loop checks (scripted LLM, fake embeddings; no servers needed)
```

### Run (single query)
```bash
python query_main.py 
```

### Baseline measurement
```bash
python tests/test_queries.py
```

Reads the 12 questions in `tests/test-queries.md`, runs each through the query pipeline against the live LLM and embedding services, and writes `tests/results/query_results.txt`.

## 3. Implemented Agent-Environment Path

See my HW10 report or my README for a detailed report on the current structure.

### Changes from U0-HW-10

The largest change I have made from HW 10 is that I enabled parsing and encoding of diagrams. Once I get my agent returning these to the user more consistently this will be a major feature in my project.

## 4. Baseline Results

Source: `tests/results/query_results.txt` 

| ID | Category | Terminal status | Success condition | Met? | Notes |
|----|----------|-----------------|-------------------|------|-------|
| Q01 | Basic | response_generated | Program + Fan + F/C pressed with thermostat off | Yes | Cited CCC2 diagnostic doc p.2 |
| Q02 | Basic | response_generated | E1 = loss of communications, with causes | Yes | Two sources (diagnostic doc p.2, operating instructions p.10) |
| Q03 | Table | response_generated | Command 10, E4 if missing | Yes | |
| Q04 | Table | response_generated | 13,500 BTU/hr, 12.4 A, 20 A | Yes | Accurately pulled table data|
| Q05 | Table | response_generated | R to Y and GL | Yes | |
| Q06 | Diagram | response_generated | Dividing points: cap tube and compressor cylinders | Partial | Correct from text; no diagram image returned, but the diagram was successfully parsed. |
| Q07 | Diagram | response_generated | Printed pages 14 and 15 | Yes | Used printed, not PDF, page numbers as asked |
| Q08 | Multi-document | response_generated | Dometic 2-week interval plus CCC2 filter icon | No | CCC2 part correct (1000 h); Dometic interval missing |
| Q09 | Multi-document | response_generated | Auto/Low/Med/High; R to Y and GH | Yes | Both documents used |
| Q10 | Not covered | insufficient_evidence_returned | Declines; no Wi-Fi/app support in docs | Yes | Fallback snippet was irrelevant |
| Q11 | Not covered | insufficient_evidence_returned | Declines; no pricing in docs | Yes | Fallback snippet was irrelevant |
| Q12 | Irrelevant | no_results_found | No answer given | Yes (status) | Response was empty, with no message to the user |

### Quantitative measures

| Measure | Value |
|---------|-------|
| Queries executed without pipeline error | 12 / 12 |
| Success condition fully met | 10 Yes, 1 Partial, 1 No |
| Correct refusals (Q10-Q12) | 3 / 3 |
| Agent search rounds / LLM calls per query | Only 1 each. I need to fix this.|

### Selected execution evidence

Source: `tests/results/query_results.txt`

#### Q03: Table - CCC2 diagnostic command 10

**Status:** `response_generated`

**Query:**

> In CCC2 diagnostic mode, which command shows the outdoor temperature, and what does the display show if the outdoor sensor is missing?

**Response:**

> In CCC2 diagnostic mode, command **10** shows the outdoor temperature. If the outdoor sensor is missing, the display shows **E4**.

**Sources:**

- CCC2-Diagnostic-Mode-With-Reset.pdf (page 2): 10 LCD shows Outdoor Temperature E4 if missing
- CCC2-Diagnostic-Mode-With-Reset.pdf (page 2): E4 – Outdoor Ambient Sensor is Open or Out of Range

#### Q06: Diagram - Coleman refrigeration system diagram

**Status:** `response_generated`

> **Note from Michael:** Agent did not return diagram, but it was successfully parsed, not sure about how it was encoded. I need to look further into this query.

**Query:**

> According to the refrigeration system diagram in the Coleman mechanical controls service manual, what are the two dividing points between the high side and the low side of the system?

**Response:**

> According to the text describing the refrigeration system, the two dividing points between the high side and the low side are the cap tube and the compressor cylinders.

**Sources:**

- Coleman-Mechanical-controls-1.pdf (page 5): The high side of these systems exists from the discharge port of the compressor to the cap tube. The low side is from the cap tube to the compressor cylinders. The dividing points then are the cap tube and compressor cylinders.

#### Q10: RV AC not covered - CCC2 smartphone control

**Status:** `insufficient_evidence_returned`

**Query:**

> Does the CCC2 thermostat support smartphone app control over Wi-Fi?

**Response:**

> ⚠️ **Insufficient Information**
>
> The provided documents list various features of the CCC2 thermostat, such as a Liquid Crystal Display, 2 operation programs, outdoor temperature sensing, and remote sensors, but they do not mention smartphone app control or Wi-Fi connectivity.
>
> However, here is the most relevant document that was retrieved:
>
> - **Source:** CCC2-Operating-Instructions.pdf (page 3)
> - **Snippet:**
>
> ```text
> 3
> ON/OFF
> To turn ON the CCC 2 thermostat when the back light is off, first press any button
> to wake up the CCC 2 thermostat. Then press and release t
> ```

## 5. Current Omissions and Known Failures

I have yet to implement the deterministic search portion of this project. I haven't been able to spend the time on it I'd like. I'll have a bunch of time over Thanksgiving break.
The Agent is not consistently returning diagrams as sources. I need to look further into what information is being encoded in reference to the diagrams. They are being parsed successfully though.
I'm learning that data is only as good as your ability to parse it. I need change it so every chunk gets the heading data it's under stored with it.
Agent is not going back for more information consistently. I want it to query the manuals further for more information.

## 6. Planned Change for Next Checkpoint

I really want to get the diagrams returning consistently. Simple text answers are not helpful at all. As seen in example query 6 my agent gave a correct text response but didn't return the diagram on the page above where it got the text information. I want that diagram.
Also adding tools to make chats more interactive for the user.
