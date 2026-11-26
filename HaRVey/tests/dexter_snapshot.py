"""
Ingestion snapshot for the Dexter catalog (layer 1 of tests/dexter-ocr-test-plan.md).

Reads what the pipeline stored for one PDF in the live Chroma collection and
records counts and quality measures, so a later run (e.g. after adding OCR) can
be compared against this one. No LLM or embedding calls are made.

    python tests/dexter_snapshot.py                      # writes tests/results/dexter/baseline/snapshot.json
    python tests/dexter_snapshot.py --label ocr-1        # writes tests/results/dexter/ocr-1/snapshot.json
    python tests/dexter_snapshot.py --compare baseline ocr-1

What it records (all pages are 1-based PDF pages):
    totals       chunk counts by type, diagram files on disk, distinct part numbers
    tables       tables extracted (pdfplumber), kept by the junk filter, stored;
                 header-only table chunks; largest table chunk
    page_tags    how much of each text chunk is printed on its tagged page (chunks that
                 run onto the next page are the known page-tag bug)
    sections     share of chunks whose section heading is printed on their page (or the one before)
    noise        chunks containing the repeated page footer; near-duplicate text chunks
    probes       results for tests/dexter-ground-truth/probes.yaml
    pages        per-page counts
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymupdf  # noqa: E402
import yaml  # noqa: E402

from src.config import load_config  # noqa: E402
from src.ingestion.tools import ChromaVectorStore, TableExtractor, TextChunker  # noqa: E402

DEFAULT_FILE = "Dexter Trailer Catalog V1-24 p1-100.pdf"
PROBES = ROOT / "tests" / "dexter-ground-truth" / "probes.yaml"
RESULTS = ROOT / "tests" / "results" / "dexter"
FOOTER = "browse inventory, review product info"
ANGLE_CELL = re.compile(r"^[-+]?\d+(\.\d+)?°\s*(UP|DOWN|DN)?$", re.IGNORECASE)
PAGE_WINDOW = (-1, 3)  # pages around the tagged page searched for a chunk's text
CHANGE_THRESHOLD = 0.20  # per-page changes larger than this are highlighted by --compare


def norm(text: str) -> str:
    """Lower-case and collapse whitespace, so line breaks don't hide matches."""
    return re.sub(r"\s+", " ", text or "").strip().lower()


def chunk_body(document: str) -> str:
    """Chunk text without the 'Table from…' / 'Figure on…' and 'Section:' prefix lines."""
    lines = document.split("\n")
    while lines and (lines[0].startswith(("Section:", "Table from ", "Figure on "))):
        lines.pop(0)
    return "\n".join(lines)


def section_path(meta: dict) -> list[str]:
    raw = meta.get("section_path") or ""
    return [part.strip() for part in raw.split(">") if part.strip()]


def kind(meta: dict) -> str:
    """text | table | diagram (stored as 'diagram_reference')."""
    value = meta.get("chunk_type", "text")
    return "diagram" if value.startswith("diagram") else value


# --- Measures ---------------------------------------------------------------

def table_pipeline(pdf_path: Path, chunks: list[dict]) -> dict:
    """Extracted -> kept by the junk filter -> stored, in total and per page."""
    extracted = TableExtractor().extract_tables_from_pdf(pdf_path)
    kept = [t for t in extracted if TextChunker.is_meaningful_table(t)]
    stored = [c for c in chunks if c["kind"] == "table"]

    def per_page(pages: list[int]) -> dict[str, int]:
        return {str(p): n for p, n in sorted(Counter(pages).items())}

    header_only = []
    for c in stored:
        rows = [r for r in chunk_body(c["text"]).split("\n") if r.startswith("|") and not r.startswith("|---")]
        cells = [cell.strip() for row in rows for cell in row.strip("|").split("|")]
        # A data value is any cell with a digit, except angle labels such as "45° DOWN" or "0°"
        if not any(re.search(r"\d", cell) and not ANGLE_CELL.match(cell) for cell in cells if cell):
            header_only.append(c["page"])

    largest = max(stored, key=lambda c: len(c["text"]), default=None)
    return {
        "extracted": len(extracted),
        "kept_by_filter": len(kept),
        "dropped_by_filter": len(extracted) - len(kept),
        "stored": len(stored),
        "header_only_stored": len(header_only),
        "header_only_pages": sorted(set(header_only)),
        "largest_chunk_chars": len(largest["text"]) if largest else 0,
        "largest_chunk_page": largest["page"] if largest else None,
        "extracted_per_page": per_page([t.page + 1 for t in extracted]),
        "kept_per_page": per_page([t.page + 1 for t in kept]),
        "stored_per_page": per_page([c["page"] for c in stored]),
    }


def page_tag_accuracy(chunks: list[dict], page_text: list[str]) -> dict:
    """
    How much of each text chunk is printed on the page it is tagged with.

    The chunk is cut into 5-word shingles. Each shingle is looked up on the
    pages near the tagged page (PAGE_WINDOW). A shingle counts as 'on tag' if
    the tagged page prints it, and as 'elsewhere' if only other pages do.
    Shingles found nowhere (layout reading order differs from get_text order)
    are ignored. A chunk is:
        on_page     >= 90% of its located shingles are on the tagged page
        straddles   50-90%   (starts on the tagged page, runs onto the next)
        wrong_page  < 50%    (mostly another page's content)
    """
    classes: Counter = Counter()
    main_offsets: Counter = Counter()
    content_on_tag = content_total = 0
    bad_pages: list[int] = []
    unlocated = 0
    for c in chunks:
        if c["kind"] != "text":
            continue
        tag = c["page"]
        window = [p for p in range(tag + PAGE_WINDOW[0], tag + PAGE_WINDOW[1] + 1) if 1 <= p <= len(page_text)]
        on_tag = elsewhere = 0
        hits: Counter = Counter()
        for shingle in shingles(chunk_body(c["text"])):
            pages = [p for p in window if shingle in page_text[p - 1]]
            hits.update(pages)
            if tag in pages:
                on_tag += 1
            elif pages:
                elsewhere += 1
        located = on_tag + elsewhere
        if located < 3:
            unlocated += 1
            continue
        share = on_tag / located
        classes["on_page" if share >= 0.9 else "straddles" if share >= 0.5 else "wrong_page"] += 1
        main_offsets[hits.most_common(1)[0][0] - tag] += 1
        content_on_tag += on_tag
        content_total += located
        if share < 0.9:
            bad_pages.append(tag)
    located_chunks = sum(classes.values())
    return {
        "text_chunks": located_chunks + unlocated,
        "located": located_chunks,
        "on_page": classes["on_page"],
        "straddles": classes["straddles"],
        "wrong_page": classes["wrong_page"],
        "on_page_share": round(classes["on_page"] / located_chunks, 3) if located_chunks else None,
        "content_on_tagged_page_share": round(content_on_tag / content_total, 3) if content_total else None,
        "main_page_offsets": {str(k): v for k, v in sorted(main_offsets.items())},
        "unlocated": unlocated,
        "pages_with_mis_tagged_chunks": sorted(set(bad_pages)),
    }


def section_accuracy(chunks: list[dict], page_text: list[str]) -> dict:
    """A section path is 'current' if its last heading is printed on the chunk's page or the page before."""
    current = 0
    stale_by_page: Counter = Counter()
    paths: Counter = Counter()
    for c in chunks:
        path = section_path(c["meta"])
        if not path:
            continue
        paths[" > ".join(path)] += 1
        heading = norm(path[-1])
        window = page_text[max(c["page"] - 2, 0): c["page"]]
        if any(heading in text for text in window):
            current += 1
        else:
            stale_by_page[c["page"]] += 1
    total = current + sum(stale_by_page.values())
    return {
        "chunks_with_section": total,
        "current": current,
        "current_share": round(current / total, 3) if total else None,
        "distinct_paths": len(paths),
        "most_common_paths": [{"path": p, "chunks": n} for p, n in paths.most_common(5)],
        "worst_pages": [{"page": p, "stale_chunks": n} for p, n in stale_by_page.most_common(10)],
    }


def shingles(text: str, size: int = 5) -> set[str]:
    words = norm(text).split(" ")
    return {" ".join(words[i:i + size]) for i in range(max(len(words) - size + 1, 1))}


def noise(chunks: list[dict]) -> dict:
    footer = sum(1 for c in chunks if FOOTER in norm(c["text"]))
    texts = [shingles(chunk_body(c["text"])) for c in chunks if c["kind"] == "text"]
    duplicated = set()
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            union = texts[i] | texts[j]
            if union and len(texts[i] & texts[j]) / len(union) >= 0.8:
                duplicated.update((i, j))
    return {
        "chunks_with_footer": footer,
        "near_duplicate_text_chunks": len(duplicated),
        "near_duplicate_share": round(len(duplicated) / len(texts), 3) if texts else None,
    }


def run_probes(chunks: list[dict]) -> dict:
    spec = yaml.safe_load(PROBES.read_text())
    results = []
    for probe in spec["probes"]:
        pool = [c for c in chunks
                if c["page"] == probe["page"]
                and (not probe.get("chunk_type") or c["kind"] == probe["chunk_type"])]
        found = any(norm(probe["text"]) in norm(c["text"]) for c in pool)
        target = probe["expect"] == "present"
        if found == target:
            status = "pass"
        elif probe["expect"] == "missing":
            status = "xpass"          # an OCR target is now found
        elif probe.get("baseline_fail"):
            status = "known_fail"     # fails for a recorded non-OCR reason
        else:
            status = "fail"           # regression
        results.append({**probe, "found": found, "status": status})
    return {
        "summary": dict(Counter(r["status"] for r in results)),
        "results": results,
    }


def per_page(chunks: list[dict], page_count: int) -> dict:
    pages = {}
    for p in range(1, page_count + 1):
        mine = [c for c in chunks if c["page"] == p]
        pages[str(p)] = {
            "chunks": len(mine),
            "text": sum(c["kind"] == "text" for c in mine),
            "table": sum(c["kind"] == "table" for c in mine),
            "diagram": sum(c["kind"] == "diagram" for c in mine),
            "text_chars": sum(len(chunk_body(c["text"])) for c in mine if c["kind"] == "text"),
        }
    return pages


# --- Snapshot and compare ----------------------------------------------------

def load_chunks(config, filename: str) -> list[dict]:
    store = ChromaVectorStore(config.chroma_persist_directory, config.chroma_collection_name)
    store.initialize_collection()
    data = store.collection.get(where={"source_filename": filename}, include=["documents", "metadatas"])
    chunks = [{"text": doc, "meta": meta, "page": int(meta.get("page_number") or 0), "kind": kind(meta)}
              for doc, meta in zip(data["documents"], data["metadatas"])]
    chunks.sort(key=lambda c: c["meta"].get("chunk_sequence", 0))
    return chunks


def find_pdf(filename: str) -> Path:
    matches = list((ROOT / "documents").rglob(filename))
    if not matches:
        sys.exit(f"Cannot find {filename} under documents/")
    return matches[0]


def snapshot(label: str, filename: str) -> Path:
    config = load_config()
    chunks = load_chunks(config, filename)
    if not chunks:
        sys.exit(f"No chunks stored for {filename}. Run ingestion first.")
    pdf_path = find_pdf(filename)
    doc = pymupdf.open(pdf_path)
    page_text = [norm(page.get_text()) for page in doc]

    diagram_files = {c["meta"].get("diagram_path") for c in chunks if c["meta"].get("diagram_path")}
    part_numbers = {p for c in chunks for p in (c["meta"].get("part_numbers") or "").split("|") if p}

    result = {
        "label": label,
        "file": filename,
        "created": datetime.now().isoformat(timespec="seconds"),
        "file_hash": chunks[0]["meta"].get("file_hash"),
        "ingested_at": chunks[0]["meta"].get("ingested_at"),
        "settings": {
            "chunk_size": config.chunk_size,
            "chunk_overlap": config.chunk_overlap,
            "embedding_model": config.embedding_model,
        },
        "totals": {
            "pages": doc.page_count,
            "chunks": len(chunks),
            "by_type": dict(Counter(c["kind"] for c in chunks)),
            "diagram_files": len(diagram_files),
            "diagram_files_on_disk": sum(1 for p in diagram_files if Path(p).exists() or (ROOT / p).exists()),
            "part_numbers": len(part_numbers),
        },
        "tables": table_pipeline(pdf_path, chunks),
        "page_tags": page_tag_accuracy(chunks, page_text),
        "sections": section_accuracy(chunks, page_text),
        "noise": noise(chunks),
        "probes": run_probes(chunks),
        "pages": per_page(chunks, doc.page_count),
    }
    out = RESULTS / label / "snapshot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print_summary(result)
    print(f"\nWrote {out.relative_to(ROOT)}")
    return out


def print_summary(s: dict) -> None:
    t, tb, pt, sec, nz = s["totals"], s["tables"], s["page_tags"], s["sections"], s["noise"]
    print(f"Snapshot '{s['label']}' of {s['file']}")
    print(f"  chunks           {t['chunks']}  {t['by_type']}")
    print(f"  diagram files    {t['diagram_files']} ({t['diagram_files_on_disk']} on disk)")
    print(f"  part numbers     {t['part_numbers']}")
    print(f"  tables           extracted {tb['extracted']} -> kept {tb['kept_by_filter']} -> stored {tb['stored']}"
          f"  (header-only {tb['header_only_stored']}, largest {tb['largest_chunk_chars']} chars on p{tb['largest_chunk_page']})")
    print(f"  page tags        {pt['on_page']}/{pt['located']} text chunks on their tagged page ({pt['on_page_share']}), "
          f"{pt['straddles']} straddle, {pt['wrong_page']} mostly another page; "
          f"{pt['content_on_tagged_page_share']} of content on tagged page; unlocated {pt['unlocated']}")
    print(f"                   main-page offset {pt['main_page_offsets']}")
    print(f"  section paths    {sec['current']}/{sec['chunks_with_section']} current ({sec['current_share']}), "
          f"{sec['distinct_paths']} distinct")
    print(f"  noise            footer in {nz['chunks_with_footer']} chunks, "
          f"near-duplicate text chunks {nz['near_duplicate_text_chunks']} ({nz['near_duplicate_share']})")
    print(f"  probes           {s['probes']['summary']}")
    for r in s["probes"]["results"]:
        if r["status"] != "pass":
            print(f"    {r['status']:10} p{r['page']} {r.get('chunk_type', 'any'):5} {r['text']!r}")


def compare(old_label: str, new_label: str) -> None:
    old = json.loads((RESULTS / old_label / "snapshot.json").read_text())
    new = json.loads((RESULTS / new_label / "snapshot.json").read_text())

    def row(name: str, a, b) -> None:
        if a == b:
            print(f"  {name:32} {a}")
        else:
            print(f"  {name:32} {a} -> {b}")

    print(f"Comparing '{old_label}' -> '{new_label}'")
    row("chunks", old["totals"]["chunks"], new["totals"]["chunks"])
    for k in sorted(set(old["totals"]["by_type"]) | set(new["totals"]["by_type"])):
        row(f"  {k}", old["totals"]["by_type"].get(k, 0), new["totals"]["by_type"].get(k, 0))
    for k in ("diagram_files", "part_numbers"):
        row(k, old["totals"][k], new["totals"][k])
    for k in ("extracted", "kept_by_filter", "stored", "header_only_stored", "largest_chunk_chars"):
        row(f"tables.{k}", old["tables"][k], new["tables"][k])
    row("page_tags.on_page_share", old["page_tags"]["on_page_share"], new["page_tags"]["on_page_share"])
    row("page_tags.content_on_tagged_page", old["page_tags"]["content_on_tagged_page_share"],
        new["page_tags"]["content_on_tagged_page_share"])
    row("sections.current_share", old["sections"]["current_share"], new["sections"]["current_share"])
    row("noise.chunks_with_footer", old["noise"]["chunks_with_footer"], new["noise"]["chunks_with_footer"])
    row("noise.near_duplicate_share", old["noise"]["near_duplicate_share"], new["noise"]["near_duplicate_share"])
    row("probes", old["probes"]["summary"], new["probes"]["summary"])

    print("\nProbe changes:")
    before = {(r["page"], r.get("chunk_type"), r["text"]): r for r in old["probes"]["results"]}
    changed = False
    for r in new["probes"]["results"]:
        prev = before.get((r["page"], r.get("chunk_type"), r["text"]))
        if prev and prev["status"] != r["status"]:
            changed = True
            print(f"  p{r['page']} {r.get('chunk_type', 'any'):5} {r['text']!r}: {prev['status']} -> {r['status']}")
    if not changed:
        print("  none")

    print(f"\nPages whose chunk count or text size changed by more than {CHANGE_THRESHOLD:.0%}:")
    flagged = False
    for p, a in old["pages"].items():
        b = new["pages"].get(p, {})
        for key in ("chunks", "text_chars"):
            x, y = a.get(key, 0), b.get(key, 0)
            if max(x, y) and abs(y - x) / max(x, 1) > CHANGE_THRESHOLD:
                flagged = True
                print(f"  p{p:>3} {key:10} {x} -> {y}  (table {a['table']}->{b.get('table', 0)}, "
                      f"diagram {a['diagram']}->{b.get('diagram', 0)})")
                break
    if not flagged:
        print("  none")


def main() -> int:
    parser = argparse.ArgumentParser(description="Record or compare an ingestion snapshot of the Dexter catalog.")
    parser.add_argument("--label", default="baseline", help="results folder name (default: baseline)")
    parser.add_argument("--file", default=DEFAULT_FILE, help="stored filename to snapshot")
    parser.add_argument("--compare", nargs=2, metavar=("OLD", "NEW"), help="compare two saved snapshots")
    args = parser.parse_args()
    if args.compare:
        compare(*args.compare)
    else:
        snapshot(args.label, args.file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
