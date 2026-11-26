"""
Live query tests: run the cases in tests/test-queries.md through the query
pipeline and grade each turn against its expectations.

    python tests/test_queries.py                      every case, once
    python tests/test_queries.py --only C,RW01        IDs starting with C, plus RW01
    python tests/test_queries.py --repeat 3           each case 3 times (pass rates)
    python tests/test_queries.py --rewrite-only       only the query rewrite (fast)
    python tests/test_queries.py --validate           check the cases, run nothing

Each run writes a new report, tests/results/query_results_001.txt, _002.txt, ...
(earlier runs are kept). The exit code is 1 when any check fails, except in
cases marked xfail or baseline_fail (reported as XFAIL/XPASS and KFAIL).

The case format is described at the top of tests/test-queries.md. Checks are
grouped by the stage that failed: rewrite, retrieval, decision (the kind of
reply), answer and error (the pipeline itself).
"""
import argparse
import re
import sys
from collections import Counter
from datetime import datetime
from logging import CRITICAL
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml

from src.config import load_config
from src.data_models import ChatTurn, QueryState
from src.ingestion.tools import ChromaVectorStore
from src.query import nodes
from src.query.graph import chat_turn_from_result, create_query_graph, execute_query_pipeline
from src.utils.logging import setup_logging

QUERIES_FILE = Path(__file__).parent / "test-queries.md"
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_FILE_PATTERN = re.compile(r"^query_results_(\d+)\.txt$")

KINDS = ("answer", "insufficient", "clarify", "empty", "error")
STAGES = ("rewrite", "retrieval", "decision", "answer", "error")
CASE_HEADER = re.compile(r"^## +([A-Z]+\d+):\s*(.*)$", re.MULTILINE)
YAML_BLOCK = re.compile(r"^```yaml\s*\n(.*?)^```\s*$", re.MULTILINE | re.DOTALL)

# expect: key -> the stage a failure belongs to
EXPECT_KEYS = {
    "rewrite_has": "rewrite",
    "rewrite_lacks": "rewrite",
    "retrieved": "retrieval",
    "kind": "decision",
    "cited": "answer",
    "cited_any": "answer",
    "facts": "answer",
    "not_facts": "answer",
    "options_has": "answer",
    "options_min": "answer",
    "diagrams": "answer",
    "max_searches": "answer",
}
REGEX_KEYS = ("rewrite_has", "rewrite_lacks", "facts", "not_facts", "options_has")
FILE_KEYS = ("retrieved", "cited", "cited_any")


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def file_spec(value) -> tuple[str, list[int]]:
    """An expected document: "a.pdf" or {file: a.pdf, pages: [1, 2]} (any of the pages)."""
    if isinstance(value, str) and value.strip():
        return value.strip(), []
    if isinstance(value, dict) and isinstance(value.get("file"), str) and set(value) <= {"file", "pages"}:
        pages = _as_list(value.get("pages"))
        if all(isinstance(p, int) for p in pages):
            return value["file"], pages
    raise ValueError(f"not a document: {value!r} (use a filename or {{file: ..., pages: [...]}})")


def _check_expect(expect: dict) -> None:
    unknown = set(expect) - set(EXPECT_KEYS)
    if unknown:
        raise ValueError(f"unknown expect key(s): {', '.join(sorted(unknown))}")
    for kind in _as_list(expect.get("kind")):
        if kind not in KINDS:
            raise ValueError(f"unknown kind {kind!r} (one of {', '.join(KINDS)})")
    for key in REGEX_KEYS:
        for pattern in _as_list(expect.get(key)):
            try:
                re.compile(str(pattern))
            except re.error as e:
                raise ValueError(f"{key}: bad pattern {pattern!r}: {e}")
    for key in FILE_KEYS:
        for value in _as_list(expect.get(key)):
            file_spec(value)
    for key in ("options_min", "max_searches"):
        if key in expect and (isinstance(expect[key], bool) or not isinstance(expect[key], int)):
            raise ValueError(f"{key} must be a whole number")


CASE_KEYS = {"history", "turns", "xfail", "baseline_fail", "verified"}


def _check_case(body) -> None:
    if not isinstance(body, dict) or set(body) - CASE_KEYS:
        raise ValueError("a case holds 'turns' and optional 'history', 'xfail', 'baseline_fail' and 'verified'")
    for key in ("xfail", "verified"):
        if key in body and not isinstance(body[key], bool):
            raise ValueError(f"'{key}' must be true or false")
    if "baseline_fail" in body and (not isinstance(body["baseline_fail"], str) or not body["baseline_fail"].strip()):
        raise ValueError("'baseline_fail' must name the known reason, e.g. page-tag")
    turns = body.get("turns")
    if not isinstance(turns, list) or not turns:
        raise ValueError("'turns' must be a non-empty list")
    for turn in turns:
        if not isinstance(turn, dict) or not isinstance(turn.get("ask"), str) or set(turn) - {"ask", "expect"}:
            raise ValueError(f"each turn needs 'ask' and an optional 'expect': {turn!r}")
        _check_expect(turn.get("expect") or {})
    for past in _as_list(body.get("history")):
        if (not isinstance(past, dict) or not isinstance(past.get("ask"), str)
                or not isinstance(past.get("answer"), str)
                or set(past) - {"ask", "answer", "kind", "options", "docs"}):
            raise ValueError(f"each history entry needs 'ask' and 'answer': {past!r}")
        if past.get("kind", "answer") not in ("answer", "insufficient", "clarify"):
            raise ValueError(f"history kind must be answer, insufficient or clarify: {past!r}")
        for value in _as_list(past.get("docs")):
            file_spec(value)


def parse_cases(text: str) -> list[dict]:
    """
    The cases in the markdown file: each "## ID: title" heading followed by one
    ```yaml block. Raises ValueError (naming the case) on any mistake.
    """
    headers = list(CASE_HEADER.finditer(text))
    cases: list[dict] = []
    seen: set[str] = set()
    for i, header in enumerate(headers):
        case_id, title = header.group(1), header.group(2).strip()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        blocks = YAML_BLOCK.findall(text[header.end():end])
        try:
            if case_id in seen:
                raise ValueError("duplicate ID")
            if len(blocks) != 1:
                raise ValueError(f"expected one ```yaml block, found {len(blocks)}")
            body = yaml.safe_load(blocks[0])
            _check_case(body)
        except (ValueError, yaml.YAMLError) as e:
            raise ValueError(f"{case_id}: {e}") from e
        seen.add(case_id)
        cases.append({"id": case_id, "title": title, "turns": body["turns"],
                      "history": _as_list(body.get("history")),
                      "xfail": body.get("xfail", False),
                      "baseline_fail": body.get("baseline_fail"),
                      "verified": body.get("verified", True)})
    return cases


def select_cases(cases: list[dict], only: str | None) -> list[dict]:
    """Cases whose ID equals or starts with one of the comma-separated values."""
    if not only:
        return cases
    wanted = [w.strip().upper() for w in only.split(",") if w.strip()]
    return [c for c in cases if any(c["id"] == w or (w.isalpha() and c["id"].rstrip("0123456789") == w)
                                    for w in wanted)]


# --------------------------------------------------------------------------
# Grading
# --------------------------------------------------------------------------
def same_document(expected: str, stored: str) -> bool:
    """The expected name is the stored path or its trailing part, in any case."""
    expected, stored = expected.strip().lower(), (stored or "").strip().lower()
    return stored == expected or stored.endswith("/" + expected)


def _found(spec, documents: list[tuple[str, int | None]]) -> bool:
    name, pages = file_spec(spec)
    return any(same_document(name, f) and (not pages or p in pages) for f, p in documents)


def reply_kind(result: QueryState) -> str:
    if result.response:
        return result.response.get("kind", "error")
    return "error" if result.errors else "empty"


def reply_text(result: QueryState) -> str:
    """What the user reads (answer, steps, options), without markdown emphasis."""
    if not result.response:
        return ""
    return nodes.response_history_text(result.response).replace("*", "")


def _matches(pattern, text: str) -> bool:
    return re.search(str(pattern), text or "", re.IGNORECASE) is not None


def grade_rewrite(expect: dict, rewrite: str) -> list[dict]:
    checks = []
    for pattern in _as_list(expect.get("rewrite_has")):
        checks.append(_check("rewrite", f"rewrite has {pattern!r}", _matches(pattern, rewrite), rewrite))
    for pattern in _as_list(expect.get("rewrite_lacks")):
        checks.append(_check("rewrite", f"rewrite lacks {pattern!r}", not _matches(pattern, rewrite), rewrite))
    return checks


def _check(stage: str, name: str, passed: bool, detail: str = "") -> dict:
    return {"stage": stage, "name": name, "passed": bool(passed), "detail": "" if passed else detail}


def grade_turn(expect: dict, result: QueryState) -> list[dict]:
    """Every check for one turn, from the pipeline's final state."""
    kind = reply_kind(result)
    response = result.response or {}
    text = reply_text(result)
    checks = []

    if kind == "error" and "error" not in _as_list(expect.get("kind")):
        checks.append(_check("error", "pipeline produced a reply", False, "; ".join(result.errors)[:300]))

    checks += grade_rewrite(expect, result.retrieval_query or result.original_query or "")

    new_documents = [(r.metadata.source_filename, r.metadata.page_number)
                     for r in result.search_results if not r.from_history]
    for spec in _as_list(expect.get("retrieved")):
        checks.append(_check("retrieval", f"retrieved {spec}", _found(spec, new_documents),
                             "got " + ", ".join(sorted({Path(f).name for f, _ in new_documents})) or "nothing"))

    kinds = _as_list(expect.get("kind"))
    if kinds:
        checks.append(_check("decision", f"kind is {' or '.join(kinds)}", kind in kinds, f"got {kind}"))

    sources = [(s.get("filename"), s.get("page")) for s in response.get("sources", [])]
    shown = ", ".join(f"{f} p{p}" for f, p in sources) or "no sources"
    for spec in _as_list(expect.get("cited")):
        checks.append(_check("answer", f"cites {spec}", _found(spec, sources), shown))
    any_of = _as_list(expect.get("cited_any"))
    if any_of:
        checks.append(_check("answer", f"cites one of {any_of}", any(_found(s, sources) for s in any_of), shown))

    for pattern in _as_list(expect.get("facts")):
        checks.append(_check("answer", f"says {pattern!r}", _matches(pattern, text), "not in the reply"))
    for pattern in _as_list(expect.get("not_facts")):
        checks.append(_check("answer", f"does not say {pattern!r}", not _matches(pattern, text), "found in the reply"))

    options = [response.get("answer", ""), *response.get("options", [])] if kind == "clarify" else []
    for pattern in _as_list(expect.get("options_has")):
        checks.append(_check("answer", f"offers {pattern!r}", any(_matches(pattern, o) for o in options),
                             f"options: {response.get('options', [])}"))

    if "options_min" in expect:
        offered = response.get("options", []) if kind == "clarify" else []
        checks.append(_check("answer", f"offers at least {expect['options_min']} options",
                             len(offered) >= expect["options_min"], f"options: {offered}"))
    if "diagrams" in expect:
        count = len(response.get("diagrams", []))
        wanted = bool(expect["diagrams"])
        checks.append(_check("answer", "shows a diagram" if wanted else "shows no diagram",
                             bool(count) == wanted, f"{count} diagram(s)"))
    if "max_searches" in expect:
        checks.append(_check("answer", f"at most {expect['max_searches']} extra searches",
                             result.search_round <= expect["max_searches"], f"used {result.search_round}"))
    return checks


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------
class Corpus:
    """The ingested documents, for scripted history and --validate."""

    def __init__(self, config) -> None:
        self.store = ChromaVectorStore(config.chroma_persist_directory, config.chroma_collection_name)
        self.store.initialize_collection()
        data = self.store.collection.get(include=["metadatas"])
        self.pages: dict[str, set[int]] = {}
        for meta in data["metadatas"]:
            self.pages.setdefault(meta["source_filename"], set()).add(meta["page_number"])

    def resolve(self, name: str) -> list[str]:
        return sorted(f for f in self.pages if same_document(name, f))

    def chunks(self, spec) -> list:
        """Stored chunks (not diagrams) of an expected document, as QueryResults."""
        name, pages = file_spec(spec)
        matches = self.resolve(name)
        if len(matches) != 1:
            raise ValueError(f"history document {name!r} matches {len(matches)} stored documents")
        where = [{"source_filename": {"$eq": matches[0]}}, {"chunk_type": {"$ne": nodes.DIAGRAM_CHUNK_TYPE}}]
        if pages:
            where.append({"page_number": {"$in": pages}})
        found = [nodes._build_query_result(raw) for raw in self.store.get_chunks({"$and": where})]
        if not found:
            raise ValueError(f"history document {name!r} has no chunks on pages {pages}")
        return sorted(found, key=nodes._reading_order_key)[:nodes.MAX_HISTORY_DOCS]


def scripted_history(entries: list[dict], corpus: Corpus | None) -> list[ChatTurn]:
    """The ChatTurns for a case's fixed 'history' entries."""
    turns = []
    for past in entries:
        kind = past.get("kind", "answer")
        answer = nodes.response_history_text({"answer": past["answer"], "options": past.get("options", [])})
        documents = []
        if corpus:
            documents = [chunk for spec in _as_list(past.get("docs")) for chunk in corpus.chunks(spec)]
        turns.append(ChatTurn(question=past["ask"], answer=answer, kind=kind, documents=documents))
    return turns


def run_case(case: dict, config, graph, corpus: Corpus) -> list[dict]:
    """One conversation: each turn's reply becomes history for the next."""
    history = scripted_history(case["history"], corpus)
    records = []
    for turn in case["turns"]:
        result = execute_query_pipeline(turn["ask"], config, graph, chat_history=list(history))
        expect = turn.get("expect") or {}
        records.append({
            "ask": turn["ask"],
            "rewrite": result.retrieval_query if history else None,
            "kind": reply_kind(result),
            "status": result.status,
            "searches": result.search_round,
            "reply": result.final_response or "",
            "errors": list(result.errors),
            "checks": grade_turn(expect, result),
        })
        remembered = chat_turn_from_result(turn["ask"], result)
        if remembered:
            history.append(remembered)
    return records


def run_rewrite(case: dict, config) -> list[dict] | None:
    """
    Only the query rewrite, for a first turn that follows scripted history (later
    turns depend on real answers). None when the case has nothing to check.
    """
    turn = case["turns"][0]
    expect = turn.get("expect") or {}
    if not case["history"] or not ({"rewrite_has", "rewrite_lacks"} & set(expect)):
        return None
    state = QueryState(original_query=turn["ask"], query_validated=True, config=config,
                       chat_history=scripted_history(case["history"], None))
    rewrite = nodes.rewrite_query(state)["retrieval_query"]
    return [{"ask": turn["ask"], "rewrite": rewrite, "kind": None, "status": None, "searches": 0,
             "reply": "", "errors": [], "checks": grade_rewrite(expect, rewrite)}]


def validate(cases: list[dict], corpus: Corpus) -> list[str]:
    """Problems with expected documents: not ingested, or a page that doesn't exist."""
    problems = []
    for case in cases:
        specs = [s for past in case["history"] for s in _as_list(past.get("docs"))]
        specs += [s for turn in case["turns"] for key in FILE_KEYS
                  for s in _as_list((turn.get("expect") or {}).get(key))]
        for spec in specs:
            name, pages = file_spec(spec)
            matches = corpus.resolve(name)
            if not matches:
                problems.append(f"{case['id']}: {name!r} is not in the database")
            elif pages and not any(set(pages) & corpus.pages[m] for m in matches):
                problems.append(f"{case['id']}: {name!r} has no chunks on pages {pages}")
    return problems


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------
def run_passed(records: list[dict]) -> bool:
    return all(c["passed"] for r in records for c in r["checks"])


def expected_to_fail(case: dict) -> bool:
    """xfail (answer only in images) or baseline_fail (a known, recorded bug)."""
    return bool(case.get("xfail") or case.get("baseline_fail"))


def case_status(case: dict, passes: int, runs: int) -> str:
    """
    PASS / FAIL / FLAKY for ordinary cases.
    xfail cases:          XFAIL (all runs fail), XPASS (all pass), XFLAKY.
    baseline_fail cases:  KFAIL (known failure), PASS, KFLAKY.
    """
    if case.get("xfail"):
        return "XPASS" if passes == runs else "XFAIL" if passes == 0 else "XFLAKY"
    if case.get("baseline_fail"):
        return "PASS" if passes == runs else "KFAIL" if passes == 0 else "KFLAKY"
    return "PASS" if passes == runs else "FAIL" if passes == 0 else "FLAKY"


def case_tags(case: dict) -> str:
    tags = []
    if case.get("xfail"):
        tags.append("xfail")
    if case.get("baseline_fail"):
        tags.append(f"known: {case['baseline_fail']}")
    if not case.get("verified", True):
        tags.append("unverified")
    return f"  [{', '.join(tags)}]" if tags else ""


def summarize(results: list[dict]) -> list[str]:
    """
    Pass rates per case and the number of failed checks per stage. Cases expected
    to fail (xfail, baseline_fail) are counted apart and never count as failures.
    """
    lines = []
    failures: Counter = Counter()
    passed_runs = total_runs = 0
    expected: Counter = Counter()
    for item in results:
        case = item["case"]
        passes = sum(run_passed(records) for records in item["runs"])
        status = case_status(case, passes, len(item["runs"]))
        failed = Counter(c["name"] for records in item["runs"] for r in records
                         for c in r["checks"] if not c["passed"])
        if expected_to_fail(case):
            expected[status] += 1
        else:
            passed_runs += passes
            total_runs += len(item["runs"])
            failures.update(c["stage"] for records in item["runs"] for r in records
                            for c in r["checks"] if not c["passed"])
        line = f"{status:6} {case['id']:6} {passes}/{len(item['runs'])}  {case['title']}{case_tags(case)}"
        if failed:
            line += "\n" + "\n".join(f"              x{n} {name}" for name, n in failed.most_common())
        lines.append(line)
    lines.append("")
    lines.append(f"Runs passed: {passed_runs}/{total_runs}" + (" (excluding expected failures)" if expected else ""))
    lines.append("Failed checks by stage: "
                 + (", ".join(f"{s} {failures[s]}" for s in STAGES if failures[s]) or "none"))
    if expected:
        lines.append("Expected failures: " + ", ".join(f"{s} {n}" for s, n in sorted(expected.items())))
    return lines


def all_passed(results: list[dict]) -> bool:
    """Exit status: every ordinary case passed every run (expected failures don't count)."""
    return all(run_passed(r) for item in results if not expected_to_fail(item["case"]) for r in item["runs"])


def format_report(results: list[dict], settings: str) -> str:
    rule = "=" * 80
    out = [rule, "QUERY TEST RESULTS", rule,
           f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", settings, rule, "",
           *summarize(results), ""]
    for item in results:
        case = item["case"]
        out += ["", rule, f"{case['id']}: {case['title']}{case_tags(case)}", rule]
        for run, records in enumerate(item["runs"], 1):
            out += ["", f"--- Run {run}: {'PASS' if run_passed(records) else 'FAIL'}"]
            for number, record in enumerate(records, 1):
                out += ["", f"Turn {number}: {record['ask']}"]
                if record["rewrite"] is not None:
                    out.append(f"Rewrite: {record['rewrite']}")
                if record["kind"] is not None:
                    out.append(f"Kind: {record['kind']}   Status: {record['status']}   "
                               f"Extra searches: {record['searches']}")
                for check in record["checks"]:
                    line = f"  [{'PASS' if check['passed'] else 'FAIL'}] ({check['stage']}) {check['name']}"
                    out.append(line + (f" -- {check['detail']}" if check["detail"] else ""))
                if record["reply"]:
                    out += ["", record["reply"]]
                out += [f"Error: {e}" for e in record["errors"]]
    return "\n".join(out) + "\n"


def next_results_file(results_dir: Path) -> Path:
    """
    Path for this run's results: query_results_NNN.txt, one higher than the
    highest existing run number (001 for the first run).

    Only files named exactly query_results_<number>.txt count, so other files
    in the folder (e.g. query_results_HW11.txt) are ignored and never overwritten.
    """
    numbers = [
        int(match.group(1))
        for path in results_dir.glob("query_results_*.txt")
        if (match := RESULTS_FILE_PATTERN.match(path.name))
    ]
    return results_dir / f"query_results_{max(numbers, default=0) + 1:03d}.txt"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run and grade the live query tests.")
    parser.add_argument("--only", help="comma-separated IDs or ID prefixes, e.g. C,RW01")
    parser.add_argument("--repeat", type=int, default=1, help="runs per case (default 1)")
    parser.add_argument("--rewrite-only", action="store_true", help="check only the query rewrite")
    parser.add_argument("--validate", action="store_true", help="check the cases without running them")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")

    setup_logging(level=CRITICAL)
    try:
        cases = select_cases(parse_cases(QUERIES_FILE.read_text()), args.only)
    except ValueError as e:
        print(f"Error in {QUERIES_FILE.name}: {e}")
        return 1
    if not cases:
        print("No matching cases.")
        return 1

    config = load_config()
    corpus = None if args.rewrite_only else Corpus(config)
    if corpus:
        problems = validate(cases, corpus)
        if problems:
            print("Cases refer to documents that are not available:", *problems, sep="\n  ")
            return 1
    if args.validate:
        print(f"{len(cases)} cases OK")
        return 0

    graph = None if args.rewrite_only else create_query_graph(config)
    results = []
    for case in cases:
        runs = []
        for _ in range(args.repeat):
            if args.rewrite_only:
                records = run_rewrite(case, config)
                if records is None:
                    break
            else:
                try:
                    records = run_case(case, config, graph, corpus)
                except Exception as e:  # keep going; the report shows what broke
                    records = [{"ask": case["turns"][0]["ask"], "rewrite": None, "kind": "error",
                                "status": "failed", "searches": 0, "reply": "", "errors": [str(e)],
                                "checks": [_check("error", "case ran", False, str(e)[:300])]}]
            runs.append(records)
            print(f"{case['id']}: {'pass' if run_passed(records) else 'FAIL'}", flush=True)
        if runs:
            results.append({"case": case, "runs": runs})

    if not results:
        print("No cases with scripted history and rewrite expectations.")
        return 1

    settings = (f"Model: {config.llm_model}   Embeddings: {config.embedding_model}   "
                f"Repeat: {args.repeat}" + ("   Rewrite only" if args.rewrite_only else ""))
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output = next_results_file(RESULTS_DIR)
    output.write_text(format_report(results, settings))
    print("", *summarize(results), f"\nReport: {output}", sep="\n")
    return 0 if all_passed(results) else 1


if __name__ == "__main__":
    sys.exit(main())
