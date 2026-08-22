"""Release-gate ACL matrix for document visibility and management roles."""

from __future__ import annotations


def _users():
    return {
        "hr.viewer": {"id": "hr.viewer", "department": "hr", "roles": ["viewer"]},
        "it.viewer": {"id": "it.viewer", "department": "it", "roles": ["viewer"]},
        "admin": {"id": "admin", "department": "general", "roles": ["admin"]},
        "editor": {"id": "editor", "department": "hr", "roles": ["editor"]},
    }


def test_acl_visibility_matrix_matches_release_contract():
    from enterprise_rag.storage.acl import document_visible_to_user

    users = _users()
    documents = {
        "all.md": {"visibility": "all", "department": "general"},
        "hr.md": {"visibility": "department", "department": "hr", "owner_id": "hr.editor"},
        "private.md": {"visibility": "private", "department": "hr", "owner_id": "hr.viewer"},
    }

    assert [name for name, meta in documents.items() if document_visible_to_user(meta, users["hr.viewer"])] == [
        "all.md", "hr.md", "private.md"
    ]
    assert [name for name, meta in documents.items() if document_visible_to_user(meta, users["it.viewer"])] == [
        "all.md"
    ]
    assert [name for name, meta in documents.items() if document_visible_to_user(meta, users["admin"])] == list(documents)
    assert [name for name, meta in documents.items() if document_visible_to_user(meta, users["editor"])] == [
        "all.md", "hr.md"
    ]


def test_manifest_revision_filter_enforces_acl_before_retrieval(monkeypatch):
    from enterprise_rag.storage import vector_store
    from enterprise_rag.storage.kb_manifest import ManifestSnapshot

    snapshot = ManifestSnapshot(
        generation=4,
        active_revisions={"all.md": "rev-all", "hr.md": "rev-hr", "private.md": "rev-private"},
        chunk_counts={"all.md": 1, "hr.md": 1, "private.md": 1},
        initialized=True,
    )

    class Manifest:
        metadata = {
            "all.md": {"visibility": "all", "department": "general", "authority_level": "authoritative", "retrieval_status": "active"},
            "hr.md": {"visibility": "department", "department": "hr", "owner_id": "hr.editor", "authority_level": "authoritative", "retrieval_status": "active"},
            "private.md": {"visibility": "private", "department": "hr", "owner_id": "hr.viewer", "authority_level": "authoritative", "retrieval_status": "active"},
        }

        def get_source_metadata(self, source):
            return self.metadata[source]

    monkeypatch.setattr(vector_store, "get_manifest", lambda: Manifest())
    for user, expected in (
        (_users()["hr.viewer"], {"rev-all", "rev-hr", "rev-private"}),
        (_users()["it.viewer"], {"rev-all"}),
        (_users()["admin"], {"rev-all", "rev-hr", "rev-private"}),
    ):
        result = vector_store._active_revision_filter(
            snapshot,
            policy="authoritative",
            access_context=user,
        )
        assert result is not None
        assert set(result["revision_id"]["$in"]) == expected


def test_management_matrix_allows_only_editor_and_admin():
    from enterprise_rag.storage.acl import user_can_manage_documents

    users = _users()
    assert not user_can_manage_documents(users["hr.viewer"])
    assert not user_can_manage_documents(users["it.viewer"])
    assert user_can_manage_documents(users["editor"])
    assert user_can_manage_documents(users["admin"])
