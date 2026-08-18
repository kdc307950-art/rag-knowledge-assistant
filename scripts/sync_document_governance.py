"""Synchronize document governance metadata into Chroma and the manifest.

The command never infers authority from filenames. Use ``--apply`` only after
reviewing ``config/document_governance.json``; without it the command is a
read-only preview.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from enterprise_rag.storage import vector_store  # noqa: E402
from enterprise_rag.storage.document_governance import (  # noqa: E402
    load_governance,
    metadata_for_source,
)
from enterprise_rag.storage.kb_manifest import get_manifest  # noqa: E402


def sync(*, apply: bool = False) -> dict:
    governance = load_governance()
    collection = vector_store.get_kb_collection()
    manifest = get_manifest()
    snapshot = manifest.snapshot()
    rows = []

    for source in sorted((governance.get("documents") or {})):
        revision = snapshot.active_revisions.get(source)
        if not revision:
            rows.append({"source": source, "status": "missing_from_manifest"})
            continue
        physical = collection.get(
            where={"revision_id": revision},
            include=["metadatas"],
        )
        ids = list(physical.get("ids") or [])
        metadatas = [dict(item or {}) for item in physical.get("metadatas") or []]
        metadata = metadata_for_source(source)
        updated = []
        for item in metadatas:
            item.update(metadata)
            updated.append(item)
        if apply and ids:
            collection.update(ids=ids, metadatas=updated)
            manifest.set_source_metadata(source, metadata)
        rows.append(
            {
                "source": source,
                "revision_id": revision,
                "chunks": len(ids),
                "status": "applied" if apply else "preview",
                "metadata": metadata,
            }
        )
    return {
        "default_retrieval_policy": governance.get("default_retrieval_policy"),
        "apply": apply,
        "documents": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="写入 Chroma metadata 和 SQLite manifest；默认只预览",
    )
    args = parser.parse_args()
    print(json.dumps(sync(apply=args.apply), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

