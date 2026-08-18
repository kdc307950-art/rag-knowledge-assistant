"""Document-level governance metadata shared by Chroma and the manifest."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import DOCUMENT_GOVERNANCE_PATH

ALLOWED_AUTHORITY_LEVELS = {"authoritative", "reference", "superseded", "unconfirmed"}
ALLOWED_RETRIEVAL_STATUSES = {"active", "archived"}
METADATA_FIELDS = (
    "document_family",
    "version",
    "effective_from",
    "effective_to",
    "authority_level",
    "retrieval_status",
)


def load_governance(path: Path | str = DOCUMENT_GOVERNANCE_PATH) -> dict[str, Any]:
    """Load and validate governance metadata without inferring authority."""
    config_path = Path(path)
    if not config_path.is_file():
        return {"schema_version": 1, "default_retrieval_policy": "unresolved", "documents": {}}
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError(f"文档治理配置必须使用 schema_version=1: {config_path}")
    documents = payload.get("documents") or {}
    if not isinstance(documents, dict):
        raise ValueError(f"文档治理配置 documents 必须是对象: {config_path}")
    for source, metadata in documents.items():
        if not isinstance(source, str) or not source.strip() or not isinstance(metadata, dict):
            raise ValueError(f"文档治理配置存在无效来源条目: {source!r}")
        authority = metadata.get("authority_level", "unconfirmed")
        status = metadata.get("retrieval_status", "active")
        if authority not in ALLOWED_AUTHORITY_LEVELS:
            raise ValueError(f"{source} authority_level 无效: {authority}")
        if status not in ALLOWED_RETRIEVAL_STATUSES:
            raise ValueError(f"{source} retrieval_status 无效: {status}")
    return payload


def metadata_for_source(source: str, *, path: Path | str = DOCUMENT_GOVERNANCE_PATH) -> dict[str, Any]:
    """Return stable metadata fields; missing authority remains unconfirmed."""
    payload = load_governance(path)
    entry = dict((payload.get("documents") or {}).get(source) or {})
    # Chroma metadata cannot contain None; an empty string means "not supplied"
    # while keeping the field schema stable for later filtering.
    return {
        field: entry.get(field) if entry.get(field) is not None else ""
        for field in METADATA_FIELDS
    } | {
        "authority_level": entry.get("authority_level", "unconfirmed"),
        "retrieval_status": entry.get("retrieval_status", "active"),
    }
