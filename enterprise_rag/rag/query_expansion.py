# 检索前 query 扩展：法律术语 -> 法条原文描述性表述
from __future__ import annotations

import json
from typing import Any

from ..config import LEGAL_TERMS_PATH

_terms_cache: dict[str, str] | None = None


def _load_terms() -> dict[str, str]:
    """读术语表；文件不存在或 schema 不符时返回空表（扩展退化为无操作）。"""
    global _terms_cache
    if _terms_cache is not None:
        return _terms_cache
    terms: dict[str, str] = {}
    path = LEGAL_TERMS_PATH
    try:
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            raw = payload.get("terms") if isinstance(payload, dict) else None
            if isinstance(raw, dict):
                terms = {
                    str(k): str(v).strip()
                    for k, v in raw.items()
                    if str(k).strip() and str(v).strip()
                }
    except (OSError, json.JSONDecodeError, ValueError):
        terms = {}
    _terms_cache = terms
    return terms


def expand_query(query: str) -> str:
    """把 query 中出现的法律术语追加其法条原文表述，用于混合召回与重排。

    术语表为空时不改变原 query。扩展是追加式（保留原词），
    避免替换导致丢失术语本身的检索信号。
    """
    if not query:
        return query
    terms = _load_terms()
    if not terms:
        return query
    additions = [expansion for term, expansion in terms.items() if term in query]
    if not additions:
        return query
    return f"{query} {' '.join(additions)}"
