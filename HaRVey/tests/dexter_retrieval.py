"""
Retrieval-only checks for the Dexter catalog (layer 3 of tests/dexter-ocr-test-plan.md).

Runs every query in tests/dexter-ground-truth/retrieval.yaml through the query
pipeline's search, without the LLM. It needs the embedding server, not the LLM.

    python tests/dexter_retrieval.py                    # writes tests/results/dexter/baseline/retrieval.json
    python tests/dexter_retrieval.py --label ocr-1
    python tests/dexter_retrieval.py --only DXR-T,DXR-P01
    python tests/dexter_retrieval.py --compare baseline ocr-1

Two things are measured per query, against the whole corpus:
    ranked hits   _hybrid_search (dense + BM25 + exact part numbers), top RANK_DEPTH.
                  Gives recall@5, recall@10 (the agent's first search uses k=10) and MRR.
    context       the full vector_store_search node output (hits plus neighbours, page
                  diagrams and code matches), i.e. what the LLM is first given.
                  Gives 'in context'.

Every score is reported two ways:
    strict    a chunk from the catalog tagged with one of the case's pages
    lenient   also counts a chunk tagged one page early, because of the known
              page-tag bug (text chunks are tagged with the page where they start)

xfail cases (answer only in images) are reported apart; one that hits is an XPASS.
Disambiguation cases also need the right page to rank above every `wrong_pages` page.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from src.config import load_config  # noqa: E402
from src.data_models import QueryState  # noqa: E402
from src.ingestion.tools import ChromaVectorStore, EmbeddingService  # noqa: E402
from src.query import nodes  # noqa: E402

CASES = ROOT / "tests" / "dexter-ground-truth" / "retrieval.yaml"
RESULTS = ROOT / "tests" / "results" / "dexter"
RANK_DEPTH = 20
CATEGORIES = ("part_number", "spec", "table_cell", "disambiguation", "concept", "image_only")


def meta_of(hit) -> dict:
    """Raw hybrid_search dicts and QueryResult objects -> (filename, page, chunk_type)."""
    if isinstance(hit, dict):
        m = hit["metadata"]
        return {"file": m.get("source_filename"), "page": int(m.get("page_number") or 0),
                "type": m.get("chunk_type", "text"), "match": hit.get("match_type", "")}
    m = hit.metadata
    return {"file": m.source_filename, "page": int(m.page_number or 0),
            "type": getattr(m, "chunk_type", "text"), "match": getattr(hit, "match_type", "")}


def is_hit(m: dict, case: dict, filename: str, lenient: bool, chunk_type: str | None = None) -> bool:
    if m["file"] != filename:
        return False
    if chunk_type and not m["type"].startswith(chunk_type):
        return False
    pages = set(case["pages"])
    return m["page"] in pages or (lenient and m["page"] + 1 in pages)


def first_rank(hits: list[dict], test) -> int | None:
    return next((i + 1 for i, m in enumerate(hits) if test(m)), None)


def run_case(case: dict, filename: str, store, embedder, config) -> dict:
    query = case["query"]
    embedding = embedder.embed_text(query)
    ranked = [meta_of(h) for h in nodes._hybrid_search(store, query, embedding, RANK_DEPTH)]
    state = QueryState(original_query=query, query_validated=True, query_embedding=embedding, config=config)
    context = [meta_of(r) for r in nodes.vector_store_search(state).get("search_results", [])]

    out = {"id": case["id"], "category": case["category"], "query": query, "pages": case["pages"],
           "xfail": bool(case.get("xfail")), "verified": bool(case.get("verified"))}
    for mode in ("strict", "lenient"):
        lenient = mode == "lenient"
        rank = first_rank(ranked, lambda m: is_hit(m, case, filename, lenient))
        entry = {"rank": rank, "in_context": any(is_hit(m, case, filename, lenient) for m in context)}
        if case.get("chunk_type"):
            entry["typed_rank"] = first_rank(
                ranked, lambda m: is_hit(m, case, filename, lenient, case["chunk_type"]))
        if case.get("wrong_pages"):
            wrong = {**case, "pages": case["wrong_pages"]}
            # In lenient mode a chunk tagged the page before the right page is the
            # right page's own text (page-tag bug), so it isn't counted as wrong.
            wrong_rank = first_rank(ranked, lambda m: is_hit(m, wrong, filename, False)
                                    and not (lenient and m["page"] + 1 in set(case["pages"])))
            entry["wrong_rank"] = wrong_rank
            entry["disambiguated"] = rank is not None and rank <= 10 and (wrong_rank is None or rank < wrong_rank)
        out[mode] = entry
    out["top"] = ranked[:3]
    out["catalog_in_top10"] = sum(m["file"] == filename for m in ranked[:10])
    out["context_size"] = len(context)
    return out


def summarize(results: list[dict]) -> dict:
    """Per category and overall, strict and lenient. xfail cases kept apart."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        groups[r["category"]].append(r)
        if not r["xfail"]:
            groups["ALL (excluding xfail)"].append(r)

    def agg(rows: list[dict], mode: str) -> dict:
        ranks = [r[mode]["rank"] for r in rows]
        out = {
            "cases": len(rows),
            "recall@1": round(sum(1 for k in ranks if k and k <= 1) / len(rows), 3),
            "recall@5": round(sum(1 for k in ranks if k and k <= 5) / len(rows), 3),
            "recall@10": round(sum(1 for k in ranks if k and k <= 10) / len(rows), 3),
            "mrr": round(sum(1 / k for k in ranks if k) / len(rows), 3),
            "in_context": round(sum(r[mode]["in_context"] for r in rows) / len(rows), 3),
        }
        typed = [r[mode]["typed_rank"] for r in rows if "typed_rank" in r[mode]]
        if typed:
            out["typed_recall@10"] = round(sum(1 for k in typed if k and k <= 10) / len(typed), 3)
        dis = [r[mode]["disambiguated"] for r in rows if "disambiguated" in r[mode]]
        if dis:
            out["disambiguated"] = f"{sum(dis)}/{len(dis)}"
        return out

    summary = {name: {mode: agg(rows, mode) for mode in ("strict", "lenient")} for name, rows in groups.items()}
    summary["xpass"] = [r["id"] for r in results if r["xfail"] and r["strict"]["rank"] and r["strict"]["rank"] <= 10]
    return summary


def print_report(result: dict) -> None:
    print(f"Retrieval '{result['label']}' ({len(result['cases'])} cases, ranked to {RANK_DEPTH})\n")
    print(f"{'id':10} {'strict':>6} {'lenient':>7} {'ctx':>4} {'type':>5} {'dexter@10':>9}  top hit")
    for r in result["cases"]:
        s, l = r["strict"], r["lenient"]
        rank = lambda k: str(k) if k else "-"  # noqa: E731
        typed = rank(s.get("typed_rank")) if "typed_rank" in s else ""
        ctx = "yes" if s["in_context"] else ("len" if l["in_context"] else "no")
        top = r["top"][0] if r["top"] else {}
        top_file = "catalog" if top.get("file") == result["file"] else (top.get("file") or "-")[:28]
        flag = " xfail" if r["xfail"] else ""
        extra = ""
        if "disambiguated" in s:
            extra = f"  wrong@{rank(s['wrong_rank'])} {'ok' if s['disambiguated'] else 'LOST'}"
        print(f"{r['id']:10} {rank(s['rank']):>6} {rank(l['rank']):>7} {ctx:>4} {typed:>5} "
              f"{r['catalog_in_top10']:>9}  {top_file} p{top.get('page', '-')} {top.get('type', '')}"
              f" {top.get('match', '')}{extra}{flag}")
    print("\nctx: yes = true page in the LLM's first context, len = only the page before (page-tag bug)")
    print(f"\n{'group':24} {'mode':8} {'R@1':>5} {'R@5':>5} {'R@10':>5} {'MRR':>5} {'ctx':>5}  other")
    order = list(CATEGORIES) + ["ALL (excluding xfail)"]
    for name in order:
        if name not in result["summary"]:
            continue
        for mode in ("strict", "lenient"):
            s = result["summary"][name][mode]
            other = "  ".join(f"{k} {v}" for k, v in s.items() if k in ("typed_recall@10", "disambiguated"))
            print(f"{name if mode == 'strict' else '':24} {mode:8} {s['recall@1']:>5.0%} {s['recall@5']:>5.0%} "
                  f"{s['recall@10']:>5.0%} {s['mrr']:>5.2f} {s['in_context']:>5.0%}  {other}")
    print(f"\nXPASS (image-only cases now found in the top 10): {result['summary']['xpass'] or 'none'}")


def compare(old_label: str, new_label: str) -> None:
    old = json.loads((RESULTS / old_label / "retrieval.json").read_text())
    new = json.loads((RESULTS / new_label / "retrieval.json").read_text())
    print(f"Retrieval '{old_label}' -> '{new_label}'\n")
    for name, modes in new["summary"].items():
        if name == "xpass" or name not in old["summary"]:
            continue
        a, b = old["summary"][name]["strict"], modes["strict"]
        print(f"  {name:24} R@10 {a['recall@10']:.0%}->{b['recall@10']:.0%}  MRR {a['mrr']:.2f}->{b['mrr']:.2f}  "
              f"ctx {a['in_context']:.0%}->{b['in_context']:.0%}")
    print("\nCases whose strict rank changed:")
    before = {r["id"]: r for r in old["cases"]}
    for r in new["cases"]:
        a = before.get(r["id"])
        if a and a["strict"]["rank"] != r["strict"]["rank"]:
            print(f"  {r['id']:10} {a['strict']['rank'] or '-'} -> {r['strict']['rank'] or '-'}  {r['query']}")
    print(f"\nXPASS: {old['summary']['xpass']} -> {new['summary']['xpass']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrieval-only checks for the Dexter catalog.")
    parser.add_argument("--label", default="baseline", help="results folder name (default: baseline)")
    parser.add_argument("--only", help="comma-separated case IDs or ID prefixes")
    parser.add_argument("--compare", nargs=2, metavar=("OLD", "NEW"), help="compare two saved results")
    args = parser.parse_args()
    if args.compare:
        compare(*args.compare)
        return 0

    spec = yaml.safe_load(CASES.read_text())
    cases = spec["cases"]
    if args.only:
        wanted = [w.strip() for w in args.only.split(",") if w.strip()]
        cases = [c for c in cases if any(c["id"] == w or c["id"].startswith(w) for w in wanted)]
    config = load_config()
    store = ChromaVectorStore(config.chroma_persist_directory, config.chroma_collection_name)
    store.initialize_collection()
    embedder = EmbeddingService(base_url=config.embedding_base_url, model=config.embedding_model,
                                api_key=config.embedding_api_key)

    results = []
    for case in cases:
        print(f"  {case['id']} ...", end="\r", flush=True)
        results.append(run_case(case, spec["file"], store, embedder, config))

    result = {
        "label": args.label,
        "file": spec["file"],
        "created": datetime.now().isoformat(timespec="seconds"),
        "rank_depth": RANK_DEPTH,
        "corpus_chunks": store.collection.count(),
        "summary": summarize(results),
        "cases": results,
    }
    if not args.only:
        out = RESULTS / args.label / "retrieval.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print_report(result)
    if not args.only:
        print(f"\nWrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
