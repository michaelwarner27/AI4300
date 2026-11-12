"""Inspect the ingestion manifest or stored chunk records."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import DEFAULT_DB_PATH, DEFAULT_MANIFEST_PATH
from app.store import open_store


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", action="store_true", help="Print manifest JSON")
    parser.add_argument("--records", action="store_true", help="Print stored chunk records")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--manifest-path", type=Path, default=DEFAULT_MANIFEST_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.manifest and not args.records:
        raise SystemExit("Choose --manifest, --records, or both")
    if args.manifest:
        if not args.manifest_path.exists():
            raise SystemExit("No manifest found. Run app.ingest first.")
        print(args.manifest_path.read_text(encoding="utf-8").rstrip())
    if args.records:
        records = open_store(args.db).get(include=["documents", "metadatas"])
        rows = [
            {"id": record_id, "metadata": metadata, "text": document}
            for record_id, metadata, document in zip(
                records["ids"], records["metadatas"], records["documents"], strict=True
            )
        ]
        print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
