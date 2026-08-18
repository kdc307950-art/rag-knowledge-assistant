"""Apply reviewed document ACL metadata to Chroma and the manifest.

The command is preview-only by default. It never changes document authority
or retrieval status; those fields remain controlled by document governance.
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
from enterprise_rag.storage.acl import normalize_acl_metadata  # noqa: E402
from enterprise_rag.storage.document_governance import metadata_for_source  # noqa: E402
from enterprise_rag.storage.kb_manifest import get_manifest  # noqa: E402


CONFIG_PATH = PROJECT_ROOT / "config" / "document_acl.json"


def load_acl_config(path: Path = CONFIG_PATH) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("documents"), dict):
        raise ValueError("document_acl.json 必须使用 schema_version=1 且包含 documents")
    return payload


def sync(*, apply: bool = False, path: Path = CONFIG_PATH) -> dict:
    payload = load_acl_config(path)
    collection = vector_store.get_kb_collection()
    manifest = get_manifest()
    snapshot = manifest.snapshot()
    rows: list[dict] = []
    for source, raw_acl in sorted(payload["documents"].items()):
        revision = snapshot.active_revisions.get(source)
        if not revision:
            rows.append({"source": source, "status": "missing_from_manifest"})
            continue
        acl = normalize_acl_metadata(raw_acl)
        governance = metadata_for_source(source)
        merged_source_metadata = {**governance, **acl}
        physical = collection.get(where={"revision_id": revision}, include=["metadatas"])
        ids = list(physical.get("ids") or [])
        updated = []
        for item in physical.get("metadatas") or []:
            merged = dict(item or {})
            merged.update(merged_source_metadata)
            updated.append(merged)
        if apply and ids:
            collection.update(ids=ids, metadatas=updated)
            manifest.set_source_metadata(source, merged_source_metadata)
        rows.append(
            {
                "source": source,
                "revision_id": revision,
                "chunks": len(ids),
                "status": "applied" if apply else "preview",
                "acl": acl,
            }
        )
    return {"apply": apply, "documents": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="写入 Chroma metadata 和 SQLite manifest")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args()
    print(json.dumps(sync(apply=args.apply, path=args.config), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
