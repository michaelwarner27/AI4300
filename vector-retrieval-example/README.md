# Lecture 170 Vector Retrieval Example

This project keeps ingestion separate from query-time agent execution. The
ingestion command reads PDF and UTF-8 text files, chunks their text, requests
embeddings from the course service, and persists records in local ChromaDB. The
query command runs a bounded LangGraph path:

```text
question -> model action -> retrieve once -> model synthesis -> end
```

The controller executes at most one retrieval. The final model call has no tools
bound and answers from the one retrieval observation. The retrieval request
contains a query, optional metadata filters, and a result count. The CLI prints
the question, tool request, source-labeled observation, answer, retrieval status,
terminal reason, and retrieval-call count.

## Setup

Install Python 3.11 or newer and `uv`. Run these commands from this directory:

```sh
uv sync --locked
cp .env.example .env
```

Edit `.env` and replace both placeholder API keys with your assigned key. The
embedding and chat services use different classroom hostnames, as shown in
`.env.example`. Connect to the course network before running either service.

## Download The Lab Documents

The student package does not contain source PDFs. Follow
[`documents/README.md`](documents/README.md) to download these four public
documents into `documents/`:

- NIST AI Risk Management Framework 1.0
- Attention Is All You Need
- Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks
- ReAct: Synergizing Reasoning and Acting in Language Models

The document guide provides the expected filenames, browser links, and `curl`
commands. Downloaded PDFs remain local and are excluded from Git and the student
ZIP.

## Ingest Documents

The `--source` option accepts one or more `.pdf` or `.txt` files and directories.
Directories are searched recursively. Rebuild the example collection with:

```sh
uv run python -m app.ingest --source documents --rebuild
```

The command prints and writes `manifest.json`. Chroma data is stored in
`chroma_db/`. Both outputs are ignored by Git because they are local generated
artifacts. Without `--rebuild`, chunks for each supplied source are replaced and
other sources already in the collection remain.

The default chunk size is 800 characters with 120 characters of overlap. Both
values are command-line options:

```sh
uv run python -m app.ingest --source documents --rebuild \
  --chunk-size 400 --chunk-overlap 60
```

## Inspect The Result

Inspect ingestion metadata and source hashes:

```sh
uv run python -m app.inspect --manifest
```

Inspect chunk text, IDs, and metadata:

```sh
uv run python -m app.inspect --records
```

## Ask One Question

Pass one question as a quoted CLI argument:

```sh
uv run python -m app.ask "What are the four functions in the NIST AI RMF?"
```

Start with questions that target one document:

```sh
uv run python -m app.ask "What are the four functions in the NIST AI RMF?"
uv run python -m app.ask "Why are attention dot products scaled by the square root of d_k?"
uv run python -m app.ask "How do RAG-Sequence and RAG-Token differ?"
uv run python -m app.ask "How does ReAct interleave reasoning and action?"
```

The numeric `distance` in each result is Chroma's vector distance; lower values
indicate closer candidates. Answer support requires evidence in the returned
passage in addition to a low distance.

## Run The Retrieval Experiments

Use the same question set for every run and save each CLI trace.

1. Ask the four document-specific questions above. Record the top-ranked source,
   page, distance, and whether the returned text supports the answer.
2. Ask an exact-identifier question: `What does GOVERN 1.1 require?`
3. Ask a paraphrase question: `Why does the Transformer reduce the cost of
   relating positions that are far apart?`
4. Ask a cross-document question: `How do the RAG and ReAct papers use external
   information differently?`
5. Rebuild with `--chunk-size 400 --chunk-overlap 60`, then repeat the questions.
6. Rebuild with `--chunk-size 1400 --chunk-overlap 200`, then repeat them again.
7. Compare chunk count, retrieved sources, page labels, distances, and answer
   support across the three configurations.

The experiment report should identify one changed result, the pipeline stage
responsible for the change, and the trace evidence supporting that diagnosis.

## Run Deterministic Tests

These tests exercise local source discovery, metadata, and stable chunk IDs. They
run offline without contacting either model service:

```sh
uv run python -m unittest discover -s tests
```

## Build The Student Archive

From the instructor copy, build the distribution archive with:

```sh
uv run python scripts/build_student_zip.py
```

The script writes `dist/vector-retrieval-example.zip` from an explicit public
file allowlist. It includes `.env.example` and `documents/README.md`. It excludes
`.env`, downloaded PDFs, local Chroma data, the virtual environment, and
generated manifests.
