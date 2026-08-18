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
    normalized_owner = str(raw.get("owner_id") or owner_id or "").strip()[:128]
    if visibility == "private" and not normalized_owner:
        raise ACLMetadataError("visibility=private 必须提供上传者身份")
    return {"classification": classification, "department": department, "visibility": visibility, "owner_id": normalized_owner}

def user_is_admin(user: dict[str, Any] | None) -> bool:
    return "admin" in set((user or {}).get("roles") or [])

def user_can_manage_documents(user: dict[str, Any] | None) -> bool:
    return bool(set((user or {}).get("roles") or []) & {"admin", "editor"})


def document_visible_to_user(metadata: dict[str, Any], user: dict[str, Any] | None) -> bool:
    if user is None:
        return False
    if user_is_admin(user):
        return True
    visibility = str(metadata.get("visibility") or "all").strip().lower()
    if visibility == "all":
        return True
    if visibility == "department":
        return str(metadata.get("department") or "general").lower() == str(user.get("department") or "").lower()
    if visibility == "private":
        return str(metadata.get("owner_id") or "") == str(user.get("id") or "")
    return False
