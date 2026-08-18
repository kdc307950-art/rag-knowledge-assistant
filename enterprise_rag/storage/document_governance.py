"""Document-level governance metadata shared by Chroma and the manifest."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from ..config import DOCUMENT_GOVERNANCE_PATH

ALLOWED_AUTHORITY_LEVELS = {
    "authoritative",
    "reference",
    "superseded",
    "unconfirmed",
    "draft",
}
ALLOWED_RETRIEVAL_STATUSES = {"active", "archived"}
ALLOWED_RETRIEVAL_POLICIES = {"unresolved", "authoritative", "all_active"}
REQUIRED_CONTROL_FIELDS = (
    "issuing_department",
    "approver",
    "approval_reference",
    "notice_reference",
    "replacement_decision",
    "conflict_priority",
)
REQUIRED_SCOPE_FIELDS = ("legal_entities", "regions", "employee_types")
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
    policy = payload.get("default_retrieval_policy", "unresolved")
    if policy not in ALLOWED_RETRIEVAL_POLICIES:
        raise ValueError(f"文档治理配置 default_retrieval_policy 无效: {policy}")
    documents = payload.get("documents") or {}
    if not isinstance(documents, dict):
        raise ValueError(f"文档治理配置 documents 必须是对象: {config_path}")
    authoritative_families: dict[str, str] = {}
    active_unconfirmed_sources: list[str] = []
    for source, metadata in documents.items():
        if not isinstance(source, str) or not source.strip() or not isinstance(metadata, dict):
            raise ValueError(f"文档治理配置存在无效来源条目: {source!r}")
        authority = metadata.get("authority_level", "unconfirmed")
        status = metadata.get("retrieval_status", "active")
        if authority not in ALLOWED_AUTHORITY_LEVELS:
            raise ValueError(f"{source} authority_level 无效: {authority}")
        if status not in ALLOWED_RETRIEVAL_STATUSES:
            raise ValueError(f"{source} retrieval_status 无效: {status}")
        family = str(metadata.get("document_family") or "").strip()
        if not family:
            raise ValueError(f"{source} 必须指定 document_family")
        if authority == "superseded" and status != "archived":
            raise ValueError(f"{source} 已替代文档必须设置 retrieval_status=archived")
        if authority == "authoritative" and status != "active":
            raise ValueError(f"{source} 权威文档必须设置 retrieval_status=active")
        if authority == "authoritative":
            _validate_authoritative_control(source, metadata, documents)
            previous = authoritative_families.get(family)
            if previous:
                raise ValueError(
                    f"同一 document_family 只能有一个 active authoritative: {family}"
                    f" ({previous}, {source})"
                )
            authoritative_families[family] = source
        if status == "active" and authority == "unconfirmed":
            active_unconfirmed_sources.append(source)
    if policy == "authoritative" and active_unconfirmed_sources:
        raise ValueError(
            "default_retrieval_policy=authoritative 时不能保留 active/unconfirmed 文档: "
            + ", ".join(sorted(active_unconfirmed_sources))
        )
    return payload


def _validate_authoritative_control(
    source: str,
    metadata: dict[str, Any],
    documents: dict[str, Any],
) -> None:
    """Require auditable release evidence before a document becomes authoritative."""
    version = str(metadata.get("version") or "").strip()
    if not version or version == "unconfirmed":
        raise ValueError(f"{source} 权威文档必须指定正式 version")
    effective_from = str(metadata.get("effective_from") or "").strip()
    try:
        date.fromisoformat(effective_from)
    except ValueError as exc:
        raise ValueError(f"{source} 权威文档必须使用 ISO 生效日期 effective_from") from exc

    control = metadata.get("control")
    if not isinstance(control, dict):
        raise ValueError(f"{source} 权威文档必须提供 control 证据包")
    missing = [
        field for field in REQUIRED_CONTROL_FIELDS if not str(control.get(field) or "").strip()
    ]
    if missing:
        raise ValueError(f"{source} control 缺少: {', '.join(missing)}")
    if control.get("replacement_decision") not in {
        "replaces",
        "does_not_replace",
        "scope_distinct",
    }:
        raise ValueError(
            f"{source} control.replacement_decision 必须是 replaces/does_not_replace/scope_distinct"
        )
    evidence_refs = control.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not any(
        isinstance(value, str) and value.strip() for value in evidence_refs
    ):
        raise ValueError(f"{source} control.evidence_refs 必须包含可追溯证据引用")
    scope = control.get("scope")
    if not isinstance(scope, dict):
        raise ValueError(f"{source} control.scope 必须说明适用范围")
    missing_scope = [
        field
        for field in REQUIRED_SCOPE_FIELDS
        if not (
            isinstance(scope.get(field), list) and any(str(value).strip() for value in scope[field])
        )
    ]
    if missing_scope:
        raise ValueError(f"{source} control.scope 缺少: {', '.join(missing_scope)}")
    supersedes = metadata.get("supersedes", [])
    if not isinstance(supersedes, list) or any(
        not isinstance(value, str) or not value.strip() for value in supersedes
    ):
        raise ValueError(f"{source} supersedes 必须是来源文件名数组")
    if control["replacement_decision"] == "replaces" and not supersedes:
        raise ValueError(f"{source} replacement_decision=replaces 时必须列出 supersedes")
    if control["replacement_decision"] != "replaces" and supersedes:
        raise ValueError(f"{source} 非 replaces 状态不能填写 supersedes")
    family = str(metadata.get("document_family") or "")
    for replaced_source in supersedes:
        replaced = documents.get(replaced_source)
        if not isinstance(replaced, dict):
            raise ValueError(f"{source} supersedes 引用了未知来源: {replaced_source}")
        if str(replaced.get("document_family") or "") != family:
            raise ValueError(f"{source} 只能替代同一 document_family 的文档: {replaced_source}")
        if replaced.get("authority_level") != "superseded" or replaced.get("retrieval_status") != "archived":
            raise ValueError(
                f"{source} 替代关系要求 {replaced_source} 为 superseded/archived"
            )


def retrieval_policy(path: Path | str = DOCUMENT_GOVERNANCE_PATH) -> str:
    return str(load_governance(path).get("default_retrieval_policy", "unresolved"))


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


def source_allowed_for_retrieval(metadata: dict[str, Any], *, policy: str) -> bool:
    """Apply document-control policy without inferring authority from filenames."""
    status = str(metadata.get("retrieval_status") or "active")
    authority = str(metadata.get("authority_level") or "unconfirmed")
    if status != "active" or authority in {"draft", "superseded"}:
        return False
    if policy == "unresolved":
        return False
    if policy == "authoritative":
        return authority == "authoritative"
    if policy == "all_active":
        return authority not in {"draft", "superseded"}
    raise ValueError(f"未知文档检索策略: {policy}")
