"""API 请求模型。"""
from __future__ import annotations

from pydantic import BaseModel


class ChatRequest(BaseModel):
    query: str
    session_id: str | None = None


class GeneralRequest(BaseModel):
    query: str


class DraftRequest(BaseModel):
    query: str
    retrieval_query: str
