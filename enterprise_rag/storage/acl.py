"""Document metadata validation and pre-retrieval visibility rules."""

from __future__ import annotations

import re
from typing import Any

CLASSIFICATIONS = {"policy", "process", "benefit", "technical", "other"}
VISIBILITIES = {"private", "department", "all"}
_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

class ACLMetadataError(ValueError):
    """Invalid or incomplete ACL metadata."""

def normalize_acl_metadata(metadata: dict[str, Any] | None = None, *, owner_id: str = "") -> dict[str, str]:
    raw = dict(metadata or {})
    classification = str(raw.get("classification") or "other").strip().lower()
    department = str(raw.get("department") or "general").strip().lower()
    visibility = str(raw.get("visibility") or "all").strip().lower()
    if classification not in CLASSIFICATIONS:
        raise ACLMetadataError("classification 必须是 policy/process/benefit/technical/other")
    if visibility not in VISIBILITIES:
        raise ACLMetadataError("visibility 必须是 private/department/all")
    if not _CODE_RE.fullmatch(department):
        raise ACLMetadataError("department 必须是 ASCII 部门代码")
    # owner_id is an authorization fact, never a client-controlled metadata field.
    normalized_owner = str(owner_id or raw.get("owner_id") or "").strip()[:128]
    if visibility == "private" and not normalized_owner:
        raise ACLMetadataError("visibility=private 必须提供上传者身份")
    return {"classification": classification, "department": department, "visibility": visibility, "owner_id": normalized_owner}

def user_is_admin(user: dict[str, Any] | None) -> bool:
    return "admin" in set((user or {}).get("roles") or [])

def user_can_manage_documents(user: dict[str, Any] | None) -> bool:
    return bool(set((user or {}).get("roles") or []) & {"admin", "editor"})


def user_is_editor(user: dict[str, Any] | None) -> bool:
    return "editor" in set((user or {}).get("roles") or [])


def acl_metadata_complete(metadata: dict[str, Any] | None) -> bool:
    value = metadata or {}
    visibility = str(value.get("visibility") or "").strip().lower()
    department = str(value.get("department") or "").strip()
    owner_id = str(value.get("owner_id") or "").strip()
    return (
        visibility in VISIBILITIES
        and bool(department)
        and (visibility != "private" or bool(owner_id))
    )


def can_create_document(user: dict[str, Any] | None, metadata: dict[str, Any]) -> bool:
    if user_is_admin(user):
        return True
    if not user_is_editor(user):
        return False
    visibility = str(metadata.get("visibility") or "").strip().lower()
    department = str(metadata.get("department") or "").strip().lower()
    return visibility in {"private", "department"} and department == str((user or {}).get("department") or "").strip().lower()


def can_replace_document(user: dict[str, Any] | None, existing_metadata: dict[str, Any] | None) -> bool:
    """Authorize replacing an existing source before asynchronous ingestion."""
    return can_update_document(user, existing_metadata or {})


def can_update_document(user: dict[str, Any] | None, metadata: dict[str, Any]) -> bool:
    if user_is_admin(user):
        return True
    return user_is_editor(user) and (
        str(metadata.get("owner_id") or "") == str((user or {}).get("id") or "")
    ) and can_create_document(user, metadata)


def can_delete_document(user: dict[str, Any] | None, metadata: dict[str, Any]) -> bool:
    if user_is_admin(user):
        return True
    return can_update_document(user, metadata)


def can_clear_documents(user: dict[str, Any] | None) -> bool:
    return user_is_admin(user)


def acl_summary(metadata: dict[str, Any] | None) -> dict[str, str]:
    value = metadata or {}
    return {
        "visibility": str(value.get("visibility") or ""),
        "department": str(value.get("department") or ""),
        "owner_id": str(value.get("owner_id") or ""),
    }


def document_visible_to_user(metadata: dict[str, Any], user: dict[str, Any] | None) -> bool:
    if user is None:
        return False
    if user_is_admin(user):
        return True
    # Legacy records without a complete ACL must fail closed for non-admins.
    if not acl_metadata_complete(metadata):
        return False
    visibility = str(metadata.get("visibility") or "").strip().lower()
    if visibility == "all":
        return True
    if visibility == "department":
        return str(metadata.get("department") or "general").lower() == str(user.get("department") or "").lower()
    if visibility == "private":
        return str(metadata.get("owner_id") or "") == str(user.get("id") or "")
    return False
