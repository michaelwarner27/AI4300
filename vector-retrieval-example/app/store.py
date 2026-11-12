"""Construct the shared embedding client and persistent Chroma collection."""

from __future__ import annotations

from pathlib import Path

from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

from app.config import COLLECTION_NAME, require_api_key, setting


def build_embeddings() -> OpenAIEmbeddings:
    """Use the course OpenAI-compatible embedding service."""
    return OpenAIEmbeddings(
        model=setting("EMBEDDING_MODEL", "Nomic-Embed-Text-v2-MoE"),
        base_url=setting(
            "EMBEDDING_BASE_URL", "http://golem.cs.utahtech.edu:4000/v1"
        ),
        api_key=require_api_key("EMBEDDING_API_KEY"),
        check_embedding_ctx_length=False,
    )


def open_store(path: Path) -> Chroma:
    """Open or create the local persistent collection."""
    return Chroma(
        collection_name=COLLECTION_NAME,
        persist_directory=str(path),
        embedding_function=build_embeddings(),
    )
