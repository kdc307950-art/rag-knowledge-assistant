"""基于原子替换索引的混合向量与BM25检索."""

from __future__ import annotations

import logging
import re
import threading

import jieba
import numpy as np
from rank_bm25 import BM25Okapi

from enterprise_rag.storage.embedding import get_embedding_model

logger = logging.getLogger(__name__)

_BM25_LOCK = threading.RLock()
bm25_index = None
doc_id_list: list[str] = []
documents_list: list[str] = []
metadatas_list: list[dict] = []

_KEYWORD_STOPWORDS = {
    "谁", "什么", "哪些", "哪个", "为什么", "如何", "怎么", "怎样", "多少",
    "公司", "政策", "制度", "问题", "介绍", "一下", "请问",
}


def _metadata_matches_where(metadata: dict, where: dict | None) -> bool:
    """复核 Chroma 过滤条件，防止旧 revision 从 BM25 旁路进入结果。"""
    if where is None:
        return True
    for key, expected in where.items():
        actual = metadata.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


def _get_visible_candidates(collection, ids: list[str], where: dict | None) -> dict:
    """按 ID 读取候选，并依据本次 manifest 快照再次过滤可见版本。"""
    if not ids:
        return {"ids": [], "documents": [], "metadatas": []}
    result = collection.get(ids=ids, include=["documents", "metadatas"])
    visible = [
        (doc_id, document, dict(metadata or {}))
        for doc_id, document, metadata in zip(
            result.get("ids") or [],
            result.get("documents") or [],
            result.get("metadatas") or [],
        )
        if _metadata_matches_where(dict(metadata or {}), where)
    ]
    return {
        "ids": [item[0] for item in visible],
        "documents": [item[1] for item in visible],
        "metadatas": [item[2] for item in visible],
    }


def _exact_keyword_ids(query: str, ids: list[str], documents: list[str]) -> list[str]:
    """返回查询中独特词汇的字面匹配候选项。
这是一项针对人名及其他专有名词的窄召回保障机制。检索结果
仍会经过已配置的 reranker（重排序器）处理后再送达大语言模型。
    """
    # 专有名词可能被纯语义检索遗漏；这里只补候选，最终仍由重排器裁决。
    compact_query = re.sub(r"[\s，。？！?,.!！]", "", query or "")
    terms = {
        term.strip()
        for term in jieba.cut(compact_query)
        if len(term.strip()) >= 2 and term.strip() not in _KEYWORD_STOPWORDS
    }
    for suffix in ("是谁", "是什么", "什么是", "有哪些", "有那些"):
        candidate = compact_query.replace(suffix, "").strip()
        if len(candidate) >= 2 and candidate not in _KEYWORD_STOPWORDS:
            terms.add(candidate)

    matched: list[str] = []
    for term in sorted(terms, key=len, reverse=True):
        for doc_id, document in zip(ids, documents):
            if term in (document or "") and doc_id not in matched:
                matched.append(doc_id)
    return matched


def rebuild_bm25(collection, where=None):
    """构建一个完整的本地索引，然后通过一次赋值操作将其发布上线."""
    global bm25_index, doc_id_list, documents_list, metadatas_list
    # 在局部变量中完整构建后一次性发布，查询线程不会读到半成品索引。
    get_kwargs = {"include": ["documents", "metadatas"]}
    if where is not None:
        get_kwargs["where"] = where
    all_docs = collection.get(**get_kwargs)
    ids = list(all_docs.get("ids") or [])
    documents = list(all_docs.get("documents") or [])
    if not documents:
        with _BM25_LOCK:
            bm25_index = None
            doc_id_list = []
            documents_list = []
            metadatas_list = []
        return

    tokenized_docs = [list(jieba.cut(doc or "")) for doc in documents]
    if all(not tokens for tokens in tokenized_docs):
        with _BM25_LOCK:
            bm25_index = None
            doc_id_list = []
            documents_list = []
            metadatas_list = []
        return

    new_index = BM25Okapi(tokenized_docs)
    with _BM25_LOCK:
        bm25_index = new_index
        doc_id_list = ids
        documents_list = documents
        metadatas_list = [dict(metadata or {}) for metadata in all_docs.get("metadatas") or []]


def hybrid_search(query, collection, top_k=10, alpha=0.5, where=None):
    # 向量分数覆盖语义相似，BM25 覆盖关键词匹配，二者合并后再排序。
    query_embedding = get_embedding_model().encode(
        [query], normalize_embeddings=True
    ).tolist()
    query_kwargs = {
        "query_embeddings": query_embedding,
        "n_results": max(1, min(50, int(top_k))),
        "include": ["documents", "metadatas", "distances"],
    }
    if where is not None:
        query_kwargs["where"] = where
    vec_results = collection.query(
        **query_kwargs,
    )
    vec_ids = vec_results.get("ids", [[]])[0] if vec_results.get("ids") else []
    vec_distances = vec_results.get("distances", [[]])[0] if vec_ids else []
    vec_scores = 1 - np.array(vec_distances) / 2 if vec_ids else np.array([])

    with _BM25_LOCK:
        index = bm25_index
        ids = list(doc_id_list)
        indexed_documents = list(documents_list)
        indexed_metadatas = list(metadatas_list)
    if index is None or not ids:
        top_ids = vec_ids[:top_k]
        # Chroma 不接受空的 ids 列表。全新或已清空的知识库应返回正常的
        # 空检索结果，由上层执行严格拒答，而不是误报“检索服务异常”。
        if not top_ids:
            return {"ids": [], "documents": [], "metadatas": []}
        return _get_visible_candidates(collection, top_ids, where)

    tokenized_query = list(jieba.cut(query))
    bm25_all_scores = index.get_scores(tokenized_query)
    if len(indexed_metadatas) != len(ids):
        # Compatibility with pre-metadata test doubles and old in-process
        # indexes; the final Chroma read still enforces `where`.
        visible_indices = list(range(len(ids)))
    else:
        visible_indices = [
            index for index, metadata in enumerate(indexed_metadatas)
            if _metadata_matches_where(metadata, where)
        ]
    bm25_top_indices = sorted(
        visible_indices, key=lambda index: bm25_all_scores[index], reverse=True
    )[:50]
    bm25_ids = [ids[i] for i in bm25_top_indices if i < len(ids)]
    bm25_scores = [bm25_all_scores[i] for i in bm25_top_indices if i < len(ids)]
    max_bm25 = max(bm25_scores) if bm25_scores and max(bm25_scores) > 0 else 1.0
    bm25_norm = np.array(bm25_scores) / max_bm25

    # 任一路命中的候选都保留，保证实体名称等关键词不会被向量结果覆盖。
    union_dict = {}
    for doc_id, score in zip(vec_ids, vec_scores):
        union_dict[doc_id] = [float(score), 0.0]
    for doc_id, score in zip(bm25_ids, bm25_norm):
        if doc_id in union_dict:
            union_dict[doc_id][1] = float(score)
        else:
            union_dict[doc_id] = [0.0, float(score)]
    visible_ids = [ids[index] for index in visible_indices if index < len(ids)]
    visible_documents = [
        indexed_documents[index]
        for index in visible_indices
        if index < len(indexed_documents)
    ]
    for doc_id in _exact_keyword_ids(query, visible_ids, visible_documents):
        if doc_id in union_dict:
            union_dict[doc_id][1] = max(union_dict[doc_id][1], 1.0)
        else:
            union_dict[doc_id] = [0.0, 1.0]

    combined = {
        doc_id: alpha * vector_score + (1 - alpha) * bm25_score
        for doc_id, (vector_score, bm25_score) in union_dict.items()
    }
    top_ids = [
        doc_id
        for doc_id, _ in sorted(
            combined.items(), key=lambda item: item[1], reverse=True
        )[:top_k]
    ]
    return _get_visible_candidates(collection, top_ids, where)
