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


def test_governance_metadata_never_contains_none_for_chroma(tmp_path):
    """Chroma rejects None metadata; both the unknown and the explicit-null path must coerce.

    Pinned to a fixture rather than to config/document_governance.json so that
    governing a real document can never silently change what this asserts.
    """
    from enterprise_rag.storage.document_governance import metadata_for_source

    path = tmp_path / "governance.json"
    path.write_text(
        '''{
          "schema_version": 1,
          "default_retrieval_policy": "all_active",
          "documents": {
            "governed.md": {
              "document_family": "handbook", "version": "2025",
              "effective_from": "2025-01-01", "effective_to": null,
              "authority_level": "reference", "retrieval_status": "active"
            }
          }
        }''',
        encoding="utf-8",
    )

    # Source absent from the config: every field falls back, nothing is None.
    unknown = metadata_for_source("not-in-config.md", path=path)
    assert unknown["effective_from"] == ""
    assert unknown["effective_to"] == ""
    assert unknown["authority_level"] == "unconfirmed"
    assert unknown["retrieval_status"] == "active"
    assert all(value is not None for value in unknown.values())

    # Source present but with an explicit JSON null: coerced to "", not passed through.
    governed = metadata_for_source("governed.md", path=path)
    assert governed["effective_from"] == "2025-01-01"
    assert governed["effective_to"] == ""
    assert governed["authority_level"] == "reference"
    assert all(value is not None for value in governed.values())


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


def test_authoritative_policy_rejects_active_reference_sources(tmp_path):
    """Switching to ``authoritative`` must not silently drop ``reference`` documents."""
    from enterprise_rag.storage.document_governance import load_governance

    path = tmp_path / "governance.json"
    body = '''{
      "schema_version": 1,
      "default_retrieval_policy": "%s",
      "documents": {
        "handbook-a.md": {
          "document_family": "handbook", "version": "2025-a",
          "effective_from": "2025-01-01", "effective_to": null,
          "authority_level": "reference", "retrieval_status": "active"
        }
      }
    }'''

    path.write_text(body % "authoritative", encoding="utf-8")
    with pytest.raises(ValueError, match="reference"):
        load_governance(path)

    # The same configuration is valid under all_active, which is what it is for.
    path.write_text(body % "all_active", encoding="utf-8")
    assert load_governance(path)["documents"]["handbook-a.md"]["authority_level"] == "reference"


def test_shipped_governance_config_matches_documented_policy():
    """config/document_governance.json is the source of truth the docs describe."""
    from enterprise_rag.config import DOCUMENT_GOVERNANCE_PATH
    from enterprise_rag.storage.document_governance import load_governance

    payload = load_governance(DOCUMENT_GOVERNANCE_PATH)
    assert payload["default_retrieval_policy"] == "all_active"
    documents = payload["documents"]
    assert documents, "生产治理配置不应为空"
    for source, metadata in documents.items():
        assert metadata["authority_level"] == "reference", source
        assert metadata["retrieval_status"] == "active", source
        assert "supersedes" not in metadata, f"{source} 并行有效，不应存在替代关系"


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


def test_retriever_preserves_governance_error_for_service_layer(monkeypatch):
    from enterprise_rag.core.exceptions import DocumentGovernanceError
    from enterprise_rag.rag import retriever

    monkeypatch.setattr(
        retriever,
        "get_search_function",
        lambda: lambda *_args, **_kwargs: (_ for _ in ()).throw(
            DocumentGovernanceError("unconfirmed")
        ),
    )
    with pytest.raises(DocumentGovernanceError):
        retriever.retrieve_context("年休假多少天")
