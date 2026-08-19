from enterprise_rag.storage.acl import ACLMetadataError, document_visible_to_user, normalize_acl_metadata


def test_acl_metadata_defaults_and_owner_injection():
    assert normalize_acl_metadata({}, owner_id="u1") == {
        "classification": "other",
        "department": "general",
        "visibility": "all",
        "owner_id": "u1",
    }


def test_private_requires_server_owner():
    try:
        normalize_acl_metadata({"visibility": "private"})
    except ACLMetadataError:
        pass
    else:
        raise AssertionError("private documents must have a server-side owner")


def test_visibility_matrix():
    hr = {"id": "hr1", "department": "hr", "roles": ["viewer"]}
    finance = {"id": "f1", "department": "finance", "roles": ["viewer"]}
    admin = {"id": "a1", "department": "other", "roles": ["admin"]}
    assert document_visible_to_user({"visibility": "all", "department": "general"}, hr)
    assert document_visible_to_user({"visibility": "department", "department": "hr", "owner_id": "hr-admin"}, hr)
    assert not document_visible_to_user({"visibility": "department", "department": "hr", "owner_id": "hr-admin"}, finance)
    assert document_visible_to_user({"visibility": "private", "department": "hr", "owner_id": "hr1"}, hr)
    assert not document_visible_to_user({"visibility": "private", "department": "hr", "owner_id": "hr1"}, finance)
    assert document_visible_to_user({"visibility": "private", "department": "hr", "owner_id": "hr1"}, admin)
    assert not document_visible_to_user({"visibility": "all"}, None)


def test_incomplete_legacy_acl_fails_closed_for_non_admin():
    viewer = {"id": "u1", "department": "hr", "roles": ["viewer"]}
    admin = {"id": "a1", "department": "general", "roles": ["admin"]}
    assert not document_visible_to_user({"source": "legacy.md"}, viewer)
    assert document_visible_to_user({"source": "legacy.md"}, admin)


def test_editor_document_boundaries():
    from enterprise_rag.storage.acl import can_create_document, can_delete_document, can_clear_documents

    editor = {"id": "e1", "department": "hr", "roles": ["editor"]}
    other = {"id": "e2", "department": "it", "roles": ["editor"]}
    assert not can_create_document(editor, {"visibility": "all", "department": "hr"})
    assert can_create_document(editor, {"visibility": "department", "department": "hr"})
    assert not can_create_document(editor, {"visibility": "private", "department": "it", "owner_id": "e1"})
    assert not can_delete_document(editor, {"visibility": "private", "department": "hr", "owner_id": "e2"})
    assert not can_clear_documents(editor)
    assert not can_create_document(other, {"visibility": "department", "department": "hr"})


def test_editor_cannot_replace_legacy_source_without_acl_metadata():
    from enterprise_rag.storage.acl import can_replace_document

    editor = {"id": "e1", "department": "hr", "roles": ["editor"]}
    assert not can_replace_document(editor, {})


def test_editor_cannot_replace_legacy_source_via_upload(monkeypatch):
    import asyncio
    from io import BytesIO
    from types import SimpleNamespace

    import pytest
    from fastapi import HTTPException

    from backend.api import upload as upload_api
    from enterprise_rag.services import document_service
    from enterprise_rag.storage import vector_store

    class Upload:
        filename = "legacy.md"
        size = 7
        file = BytesIO(b"content")

        async def seek(self, offset: int) -> None:
            self.file.seek(offset)

    called = []
    monkeypatch.setattr(vector_store, "get_source_metadata", lambda _source: {})
    monkeypatch.setattr(
        document_service.DocumentService,
        "process_uploads",
        lambda *_args, **_kwargs: called.append(True),
    )
    request = SimpleNamespace(
        state=SimpleNamespace(
            current_user={"id": "editor", "department": "hr", "roles": ["editor"]}
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            upload_api.upload(
                request,
                [Upload()],
                classification="policy",
                department="hr",
                visibility="department",
            )
        )

    assert exc_info.value.status_code == 403
    assert called == []


def test_source_metadata_distinguishes_missing_source_from_legacy_acl(monkeypatch, tmp_path):
    from enterprise_rag.storage import vector_store
    from enterprise_rag.storage.kb_manifest import KnowledgeBaseManifest

    manifest = KnowledgeBaseManifest(tmp_path / "manifest.sqlite3")
    manifest.commit_source("legacy.md", "rev-1", "hash-1", 1)
    monkeypatch.setattr(vector_store, "get_manifest", lambda: manifest)

    assert vector_store.get_source_metadata("missing.md") is None
    assert vector_store.get_source_metadata("legacy.md") == {}


def test_source_metadata_read_failure_is_not_treated_as_missing_source(monkeypatch):
    import pytest

    from enterprise_rag.storage import vector_store

    def unavailable_manifest():
        raise RuntimeError("manifest unavailable")

    monkeypatch.setattr(vector_store, "get_manifest", unavailable_manifest)

    with pytest.raises(vector_store.SourceMetadataReadError):
        vector_store.get_source_metadata("existing.md")


def test_upload_fails_closed_when_source_metadata_is_unavailable(monkeypatch):
    """A manifest outage cannot turn a protected replacement into a new upload."""
    import asyncio
    from io import BytesIO
    from types import SimpleNamespace

    import pytest
    from fastapi import HTTPException

    from backend.api import upload as upload_api
    from enterprise_rag.storage import vector_store
    from enterprise_rag.storage.vector_store import SourceMetadataReadError

    class Upload:
        filename = "protected.md"
        size = 7
        file = BytesIO(b"content")

        async def seek(self, offset: int) -> None:
            self.file.seek(offset)

    monkeypatch.setattr(
        vector_store,
        "get_source_metadata",
        lambda _source: (_ for _ in ()).throw(SourceMetadataReadError("unavailable")),
    )
    request = SimpleNamespace(
        state=SimpleNamespace(
            current_user={"id": "admin", "department": "general", "roles": ["admin"]}
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            upload_api.upload(
                request,
                [Upload()],
                classification="policy",
                department="general",
                visibility="all",
            )
        )

    assert exc_info.value.status_code == 503


def test_delete_fails_closed_when_source_metadata_is_unavailable(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import pytest
    from fastapi import HTTPException

    from backend.api import kb as kb_api
    from enterprise_rag.storage import vector_store

    class Manifest:
        def get_source(self, _source):
            return {"active_revision": "rev-1"}

        def get_source_metadata(self, _source):
            raise RuntimeError("manifest unavailable")

    monkeypatch.setattr(vector_store, "get_manifest", lambda: Manifest())
    monkeypatch.setattr(vector_store, "get_kb_collection", lambda: object())
    monkeypatch.setattr(
        vector_store,
        "_ensure_manifest_initialized",
        lambda _collection: None,
    )
    request = SimpleNamespace(
        state=SimpleNamespace(
            current_user={"id": "admin", "department": "general", "roles": ["admin"]}
        )
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(kb_api.delete_document("protected.md", request))

    assert exc_info.value.status_code == 503


def test_worker_rechecks_acl_inside_vector_write_lock(monkeypatch):
    """A source changing after HTTP preflight still cannot be overwritten."""
    import pytest

    from enterprise_rag.core.exceptions import DocumentAuthorizationError
    from enterprise_rag.storage import vector_store
    from enterprise_rag.storage.kb_manifest import ManifestSnapshot

    class Manifest:
        def get_source(self, _source):
            return {"active_revision": "rev-old", "content_hash": "old", "chunk_count": 1}

        def get_source_metadata(self, _source):
            return {
                "visibility": "department",
                "department": "hr",
                "owner_id": "other-editor",
            }

    monkeypatch.setattr(vector_store, "get_kb_collection", lambda: object())
    monkeypatch.setattr(
        vector_store,
        "get_manifest",
        lambda: Manifest(),
    )
    monkeypatch.setattr(
        vector_store,
        "_ensure_manifest_initialized",
        lambda _collection: ManifestSnapshot(1, {"legacy.md": "rev-old"}, {"legacy.md": 1}, True),
    )
    monkeypatch.setattr(vector_store, "metadata_for_source", lambda _source: {})

    metadata = {
        "classification": "policy",
        "department": "hr",
        "visibility": "department",
        "principal_id": "editor",
        "principal_roles": "editor",
        "principal_department": "hr",
    }
    with pytest.raises(DocumentAuthorizationError):
        vector_store.add_document_to_kb(
            "legacy.md",
            "new content",
            refresh_indexes=False,
            metadata=metadata,
        )
