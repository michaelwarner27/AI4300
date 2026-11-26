"""
Table fidelity for the Dexter catalog (layer 2 of tests/dexter-ocr-test-plan.md).

Scores each ground-truth table in tests/dexter-ground-truth/tables.yaml at three
stages, so it is clear where values are lost:

    extracted   what the pipeline's table extractor returns for the page
    filtered    what is left after TextChunker.is_meaningful_table (the junk filter)
    stored      the table chunks for that page in the live Chroma collection

    python tests/dexter_tables.py                     # writes tests/results/dexter/baseline/tables.json
    python tests/dexter_tables.py --label ocr-1
    python tests/dexter_tables.py --compare baseline ocr-1

Metrics per table and stage:
    found          at least one ground-truth row is fully present in some piece
    fragments      pieces on the page holding at least one ground-truth row (1 is ideal)
    row_recall     share of ground-truth rows whose cells all appear, in order, in one row
    cell_accuracy  share of ground-truth cells present in the best-matching row (a row
                   must match at least half its cells, and at least 2, to earn credit)
    header_kept    the piece holding the most rows also holds the column headers
                   (skipped when the page prints no header row)
    section_ok     (stored only) the chunk's Section heading is printed on its page or the one before

Cells are compared after lower-casing, collapsing whitespace and unifying quote and
dash characters. A cell must match a whole extracted cell; values merged into one
cell or split across cells count as lost.

When the table extractor changes (e.g. Docling), the 'extracted' stage uses
whatever extract_pieces() returns, so update that one function.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf  # noqa: E402
import yaml  # noqa: E402

from src.config import load_config  # noqa: E402
from src.ingestion.tools import ChromaVectorStore, TableExtractor, TextChunker  # noqa: E402

GROUND_TRUTH = ROOT / "tests" / "dexter-ground-truth" / "tables.yaml"
RESULTS = ROOT / "tests" / "results" / "dexter"
STAGES = ("extracted", "filtered", "stored")
METRICS = ("found", "fragments", "row_recall", "cell_accuracy", "header_kept")


def norm(text) -> str:
    text = str(text or "")
    text = text.replace("”", '"').replace("“", '"').replace("″", '"').replace("''", '"')
    text = text.replace("–", "-").replace("−", "-")
    return re.sub(r"\s+", " ", text).strip().lower()


# --- Pieces: each is a list of rows, each row a list of normalised cells ------

def extract_pieces(pdf_path: Path) -> list:
    """The pipeline's current table extractor (pdfplumber). Returns TableData objects."""
    return TableExtractor().extract_tables_from_pdf(pdf_path)


def rows_from_raw(raw: list[list]) -> list[list[str]]:
    return [[norm(cell) for cell in row] for row in raw if row]


def rows_from_markdown(chunk_text: str) -> list[list[str]]:
    """Parse a stored table chunk. A cell with a newline continues on the next line."""
    rows: list[str] = []
    for line in chunk_text.split("\n"):
        if line.startswith("|"):
            if not re.match(r"^\|(-{3}\|)+$", line.strip()):
                rows.append(line)
        elif rows:
            rows[-1] += " " + line
    return [[norm(cell) for cell in row.strip().strip("|").split("|")] for row in rows]


def stored_pieces(config, filename: str) -> dict[int, list[dict]]:
    store = ChromaVectorStore(config.chroma_persist_directory, config.chroma_collection_name)
    store.initialize_collection()
    data = store.collection.get(
        where={"$and": [{"source_filename": {"$eq": filename}}, {"chunk_type": {"$eq": "table"}}]},
        include=["documents", "metadatas"],
    )
    by_page: dict[int, list[dict]] = {}
    for doc, meta in zip(data["documents"], data["metadatas"]):
        by_page.setdefault(int(meta["page_number"]), []).append(
            {"rows": rows_from_markdown(doc), "section": meta.get("section_path", "")})
    return by_page


# --- Scoring ----------------------------------------------------------------

def ordered_matches(expected: list[str], row: list[str]) -> int:
    """How many expected cells appear in `row`, in order (greedy)."""
    count, start = 0, 0
    for cell in expected:
        for i in range(start, len(row)):
            if row[i] == cell:
                count += 1
                start = i + 1
                break
    return count


def header_like(row: list[str], columns: list[str]) -> bool:
    names = {norm(c) for c in columns} | {norm(c).split(" ")[-1] for c in columns}
    cells = [c for c in row if c]
    return bool(cells) and sum(c in names for c in cells) / len(cells) >= 0.5


def score(table: dict, pieces: list[list[list[str]]]) -> dict:
    truth = [[norm(c) for c in row] for row in table["rows"]]
    total_cells = sum(len(r) for r in truth)
    rows_full = 0
    cells_ok = 0
    rows_per_piece = [0] * len(pieces)
    for expected in truth:
        best_cells = 0
        full_in = None
        # A piece row is credited only if it matches at least half of the expected
        # cells (and at least 2), so a stray "B" in a header row earns nothing, but a
        # row that lost one merged cell (blank start angle) still earns its other cells.
        needed = max(2, (len(expected) + 1) // 2)
        for p_idx, piece in enumerate(pieces):
            for row in piece:
                # A filled-in first cell that differs from the expected key means a
                # different row (e.g. tire 20.5X8.0-10 shares many values with 4.80-12).
                if row and row[0] and row[0] != expected[0]:
                    continue
                n = ordered_matches(expected, row)
                if n < needed:
                    continue
                best_cells = max(best_cells, n)
                if n == len(expected) and full_in is None:
                    full_in = p_idx
        cells_ok += best_cells
        if full_in is not None:
            rows_full += 1
            rows_per_piece[full_in] += 1

    fragments = sum(1 for n in rows_per_piece if n)
    header_kept = None
    if table.get("header_printed", True):
        header_kept = False
        if fragments:
            main = pieces[max(range(len(pieces)), key=lambda i: rows_per_piece[i])]
            header_kept = any(header_like(row, table["columns"]) for row in main)
    return {
        "found": rows_full > 0,
        "fragments": fragments,
        "row_recall": round(rows_full / len(truth), 3),
        "cell_accuracy": round(cells_ok / total_cells, 3),
        "header_kept": header_kept,
        "pieces_on_page": len(pieces),
    }


def section_ok(sections: list[str], page: int, page_text: list[str]) -> bool | None:
    if not sections:
        return None
    window = page_text[max(page - 2, 0): page]
    for raw in sections:
        last = [p.strip() for p in raw.split(">") if p.strip()]
        if last and any(norm(last[-1]) in text for text in window):
            return True
    return False


# --- Run --------------------------------------------------------------------

def run(label: str) -> Path:
    spec = yaml.safe_load(GROUND_TRUTH.read_text())
    filename = spec.get("file", "Dexter Trailer Catalog V1-24 p1-100.pdf")
    pdf_path = next((ROOT / "documents").rglob(filename), None)
    if pdf_path is None:
        sys.exit(f"Cannot find {filename} under documents/")
    config = load_config()
    page_text = [norm(p.get_text()) for p in pymupdf.open(pdf_path)]

    extracted = extract_pieces(pdf_path)
    stored = stored_pieces(config, filename)

    results = []
    for table in spec["tables"]:
        page = table["page"]
        on_page = [t for t in extracted if t.page + 1 == page]
        stages = {
            "extracted": [rows_from_raw(t.raw_data) for t in on_page],
            "filtered": [rows_from_raw(t.raw_data) for t in on_page if TextChunker.is_meaningful_table(t)],
            "stored": [p["rows"] for p in stored.get(page, [])],
        }
        entry = {"id": table["id"], "page": page, "verified": bool(table.get("verified")),
                 "rows": len(table["rows"]), "stages": {s: score(table, pcs) for s, pcs in stages.items()}}
        entry["stages"]["stored"]["section_ok"] = section_ok(
            [p["section"] for p in stored.get(page, [])], page, page_text)
        results.append(entry)

    result = {
        "label": label,
        "file": filename,
        "created": datetime.now().isoformat(timespec="seconds"),
        "extractor": "pdfplumber (TableExtractor)",
        "summary": summarize(results),
        "tables": results,
    }
    out = RESULTS / label / "tables.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print_report(result)
    print(f"\nWrote {out.relative_to(ROOT)}")
    return out


def summarize(results: list[dict]) -> dict:
    """Averages per stage, over all tables and over verified tables only."""
    def agg(rows: list[dict]) -> dict:
        out = {}
        for stage in STAGES:
            s = [r["stages"][stage] for r in rows]
            if not s:
                continue
            headers = [x["header_kept"] for x in s if x["header_kept"] is not None]
            out[stage] = {
                "found": sum(x["found"] for x in s),
                "tables": len(s),
                "mean_row_recall": round(sum(x["row_recall"] for x in s) / len(s), 3),
                "mean_cell_accuracy": round(sum(x["cell_accuracy"] for x in s) / len(s), 3),
                "mean_fragments_when_found": round(
                    sum(x["fragments"] for x in s if x["found"]) / max(sum(x["found"] for x in s), 1), 2),
                "header_kept": f"{sum(headers)}/{len(headers)}",
            }
        return out
    return {"all": agg(results), "verified": agg([r for r in results if r["verified"]])}


def print_report(result: dict) -> None:
    print(f"Table fidelity '{result['label']}' ({result['extractor']})\n")
    print(f"{'table':30} {'stage':9} {'found':>5} {'frag':>4} {'rows':>6} {'cells':>6} {'header':>6} {'section':>7}")
    for t in result["tables"]:
        mark = "" if t["verified"] else " *"
        for stage in STAGES:
            s = t["stages"][stage]
            header = "-" if s["header_kept"] is None else ("yes" if s["header_kept"] else "no")
            section = {None: "-", True: "ok", False: "stale"}[s.get("section_ok")] if stage == "stored" else ""
            name = (t["id"] + mark) if stage == "extracted" else ""
            print(f"{name:30} {stage:9} {'yes' if s['found'] else 'no':>5} {s['fragments']:>4} "
                  f"{s['row_recall']:>6.0%} {s['cell_accuracy']:>6.0%} {header:>6} {section:>7}")
    print("\n* = not yet verified against the page image")
    for group in ("all", "verified"):
        print(f"\nSummary ({group} tables):")
        for stage, s in result["summary"][group].items():
            print(f"  {stage:9} found {s['found']}/{s['tables']}  row recall {s['mean_row_recall']:.0%}  "
                  f"cell accuracy {s['mean_cell_accuracy']:.0%}  fragments {s['mean_fragments_when_found']}  "
                  f"header kept {s['header_kept']}")


def compare(old_label: str, new_label: str) -> None:
    old = json.loads((RESULTS / old_label / "tables.json").read_text())
    new = json.loads((RESULTS / new_label / "tables.json").read_text())
    print(f"Table fidelity '{old_label}' -> '{new_label}'\n")
    for group in ("all", "verified"):
        print(f"Summary ({group} tables):")
        for stage in STAGES:
            a, b = old["summary"][group].get(stage), new["summary"][group].get(stage)
            if not a or not b:
                continue
            print(f"  {stage:9} found {a['found']}->{b['found']}  "
                  f"row recall {a['mean_row_recall']:.0%}->{b['mean_row_recall']:.0%}  "
                  f"cells {a['mean_cell_accuracy']:.0%}->{b['mean_cell_accuracy']:.0%}  "
                  f"header {a['header_kept']}->{b['header_kept']}")
    print("\nStored stage per table (row recall / cell accuracy):")
    before = {t["id"]: t for t in old["tables"]}
    for t in new["tables"]:
        a = before.get(t["id"], {}).get("stages", {}).get("stored")
        b = t["stages"]["stored"]
        if a:
            print(f"  {t['id']:30} {a['row_recall']:.0%}/{a['cell_accuracy']:.0%} -> "
                  f"{b['row_recall']:.0%}/{b['cell_accuracy']:.0%}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Score Dexter table extraction against ground truth.")
    parser.add_argument("--label", default="baseline", help="results folder name (default: baseline)")
    parser.add_argument("--compare", nargs=2, metavar=("OLD", "NEW"), help="compare two saved results")
    args = parser.parse_args()
    if args.compare:
        compare(*args.compare)
    else:
        run(args.label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
