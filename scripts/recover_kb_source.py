"""Reconstruct traceable source text from a Chroma backup.

The runtime backup does not contain the original uploaded files. Chroma still
keeps parent text and source metadata, so this command exports a best-effort
reconstruction for offline evaluation without replacing the live data dir.
The output manifest records that provenance explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import chromadb


def _safe_name(source: str) -> str:
    stem = re.sub(r"[^0-9A-Za-z._-]+", "_", source).strip("._") or "source"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
    return f"{stem[:80]}-{digest}.txt"


def recover(backup_data_dir: Path, output_dir: Path) -> dict[str, Any]:
    client = chromadb.PersistentClient(path=str(backup_data_dir / "kb_data"))
    collections = client.list_collections()
    if not collections:
        raise RuntimeError(f"未找到 Chroma collection: {backup_data_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    sources: list[dict[str, Any]] = []
    for collection in collections:
        result = collection.get(include=["documents", "metadatas"])
        grouped: dict[str, dict[str, dict[str, Any]]] = {}
        for document, metadata in zip(
            result.get("documents") or [], result.get("metadatas") or []
        ):
            meta = dict(metadata or {})
            source = str(meta.get("source") or "unknown")
            parent_id = str(meta.get("parent_id") or meta.get("chunk_index") or document)
            parent_text = str(meta.get("parent_text") or document or "")
            entry = grouped.setdefault(source, {}).get(parent_id)
            if entry is None or int(meta.get("chunk_index") or 0) < entry["chunk_index"]:
                grouped.setdefault(source, {})[parent_id] = {
                    "text": parent_text,
                    "chunk_index": int(meta.get("chunk_index") or 0),
                    "chapter": meta.get("chapter"),
                    "paragraph": meta.get("paragraph"),
                }

        for source, parents in grouped.items():
            ordered = sorted(parents.values(), key=lambda item: item["chunk_index"])
            text = "\n\n".join(item["text"] for item in ordered if item["text"].strip())
            filename = _safe_name(source)
            path = output_dir / filename
            path.write_text(text + ("\n" if text else ""), encoding="utf-8")
            sources.append(
                {
                    "source": source,
                    "collection": collection.name,
                    "output": filename,
                    "parent_count": len(ordered),
                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "provenance": "reconstructed_from_chroma_parent_text",
                }
            )

    manifest = {
        "schema_version": 1,
        "input": str(backup_data_dir.resolve()),
        "source_count": len(sources),
        "sources": sources,
        "warning": "输出不是原始上传文件；内容可能保留入库时的编码损失。",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-data", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = recover(args.backup_data, args.output_dir)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
