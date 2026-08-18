"""Tests for the isolated HR/legal governance approval simulation."""

from __future__ import annotations

import json

import pytest


def _base_config():
    return {
        "schema_version": 1,
        "default_retrieval_policy": "unresolved",
        "documents": {
            "old.md": {
                "document_family": "handbook",
                "version": "2024",
                "effective_from": "2024-01-01",
                "authority_level": "unconfirmed",
                "retrieval_status": "active",
            },
            "current.md": {
                "document_family": "handbook",
                "version": "unconfirmed",
                "effective_from": "",
                "authority_level": "unconfirmed",
                "retrieval_status": "active",
            },
        },
    }


def test_workflow_requires_hr_before_legal_and_two_distinct_actors():
    from enterprise_rag.storage.document_governance_workflow import (
        GovernanceWorkflowError,
        approve,
        create_application,
    )

    application = create_application(
        application_id="A-1",
        document_family="handbook",
        authoritative_source="current.md",
        superseded_sources=["old.md"],
        version="2025",
        effective_from="2025-01-01",
        scope={"legal_entities": ["all"], "regions": ["all"], "employee_types": ["all"]},
        conflict_priority="current approved policy",
    )
    with pytest.raises(GovernanceWorkflowError):
        approve(application, role="legal", actor="legal", evidence_refs=["legal.pdf"])
    approve(application, role="hr", actor="same", evidence_refs=["hr.pdf"])
    with pytest.raises(GovernanceWorkflowError):
        approve(application, role="legal", actor="same", evidence_refs=["legal.pdf"])


def test_workflow_builds_and_validates_candidate_config(tmp_path):
    from enterprise_rag.storage.document_governance import load_governance
    from enterprise_rag.storage.document_governance_workflow import (
        approve,
        build_candidate_config,
        create_application,
        write_simulation_artifacts,
    )

    application = create_application(
        application_id="A-2",
        document_family="handbook",
        authoritative_source="current.md",
        superseded_sources=["old.md"],
        version="2025",
        effective_from="2025-01-01",
        scope={"legal_entities": ["all"], "regions": ["all"], "employee_types": ["all"]},
        conflict_priority="current approved policy",
    )
    approve(application, role="hr", actor="hr-owner", evidence_refs=["hr.pdf"])
    approve(application, role="legal", actor="legal-reviewer", evidence_refs=["legal.pdf"])
    candidate = build_candidate_config(_base_config(), application)
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
    loaded = load_governance(candidate_path)
    assert loaded["default_retrieval_policy"] == "authoritative"
    assert loaded["documents"]["current.md"]["authority_level"] == "authoritative"
    assert loaded["documents"]["old.md"]["retrieval_status"] == "archived"
    paths = write_simulation_artifacts(tmp_path / "artifacts", application, candidate)
    events = (tmp_path / "artifacts" / "approval_events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(events) == 2
    assert all(json.loads(line)["event_hash"] for line in events)
    assert paths["candidate_config"].endswith("document_governance.candidate.json")
