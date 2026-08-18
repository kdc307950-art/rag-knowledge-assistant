"""Inspect real Chroma metadata for manual golden-set annotation.

This tool is intentionally read-only. It never writes a golden set and never
guesses expected evidence. Use the output to manually review source/chapter/
parent_id/paragraph anchors against the original document.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

import chromadb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from enterprise_rag.config import KB_DATA_DIR  # noqa: E402


def _matches(metadata: dict[str, Any], source: str | None, contains: str | None) -> bool:
    if source and str(metadata.get("source") or "") != source:
        return False
    if contains:
        haystack = "\n".join(
            str(metadata.get(field) or "")
            for field in ("chapter", "parent_text", "paragraph")
        )
        if contains not in haystack:
            return False
    return True


def inspect(
    *,
    data_dir: Path = KB_DATA_DIR,
    collection_name: str = "kb_text",
    source: str | None = None,
    contains: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    client = chromadb.PersistentClient(path=str(data_dir))
    collection = client.get_collection(collection_name)
    result = collection.get(include=["metadatas", "documents"])
    rows: list[dict[str, Any]] = []
    seen_parent_keys: set[tuple[str, str, str]] = set()
    for metadata, document in zip(
        result.get("metadatas") or [], result.get("documents") or []
    ):
        item = dict(metadata or {})
        if not _matches(item, source, contains):
            continue
        parent_key = (
            str(item.get("source") or ""),
            str(item.get("revision_id") or ""),
            str(item.get("parent_id") or item.get("chunk_index") or document or ""),
        )
        if parent_key in seen_parent_keys:
            continue
        seen_parent_keys.add(parent_key)
        rows.append(
            {
                "source": item.get("source"),
                "chapter": item.get("chapter"),
                "parent_id": item.get("parent_id"),
                "paragraph": item.get("paragraph"),
                "page": item.get("page"),
                "revision_id": item.get("revision_id"),
                "ingest_state": item.get("ingest_state"),
                "excerpt": str(item.get("parent_text") or document or "")[:240],
            }
        )
        if len(rows) >= max(1, limit):
            break
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=KB_DATA_DIR)
    parser.add_argument("--collection", default="kb_text")
    parser.add_argument("--source")
    parser.add_argument("--contains", help="chapter/paragraph/text substring")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    rows = inspect(
        data_dir=args.data_dir,
        collection_name=args.collection,
        source=args.source,
        contains=args.contains,
        limit=args.limit,
    )
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
