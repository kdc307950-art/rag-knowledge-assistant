"""Review-gated document governance workflow.

The workflow is deliberately storage-agnostic. It records an auditable review
chain and produces a candidate governance payload, but it never edits the
production governance file or changes Chroma/manifest state by itself.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


WORKFLOW_SCHEMA_VERSION = 1
WORKFLOW_STAGES = ("draft", "pending_hr", "pending_legal", "approved")


class GovernanceWorkflowError(ValueError):
    """Invalid transition or incomplete approval evidence."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise GovernanceWorkflowError(f"{field} 不能为空")
    return text


def create_application(
    *,
    application_id: str,
    document_family: str,
    authoritative_source: str,
    superseded_sources: list[str],
    version: str,
    effective_from: str,
    scope: dict[str, list[str]],
    conflict_priority: str,
) -> dict[str, Any]:
    """Create a pending HR review without making an authority decision."""
    if not superseded_sources:
        raise GovernanceWorkflowError("模拟替代审批必须明确 superseded_sources")
    if authoritative_source in superseded_sources:
        raise GovernanceWorkflowError("权威来源不能同时列入 superseded_sources")
    if not isinstance(scope, dict) or not all(
        isinstance(scope.get(field), list) and any(str(item).strip() for item in scope[field])
        for field in ("legal_entities", "regions", "employee_types")
    ):
        raise GovernanceWorkflowError("scope 必须包含 legal_entities/regions/employee_types")
    return {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "mode": "simulation",
        "application_id": _require_text(application_id, "application_id"),
        "document_family": _require_text(document_family, "document_family"),
        "authoritative_source": _require_text(authoritative_source, "authoritative_source"),
        "superseded_sources": [
            _require_text(source, "superseded_source") for source in superseded_sources
        ],
        "version": _require_text(version, "version"),
        "effective_from": _require_text(effective_from, "effective_from"),
        "scope": deepcopy(scope),
        "conflict_priority": _require_text(conflict_priority, "conflict_priority"),
        "stage": "pending_hr",
        "created_at": _now(),
        "approvals": [],
    }


def approve(application: dict[str, Any], *, role: str, actor: str, evidence_refs: list[str]) -> dict[str, Any]:
    """Advance one approval stage; HR and Legal must be separate actors."""
    role = _require_text(role, "role").lower()
    actor = _require_text(actor, "actor")
    refs = [_require_text(ref, "evidence_ref") for ref in evidence_refs]
    if role not in {"hr", "legal"}:
        raise GovernanceWorkflowError("role 只能是 hr 或 legal")
    if not refs:
        raise GovernanceWorkflowError("审批必须提供 evidence_refs")
    expected_role = {"pending_hr": "hr", "pending_legal": "legal"}.get(application.get("stage"))
    if expected_role != role:
        raise GovernanceWorkflowError(
            f"当前阶段 {application.get('stage')} 不接受 {role} 审批"
        )
    if any(item.get("actor") == actor for item in application.get("approvals", [])):
        raise GovernanceWorkflowError("HR 与法务审批人必须是不同主体")
    approval = {
        "role": role,
        "actor": actor,
        "evidence_refs": refs,
        "approved_at": _now(),
    }
    application["approvals"].append(approval)
    application["stage"] = "pending_legal" if role == "hr" else "approved"
    return application


def build_candidate_config(base_config: dict[str, Any], application: dict[str, Any]) -> dict[str, Any]:
    """Build a candidate production config only after both approvals exist."""
    if application.get("stage") != "approved":
        raise GovernanceWorkflowError("审批未完成，不能生成候选治理配置")
    approvals = application.get("approvals") or []
    if {item.get("role") for item in approvals} != {"hr", "legal"}:
        raise GovernanceWorkflowError("候选配置必须包含 HR 与法务审批")
    payload = deepcopy(base_config)
    documents = payload.setdefault("documents", {})
    target = application["authoritative_source"]
    replacements = set(application["superseded_sources"])
    if target not in documents:
        raise GovernanceWorkflowError(f"权威来源不在基础配置中: {target}")
    if not replacements.issubset(documents):
        missing = sorted(replacements.difference(documents))
        raise GovernanceWorkflowError(f"superseded 来源不在基础配置中: {missing}")
    target_entry = dict(documents[target])
    target_entry.update(
        {
            "document_family": application["document_family"],
            "version": application["version"],
            "effective_from": application["effective_from"],
            "authority_level": "authoritative",
            "retrieval_status": "active",
            "supersedes": sorted(replacements),
            "control": {
                "issuing_department": "SIMULATION-HR",
                "approver": ", ".join(item["actor"] for item in approvals),
                "approval_reference": f"SIM-{application['application_id']}-APPROVAL",
                "notice_reference": f"SIM-{application['application_id']}-NOTICE",
                "replacement_decision": "replaces",
                "conflict_priority": application["conflict_priority"],
                "evidence_refs": sorted(
                    ref for item in approvals for ref in item["evidence_refs"]
                ),
                "scope": deepcopy(application["scope"]),
            },
        }
    )
    documents[target] = target_entry
    for source in replacements:
        entry = dict(documents[source])
        entry.update({"authority_level": "superseded", "retrieval_status": "archived"})
        documents[source] = entry
    payload["default_retrieval_policy"] = "authoritative"
    payload.setdefault("notes", []).append(
        "simulation_only: generated by document_governance_workflow; not production evidence"
    )
    return payload


def write_simulation_artifacts(
    workdir: Path | str,
    application: dict[str, Any],
    candidate_config: dict[str, Any],
) -> dict[str, str]:
    """Write an append-only approval event chain and candidate config."""
    directory = Path(workdir)
    directory.mkdir(parents=True, exist_ok=True)
    events_path = directory / "approval_events.jsonl"
    previous_hash = "0" * 64
    lines: list[str] = []
    for sequence, approval in enumerate(application.get("approvals", []), 1):
        event = {"sequence": sequence, "event": "approval_recorded", **approval}
        event["previous_hash"] = previous_hash
        event_hash = hashlib.sha256(
            (previous_hash + _canonical(event)).encode("utf-8")
        ).hexdigest()
        event["event_hash"] = event_hash
        previous_hash = event_hash
        lines.append(_canonical(event))
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    application_path = directory / "application.json"
    application_path.write_text(_canonical(application) + "\n", encoding="utf-8")
    candidate_path = directory / "document_governance.candidate.json"
    candidate_path.write_text(_canonical(candidate_config) + "\n", encoding="utf-8")
    return {
        "application": str(application_path),
        "approval_events": str(events_path),
        "candidate_config": str(candidate_path),
    }
