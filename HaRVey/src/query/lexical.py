"""
Lexical (keyword) side of hybrid retrieval.

LexicalIndex holds two in-memory indexes over every chunk in the Chroma
collection:

- BM25 over the chunk text. The tokenizer keeps part numbers whole (as their
  normalized key, e.g. "45791570X"), so "457915.70X" is one term instead of
  being shredded into "457915" and "70x".
- An exact part-number index: normalized key -> chunk ids, built from the
  "part_numbers" metadata written at ingestion.

The corpus is small (hundreds of chunks), so both are built once per process
from collection.get() and cached; the cache rebuilds when the collection's
chunk count changes (e.g. after re-ingestion).
"""

import re
from typing import Any

from rank_bm25 import BM25Okapi

from src.utils.logging import get_logger
from src.utils.part_numbers import part_keys, split_keys

logger = get_logger()

_WORD = re.compile(r"[a-z0-9]+")

# Very common words that carry no meaning for retrieval. BM25's IDF already
# down-weights them; dropping them keeps short queries focused on real terms.
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for",
    "from", "how", "i", "if", "in", "is", "it", "my", "of", "on", "or", "should",
    "that", "the", "this", "to", "what", "when", "where", "which", "why", "will",
    "with", "you", "your",
}


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens plus whole normalized part-number keys.

    Part keys are added in addition to the word tokens, so a chunk containing
    "457915.70X" matches both a query for "457915.70X" (key "45791570x") and
    one for just "457915".
    """
    words = [w for w in _WORD.findall((text or "").lower()) if w not in _STOPWORDS]
    return words + [k.lower() for k in part_keys(text)]


class LexicalIndex:
    """BM25 + exact part-number lookup over one Chroma collection."""

    def __init__(self, ids: list[str], documents: list[str], metadatas: list[dict[str, Any]]):
        self.ids = ids
        self._pos = {chunk_id: i for i, chunk_id in enumerate(ids)}
        self.bm25 = BM25Okapi([tokenize(doc) for doc in documents]) if ids else None

        self.parts: dict[str, set[str]] = {}
        for chunk_id, meta in zip(ids, metadatas):
            for key in split_keys((meta or {}).get("part_numbers")):
                self.parts.setdefault(key, set()).add(chunk_id)

    @classmethod
    def from_collection(cls, collection: Any) -> "LexicalIndex":
        data = collection.get(include=["documents", "metadatas"])
        return cls(data["ids"], data["documents"] or [], data["metadatas"] or [])

    def bm25_search(self, query: str, k: int, allowed: set[str] | None = None) -> list[tuple[str, float]]:
        """Top-k (chunk_id, bm25_score) with score > 0, best first.

        allowed: restrict to these chunk ids (a metadata filter's matches);
        None means search everything.
        """
        terms = tokenize(query)
        if not self.bm25 or not terms or k <= 0:
            return []
        scores = self.bm25.get_scores(terms)
        ranked = [
            (chunk_id, float(scores[i]))
            for i, chunk_id in enumerate(self.ids)
            if scores[i] > 0 and (allowed is None or chunk_id in allowed)
        ]
        ranked.sort(key=lambda pair: pair[1], reverse=True)
        return ranked[:k]

    def exact_part_hits(self, keys: list[str], allowed: set[str] | None = None) -> dict[str, list[str]]:
        """chunk_id -> the query keys it contains, for every chunk matching
        at least one key."""
        hits: dict[str, list[str]] = {}
        for key in keys:
            for chunk_id in self.parts.get(key, ()):
                if allowed is None or chunk_id in allowed:
                    hits.setdefault(chunk_id, []).append(key)
        return hits


# (persist_directory, collection_name) -> (chunk count when built, index)
_CACHE: dict[tuple[str, str], tuple[int, LexicalIndex]] = {}


def get_lexical_index(collection: Any, cache_key: tuple[str, str]) -> LexicalIndex:
    """The cached index for this collection, rebuilt if its size changed."""
    count = collection.count()
    cached = _CACHE.get(cache_key)
    if cached and cached[0] == count:
        return cached[1]
    index = LexicalIndex.from_collection(collection)
    _CACHE[cache_key] = (count, index)
    logger.info(f"Built lexical index: {count} chunks, {len(index.parts)} part-number keys")
    return index
