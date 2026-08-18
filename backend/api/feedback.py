"""Single-tenant answer-quality feedback and admin review APIs."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator

from enterprise_rag.services.quality_service import (
    FEEDBACK_REASONS,
    REVIEW_RESULTS,
    FeedbackConflictError,
    FeedbackForbiddenError,
    FeedbackIneligibleError,
    FeedbackNotFoundError,
    FeedbackRateLimitError,
    get_quality_service,
)
from enterprise_rag.storage.acl import user_is_admin
from enterprise_rag.utils.logger import log_audit_event


router = APIRouter()


class FeedbackCreateRequest(BaseModel):
    message_id: str = Field(min_length=1, max_length=64)
    verdict: Literal["up", "down"]
    reason: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def validate_reason(self):
        if self.verdict == "up" and self.reason is not None:
            raise ValueError("赞不接受 reason")
        if self.verdict == "down" and self.reason not in FEEDBACK_REASONS:
            raise ValueError("踩必须使用受支持的 reason")
        return self


class FeedbackReviewRequest(BaseModel):
    accepted: bool
    adjudication_result: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_result(self):
        if self.adjudication_result not in REVIEW_RESULTS:
            raise ValueError("adjudication_result 无效")
        return self


def _quality_store():
    try:
        quality = get_quality_service()
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="质量反馈配置无效") from exc
    if quality is None:
        raise HTTPException(status_code=503, detail="质量反馈采集未配置")
    return quality


def _current_user(request: Request) -> dict:
    return getattr(request.state, "current_user", None) or {}


def _require_admin(request: Request) -> dict:
    user = _current_user(request)
    if not user_is_admin(user):
        raise HTTPException(status_code=403, detail="仅管理员可以评审反馈")
    return user


@router.post("/feedback")
async def submit_feedback(payload: FeedbackCreateRequest, request: Request):
    """Record at most one immutable verdict for a caller-owned chat answer."""
    user = _current_user(request)
    try:
        result = _quality_store().submit_feedback(
            message_id=payload.message_id,
            principal_id=str(user.get("id") or ""),
            verdict=payload.verdict,
            reason=payload.reason,
        )
    except FeedbackNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FeedbackIneligibleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FeedbackForbiddenError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except FeedbackConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except FeedbackRateLimitError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    log_audit_event(
        "quality_feedback_submitted",
        user_id=user.get("id"),
        verdict=payload.verdict,
        status=result.status,
    )
    return {"id": result.feedback_id, "status": result.status}


@router.get("/feedback/review")
async def list_feedback_for_review(
    request: Request,
    status: Literal["pending", "accepted", "rejected"] = Query(default="pending"),
    limit: int = Query(default=50, ge=1, le=100),
):
    _require_admin(request)
    return {"feedback": _quality_store().list_feedback_for_review(status=status, limit=limit)}


@router.patch("/feedback/{feedback_id}/review")
async def review_feedback(
    feedback_id: int,
    payload: FeedbackReviewRequest,
    request: Request,
):
    user = _require_admin(request)
    updated = _quality_store().review_feedback(
        feedback_id,
        reviewer_id=str(user.get("id") or ""),
        adjudication_result=payload.adjudication_result,
        accepted=payload.accepted,
    )
    if not updated:
        raise HTTPException(status_code=409, detail="反馈不存在或已完成评审")
    log_audit_event(
        "quality_feedback_reviewed",
        user_id=user.get("id"),
        feedback_id=feedback_id,
        accepted=payload.accepted,
        adjudication_result=payload.adjudication_result,
    )
    return {"ok": True}
