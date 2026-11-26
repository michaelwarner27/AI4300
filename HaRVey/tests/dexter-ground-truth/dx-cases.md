# DX cases (draft): Dexter catalog end-to-end questions

Draft cases for the `DX` group. They use the format in `tests/test-queries.md` and will move there in step 5 of `tests/dexter-ocr-test-plan.md`, once `test_queries.py` supports these two keys:

- `xfail: true` (case level): the answer exists only in images. Expected to fail before OCR.
- `baseline_fail: page-tag` (case level): the fact is right, but at baseline it is stored under the page before. The `cited` check lists the **true** page, so it fails until the page-tag bug is fixed.

**File:** `Dexter Trailer Catalog V1-24 p1-100.pdf`. Pages are PDF pages. Facts were taken from the page text layer and the rendered pages in `scratch/dexter-pages/`, not from agent answers. Every case starts as `verified: false`.

## DX01: Part number - boxed brake K23-026-00

```yaml
verified: false
baseline_fail: page-tag        # stored under page 14
turns:
  - ask: What is Dexter part K23-026-00?
    expect:
      kind: answer
      cited: [{file: "Dexter Trailer Catalog V1-24 p1-100.pdf", pages: [15]}]
      facts: ['182753', '10\s*x\s*2\.25', '\bLH\b|left']
```

## DX02: Part number - hub 84545 bearings and capacity

```yaml
verified: false
turns:
  - ask: What bearings does the Dexter 84545 hub use and what is its capacity?
    expect:
      kind: answer
      cited: [{file: "Dexter Trailer Catalog V1-24 p1-100.pdf", pages: [15]}]
      facts: ['L68149', 'L44649', '1,?750']
```

## DX03: Part number - EB01-130 fitment

```yaml
verified: false
turns:
  - ask: Will the EB01-130 electric brake fit a Rockwell axle?
    expect:
      kind: answer
      cited: [{file: "Dexter Trailer Catalog V1-24 p1-100.pdf", pages: [96]}]
      facts: ['\bnot?\b|do not|does not|won.t', 'rockwell', '12\s*1/4|12\.25']
```

## DX04: Part number - hanger kit by axle spacing

```yaml
verified: false
turns:
  - ask: Which Dexter hanger kit part number do I need for 42 1/4 inch axle spacing on a 10K axle with 3 inch slipper springs?
    expect:
      kind: answer
      cited_any: ["Dexter Trailer Catalog V1-24 p1-100.pdf"]
      facts: ['HAP-203-02']
```

## DX05: Table - D35 overhang limits

```yaml
verified: false
turns:
  - ask: For a Dexter D35 axle with a 4 inch drop spindle, 3.00 inch tube and disc brakes, what are the minimum and maximum overhang?
    expect:
      kind: answer
      cited: [{file: "Dexter Trailer Catalog V1-24 p1-100.pdf", pages: [14]}]
      facts: ['\b15(\.0+)?"?', '\b23(\.0+)?"?']
```

## DX06: Table - #9 Torflex full load dimension (row dropped from table chunks)

```yaml
verified: false
turns:
  - ask: On a #9 Torflex axle with 4.80-12 tires and a 0 degree start angle, what is the full load B dimension (top of bracket to ground)?
    expect:
      kind: answer
      cited: [{file: "Dexter Trailer Catalog V1-24 p1-100.pdf", pages: [45]}]
      facts: ['8\.4']
```

## DX07: Table - hub face equation

```yaml
verified: false
turns:
  - ask: I only have a bare beam. How do I work out the hub face for a 3,500 lb Dexter axle?
    expect:
      kind: answer
      cited: [{file: "Dexter Trailer Catalog V1-24 p1-100.pdf", pages: [12]}]
      facts: ['6\.34', 'flange']
```

## DX08: Table - #9 Torflex mounting bracket F

```yaml
verified: false
turns:
  - ask: What is mounting bracket dimension F on a #9 Torflex axle for high and low profile brackets?
    expect:
      kind: answer
      cited: [{file: "Dexter Trailer Catalog V1-24 p1-100.pdf", pages: [45]}]
      facts: ['3\.75', '2\.88']
```

## DX09: Comparison - Versa-Flex capacities

```yaml
verified: false
turns:
  - ask: What capacities do Dexter Versa-Flex torsion axles come in?
    expect:
      kind: answer
      cited_any: ["Dexter Trailer Catalog V1-24 p1-100.pdf"]
      facts: ['3,?700', '4,?200', '6,?000', '7,?000']
      not_facts: ['4,?250']      # that is the M10FE Eliminator, not Versa-Flex
```

## DX10: Comparison - D35 vs D70 bearings

```yaml
verified: false
baseline_fail: page-tag        # D35 page stored under page 13
turns:
  - ask: How do the bearings differ between a Dexter D35 and a D70 axle?
    expect:
      kind: answer
      cited: [{file: "Dexter Trailer Catalog V1-24 p1-100.pdf", pages: [14]}, {file: "Dexter Trailer Catalog V1-24 p1-100.pdf", pages: [20]}]
      facts: ['L68149', 'L44649', '25580', '14125A']
```

## DX11: Diagram - Torflex brackets and start angles

```yaml
verified: false
turns:
  - ask: Show me the diagram of Torflex bracket mounting and start angles.
    expect:
      kind: answer
      cited_any: ["Dexter Trailer Catalog V1-24 p1-100.pdf"]
      diagrams: true
```

## DX12: Diagram - reading torsion articulation dimensions

```yaml
verified: false
turns:
  - ask: How high should fenders be set above the tires on a torsion axle? Show the diagram.
    expect:
      kind: answer
      cited: [{file: "Dexter Trailer Catalog V1-24 p1-100.pdf", pages: [9]}]
      facts: ['5\.3', '3(\.00)?"?\s*(inch)?.*(recommend|clearance)|recommend']
      diagrams: true
```

## DX13: Image only - Torflex arm length

```yaml
verified: false
xfail: true
turns:
  - ask: On the Dexter Torflex articulation diagram, what two points is the arm length measured between?
    expect:
      kind: answer
      cited_any: ["Dexter Trailer Catalog V1-24 p1-100.pdf"]
      facts: ['center of (the )?axle', 'center of (the )?spindle']
```

## DX14: Image only - axle label location

```yaml
verified: false
xfail: true
turns:
  - ask: Where is the axle label on a Dexter leaf spring axle?
    expect:
      kind: answer
      cited: [{file: "Dexter Trailer Catalog V1-24 p1-100.pdf", pages: [7]}]
      facts: ['tube|beam']
```

## DX15: Image only - made in America

```yaml
verified: false
xfail: true
turns:
  - ask: Are Dexter's genuine replacement brake parts made in America?
    expect:
      kind: answer
      cited: [{file: "Dexter Trailer Catalog V1-24 p1-100.pdf", pages: [15]}]
      facts: ['made in america|made in the (usa|u\.s)']
```

## DX16: No such model - must not make up specs

```yaml
verified: false
turns:
  - ask: What is the capacity and bearing set of the Dexter D500 leaf spring axle?
    expect:
      kind: [insufficient, clarify]
      not_facts: ['D500.{0,40}\d{1,2},\d{3}\s*lb']
```
