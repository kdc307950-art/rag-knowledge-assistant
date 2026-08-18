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
    assert document_visible_to_user({"visibility": "all"}, hr)
    assert document_visible_to_user({"visibility": "department", "department": "hr"}, hr)
    assert not document_visible_to_user({"visibility": "department", "department": "hr"}, finance)
    assert document_visible_to_user({"visibility": "private", "owner_id": "hr1"}, hr)
    assert not document_visible_to_user({"visibility": "private", "owner_id": "hr1"}, finance)
    assert document_visible_to_user({"visibility": "private", "owner_id": "hr1"}, admin)
    assert not document_visible_to_user({"visibility": "all"}, None)
