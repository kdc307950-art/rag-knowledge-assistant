"""Document governance metadata must remain explicit and storage-safe."""

from __future__ import annotations


def test_manifest_persists_and_removes_source_metadata(tmp_path):
    from enterprise_rag.storage.kb_manifest import KnowledgeBaseManifest

    manifest = KnowledgeBaseManifest(tmp_path / "manifest.sqlite3")
    manifest.commit_source("book.md", "rev-1", "hash-1", 2)
    metadata = {
        "document_family": "employee_handbook",
        "version": "2025",
        "effective_from": "",
        "effective_to": "",
        "authority_level": "unconfirmed",
        "retrieval_status": "active",
    }
    manifest.set_source_metadata("book.md", metadata)

    assert manifest.get_source_metadata("book.md") == metadata
    assert manifest.delete_source("book.md") is True
    assert manifest.get_source_metadata("book.md") == {}


def test_governance_metadata_never_contains_none_for_chroma():
    from enterprise_rag.storage.document_governance import metadata_for_source

    metadata = metadata_for_source("2025版新劳动合同法下的企业员工手册.md")
    assert metadata["effective_from"] == ""
    assert metadata["effective_to"] == ""
    assert all(value is not None for value in metadata.values())
    assert metadata["authority_level"] == "unconfirmed"
