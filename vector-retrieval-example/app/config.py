"""Shared configuration for embedding, storage, and chat services."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_DIR / "chroma_db"
DEFAULT_MANIFEST_PATH = PROJECT_DIR / "manifest.json"
COLLECTION_NAME = "lecture_170_documents"

load_dotenv(PROJECT_DIR / ".env")


def setting(name: str, default: str) -> str:
    """Read a setting while keeping classroom endpoint defaults visible."""
    return os.getenv(name, default)


def require_api_key(name: str) -> str:
    """Return an API key and reject the documented placeholder."""
    value = os.getenv(name, "")
    if not value or value == "student-should-use-their-own":
        raise RuntimeError(
            f"Set {name} in .env to the API key assigned to you. "
            "See .env.example."
        )
    return value
