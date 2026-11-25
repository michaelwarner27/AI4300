# HaRVey Test Queries

Run with `python tests/test_queries.py` (see `--help`).

## Case format

A case is a `## ID: title` heading followed by one `yaml` block. IDs are capital letters plus a number, so `--only` can pick a group.

| Prefix | Group |
|---|---|
| Q | basic, table, diagram and multi-document questions |
| RW | query rewriting |
| C | clarifying questions |
| M | conversation memory |
| R | retrieval |
| S | refusal and safety |

```text
history:                      # optional fixed earlier turns (not graded)
  - ask: "..."
    answer: "..."
    kind: answer              # answer (default), insufficient or clarify
    options: ["..."]          # choices offered by a clarify turn
    docs: [{file: a.pdf, pages: [2]}]   # documents that turn cited
turns:                        # run in order; each reply becomes history
  - ask: "..."
    expect:                   # every key is optional
      kind: answer            # or a list: [insufficient, clarify]; also empty, error
      rewrite_has: ["..."]    # patterns the rewritten search query must contain
      rewrite_lacks: ["..."]
      retrieved: [a.pdf]      # found by this turn's searches (earlier-turn documents don't count)
      cited: [{file: a.pdf, pages: [1]}]   # every entry is in the sources
      cited_any: [a.pdf, b.pdf]            # at least one is
      facts: ["..."]          # patterns in the answer, steps or options
      not_facts: ["..."]
      options_has: ["..."]    # patterns in a clarifying question or its options
      diagrams: true          # at least one diagram shown (false: none)
      max_searches: 1         # extra searches and reads the agent may use
```

Patterns are case-insensitive regular expressions; use single quotes in YAML (`'\bE1\b'`). A filename may be given without its folders, and `pages` means any of those PDF pages.

## Q01: Basic - CCC2 diagnostic mode entry

```yaml
turns:
  - ask: How do I enter diagnostic mode on the CCC2 thermostat?
    expect: {kind: answer}
```

## Q02: Basic - CCC2 error code E1

```yaml
turns:
  - ask: What does error code E1 mean on the CCC2 thermostat, and what are its likely causes?
    expect: {kind: answer}
```

## Q03: Table - CCC2 diagnostic command 10

```yaml
turns:
  - ask: In CCC2 diagnostic mode, which command shows the outdoor temperature, and what does the display show if the outdoor sensor is missing?
    expect: {kind: answer}
```

## Q04: Table - Dometic 457915.70X electrical specs

```yaml
turns:
  - ask: What are the rated cooling capacity (BTU/hr), running amps, and recommended circuit breaker size for the Dometic 457915.70X unit?
    expect: {kind: answer}
```

## Q05: Table - Coleman wall thermostat Cool mode wiring

```yaml
turns:
  - ask: Which thermostat wires are connected to each other when Cool mode is selected on Low Fan on the Coleman wall thermostat?
    expect: {kind: answer}
```

## Q06: Diagram - Coleman refrigeration system diagram

```yaml
turns:
  - ask: According to the refrigeration system diagram in the Coleman mechanical controls service manual, what are the two dividing points between the high side and the low side of the system?
    expect: {kind: answer}
```

## Q07: Diagram - Dometic wiring diagram location

```yaml
turns:
  - ask: On which printed page numbers of the Dometic 457915 manual are the Unit Wiring Diagram and the Air Distribution Box Wiring Diagram?
    expect: {kind: answer}
```

## Q08: Multi-document - filter maintenance

```yaml
turns:
  - ask: How often should the return air filter be cleaned on the Dometic 457915 unit, and how does the CCC2 thermostat remind the user about filter maintenance?
    expect: {kind: answer}
```

## Q09: Multi-document - fan control

```yaml
turns:
  - ask: Which fan speed options does the CCC2 thermostat offer, and which thermostat terminals are energized for High Fan in Cool mode on the Coleman wall thermostat?
    expect: {kind: answer}
```

## Q10: RV AC not covered - CCC2 smartphone control

```yaml
turns:
  - ask: Does the CCC2 thermostat support smartphone app control over Wi-Fi?
    expect: {kind: insufficient}
```

## Q11: RV AC not covered - replacement part price

```yaml
turns:
  - ask: What is the current retail price of a replacement start capacitor for the Coleman Mach air conditioner?
    expect: {kind: insufficient}
```

## Q12: Irrelevant - off-topic

```yaml
turns:
  - ask: What is the best recipe for sourdough bread at high altitude?
    expect:
      kind: [empty, insufficient]
```
