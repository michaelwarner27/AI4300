"""Ingest PDF and text files into a persistent local Chroma collection."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

from app.config import DEFAULT_DB_PATH, DEFAULT_MANIFEST_PATH, setting
from app.store import open_store

SUPPORTED_SUFFIXES = {".pdf", ".txt"}


def discover_sources(inputs: list[Path]) -> list[Path]:
    """Expand files and directories into a stable list of supported files."""
    sources: set[Path] = set()
    for input_path in inputs:
        path = input_path.expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"Source does not exist: {input_path}")
        if path.is_file():
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                raise ValueError(f"Unsupported file type: {input_path}")
            sources.add(path)
        else:
            sources.update(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file()
                and candidate.suffix.lower() in SUPPORTED_SUFFIXES
            )
    if not sources:
        raise ValueError("No .pdf or .txt files were found")
    return sorted(sources)


def read_source(path: Path) -> list[Document]:
    """Extract page-aware documents from one source file."""
    source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    common = {
        "source": path.name,
        "source_path": str(path),
        "source_hash": source_hash,
        "media_type": "application/pdf" if path.suffix.lower() == ".pdf" else "text/plain",
    }
    if path.suffix.lower() == ".txt":
        return [
            Document(
                page_content=path.read_text(encoding="utf-8"),
                metadata={**common, "page": 1},
            )
        ]

    documents: list[Document] = []
    for page_number, page in enumerate(PdfReader(path).pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            documents.append(
                Document(
                    page_content=text,
                    metadata={**common, "page": page_number},
                )
            )
    return documents


def chunk_sources(
    sources: list[Path], chunk_size: int = 800, chunk_overlap: int = 120
) -> tuple[list[Document], list[str]]:
    """Split sources and assign IDs that remain stable for unchanged content."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        add_start_index=True,
    )
    chunks: list[Document] = []
    ids: list[str] = []
    for source in sources:
        for chunk in splitter.split_documents(read_source(source)):
            identity = "|".join(
                [
                    chunk.metadata["source_hash"],
                    str(chunk.metadata["page"]),
                    str(chunk.metadata["start_index"]),
                    chunk.page_content,
                ]
            )
            chunk_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
            chunk.metadata["chunk_id"] = chunk_id
            chunks.append(chunk)
            ids.append(chunk_id)
    if not chunks:
        raise ValueError("The source files contained no extractable text")
    return chunks, ids


def ingest(
    source_inputs: list[Path],
    db_path: Path,
    manifest_path: Path,
    rebuild: bool,
    chunk_size: int,
    chunk_overlap: int,
) -> dict[str, object]:
    """Build the collection and return an inspectable ingestion manifest."""
    sources = discover_sources(source_inputs)
    if chunk_overlap >= chunk_size:
        raise ValueError("Chunk overlap must be smaller than chunk size")
    chunks, ids = chunk_sources(sources, chunk_size, chunk_overlap)

    if rebuild and db_path.exists():
        shutil.rmtree(db_path)
    store = open_store(db_path)

    # Replace prior chunks for each source before adding the current version.
    for source in sources:
        old = store.get(where={"source_path": str(source)}, include=[])
        if old["ids"]:
            store.delete(ids=old["ids"])
    store.add_documents(chunks, ids=ids)

    stored_ids = store.get(include=[])["ids"]
    sample = store.get(ids=[ids[0]], include=["embeddings"])["embeddings"]
    embedding_dimension = len(sample[0]) if sample is not None and len(sample) else None
    index_version = hashlib.sha256(
        "\n".join(sorted(stored_ids)).encode("utf-8")
    ).hexdigest()[:16]

    manifest: dict[str, object] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "embedding_model": setting("EMBEDDING_MODEL", "Nomic-Embed-Text-v2-MoE"),
        "embedding_dimension": embedding_dimension,
        "collection": store._collection.name,
        "index_version": index_version,
        "record_count": store._collection.count(),
        "ingested_chunk_count": len(chunks),
        "chunking": {
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
        },
        "parsers": {"pypdf": importlib.metadata.version("pypdf"), "text": "utf-8"},
        "sources": [
            {
                "name": source.name,
                "path": str(source),
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }
            for source in sources
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        nargs="+",
        type=Path,
        required=True,
        help="One or more PDF/TXT files or directories",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--chunk-size", type=int, default=800)
    parser.add_argument("--chunk-overlap", type=int, default=120)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = ingest(
        args.source,
        args.db,
        args.manifest,
        args.rebuild,
        args.chunk_size,
        args.chunk_overlap,
    )
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
