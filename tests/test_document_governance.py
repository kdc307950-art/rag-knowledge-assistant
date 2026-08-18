"""Document governance metadata must remain explicit and storage-safe."""

from __future__ import annotations

import pytest


def test_manifest_persists_and_removes_source_metadata(tmp_path):
    from enterprise_rag.storage.kb_manifest import KnowledgeBaseManifest

    manifest = KnowledgeBaseManifest(tmp_path / "manifest.sqlite3")
    metadata = {
        "document_family": "employee_handbook",
        "version": "2025",
        "effective_from": "",
        "effective_to": "",
        "authority_level": "unconfirmed",
        "retrieval_status": "active",
    }
    manifest.commit_source("book.md", "rev-1", "hash-1", 2, metadata=metadata)

    assert manifest.get_source_metadata("book.md") == metadata
    assert manifest.snapshot().generation == 1
    assert manifest.set_source_metadata("book.md", metadata) is False
    assert manifest.snapshot().generation == 1
    changed = {**metadata, "authority_level": "authoritative"}
    assert manifest.set_source_metadata("book.md", changed) is True
    assert manifest.snapshot().generation == 2
    assert manifest.delete_source("book.md") is True
    assert manifest.get_source_metadata("book.md") == {}


def test_governance_metadata_never_contains_none_for_chroma():
    from enterprise_rag.storage.document_governance import metadata_for_source

    metadata = metadata_for_source("2025版新劳动合同法下的企业员工手册.md")
    assert metadata["effective_from"] == ""
    assert metadata["effective_to"] == ""
    assert all(value is not None for value in metadata.values())
    assert metadata["authority_level"] == "unconfirmed"


def test_governance_filter_excludes_archived_and_fails_closed_when_unconfirmed(monkeypatch):
    from enterprise_rag.core.exceptions import DocumentGovernanceError
    from enterprise_rag.storage import vector_store
    from enterprise_rag.storage.kb_manifest import ManifestSnapshot

    snapshot = ManifestSnapshot(
        generation=3,
        active_revisions={"current.md": "rev-current", "old.md": "rev-old"},
        chunk_counts={"current.md": 2, "old.md": 2},
        initialized=True,
    )

    class Manifest:
        def get_source_metadata(self, source):
            return {
                "current.md": {
                    "authority_level": "authoritative",
                    "retrieval_status": "active",
                },
                "old.md": {
                    "authority_level": "superseded",
                    "retrieval_status": "archived",
                },
            }[source]

    monkeypatch.setattr(vector_store, "get_manifest", lambda: Manifest())
    assert vector_store._active_revision_filter(snapshot, policy="authoritative") == {
        "revision_id": {"$in": ["rev-current"]}
    }
    assert vector_store._active_revision_filter(snapshot, policy="all_active") == {
        "revision_id": {"$in": ["rev-current"]}
    }

    class UnconfirmedManifest:
        def get_source_metadata(self, _source):
            return {"authority_level": "unconfirmed", "retrieval_status": "active"}

    monkeypatch.setattr(vector_store, "get_manifest", lambda: UnconfirmedManifest())
    with pytest.raises(DocumentGovernanceError):
        vector_store._active_revision_filter(snapshot, policy="unresolved")


def test_unresolved_policy_never_silently_uses_a_partial_authoritative_subset(monkeypatch):
    from enterprise_rag.core.exceptions import DocumentGovernanceError
    from enterprise_rag.storage import vector_store
    from enterprise_rag.storage.kb_manifest import ManifestSnapshot

    snapshot = ManifestSnapshot(
        generation=3,
        active_revisions={"current.md": "rev-current", "unknown.md": "rev-unknown"},
        chunk_counts={"current.md": 2, "unknown.md": 2},
        initialized=True,
    )

    class Manifest:
        def get_source_metadata(self, source):
            return {
                "current.md": {"authority_level": "authoritative", "retrieval_status": "active"},
                "unknown.md": {"authority_level": "unconfirmed", "retrieval_status": "active"},
            }[source]

    monkeypatch.setattr(vector_store, "get_manifest", lambda: Manifest())
    with pytest.raises(DocumentGovernanceError):
        vector_store._active_revision_filter(snapshot, policy="unresolved")


def test_authoritative_state_requires_auditable_control_package(tmp_path):
    from enterprise_rag.storage.document_governance import load_governance

    path = tmp_path / "governance.json"
    path.write_text(
        '{"schema_version":1,"default_retrieval_policy":"authoritative","documents":'
        '{"current.md":{"document_family":"handbook","version":"2025",'
        '"effective_from":"2025-01-01","authority_level":"authoritative",'
        '"retrieval_status":"active"}}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="control"):
        load_governance(path)


def test_authoritative_state_accepts_complete_evidence_and_single_family_owner(tmp_path):
    from enterprise_rag.storage.document_governance import load_governance

    path = tmp_path / "governance.json"
    path.write_text(
        '''{
          "schema_version": 1,
          "default_retrieval_policy": "authoritative",
          "documents": {
            "old.md": {
              "document_family": "handbook", "version": "2024",
              "effective_from": "2024-01-01", "authority_level": "superseded",
              "retrieval_status": "archived"
            },
            "current.md": {
              "document_family": "handbook", "version": "2025",
              "effective_from": "2025-01-01", "authority_level": "authoritative",
              "retrieval_status": "active", "supersedes": ["old.md"],
              "control": {
                "issuing_department": "HR", "approver": "HR Director",
                "approval_reference": "HR-2025-001", "notice_reference": "NOTICE-2025-01",
                "replacement_decision": "replaces", "conflict_priority": "当前生效制度优先",
                "evidence_refs": ["records/HR-2025-001.pdf"],
                "scope": {"legal_entities": ["all"], "regions": ["all"], "employee_types": ["all"]}
              }
            }
          }
        }''',
        encoding="utf-8",
    )
    assert load_governance(path)["documents"]["current.md"]["version"] == "2025"


def test_rag_service_returns_governance_message_instead_of_generic_retrieval_error(monkeypatch):
    from enterprise_rag.core.exceptions import DocumentGovernanceError
    from enterprise_rag.services import rag_service

    monkeypatch.setattr(
        rag_service,
        "retrieve_context",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(DocumentGovernanceError("unconfirmed")),
    )
    service = rag_service.RagService()

    content = "".join(service.answer_stream("年休假多少天", "", []))

    assert "未确认生效关系" in content
    assert service._last_meta["error_code"] == "document_governance_unresolved"
