"""两阶段检索入口：混合召回、交叉编码器重排、去重和来源构造。"""
from enterprise_rag.config import (
    FINAL_TOP_K,
    INITIAL_RETRIEVAL_K,
    MAX_CONTEXT_LENGTH,
    RERANK_SCORE_THRESHOLD,
    RERANK_BATCH_SIZE,
)
from enterprise_rag.storage.vector_store import get_search_function
from enterprise_rag.core.exceptions import (
    DocumentGovernanceError,
    KnowledgeBaseBusyError,
    RetrievalException,
)
from .reranker import get_reranker
import logging

try:
    from backend.observability.context import timed_stage
except Exception:  # pragma: no cover - core package can run without the API
    from contextlib import nullcontext

    def timed_stage(_name):
        return nullcontext()

logger = logging.getLogger(__name__)


def calculate_rerank_threshold(_scores) -> float:
    """返回严格知识库模式使用的绝对重排阈值。"""
    # 严格模式下阈值不随本次候选分数下调。
    return RERANK_SCORE_THRESHOLD


def retrieve_context(
    query: str,
    n_results: int = None,
    top_k: int = None,
    return_raw: bool = False,
    return_generation: bool = False,
    retrieval_policy: str | None = None,
):
    """召回并重排候选片段，可同时返回本次检索对应的知识库代际。"""
    # 第一阶段召回候选分块，第二阶段交由重排器筛选可靠上下文。
    if n_results is None:
        n_results = INITIAL_RETRIEVAL_K
    if top_k is None:
        top_k = FINAL_TOP_K

    try:
        with timed_stage("retrieval"):
            search_func = get_search_function()
            if retrieval_policy is None:
                results = search_func(query, n_results=n_results)
            else:
                results = search_func(
                    query,
                    n_results=n_results,
                    retrieval_policy=retrieval_policy,
                )
        kb_generation = results.get("_kb_generation")
        documents = results.get("documents") or []
        metadatas = results.get("metadatas") or []
        all_distances = results.get("distances") or []
        docs = documents[0] if documents and isinstance(documents[0], list) else documents
        metas = metadatas[0] if metadatas and isinstance(metadatas[0], list) else metadatas
        distances = (
            all_distances[0]
            if all_distances and isinstance(all_distances[0], list)
            else all_distances
        )
    except KnowledgeBaseBusyError:
        # 上传/删除期间没有稳定快照，上层应显示专用忙碌提示而不是普通故障。
        raise
    except DocumentGovernanceError:
        # 生效关系未确认是可识别的治理状态，不能包装成通用检索故障。
        raise
    except Exception as e:
        logger.error(f"检索失败: {e}", exc_info=True)
        raise RetrievalException(f"知识库检索失败: {e}") from e

    if not docs:
        if return_raw and return_generation:
            return "", [], [], kb_generation
        if return_raw:
            return "", [], []
        if return_generation:
            return "", [], kb_generation
        return "", []

    # 交叉编码器精排比向量距离更适合决定是否将片段交给模型回答。
    # 将模型加载和 predict 一起计入 rerank，便于区分 retrieval 与重排瓶颈。
    pairs = [[query, doc] for doc in docs]
    with timed_stage("rerank"):
        reranker = get_reranker()
        try:
            scores = reranker.predict(pairs, batch_size=RERANK_BATCH_SIZE)
        except TypeError:
            # 兼容不接受 batch_size 的旧重排器实现和轻量测试替身。
            scores = reranker.predict(pairs)

    scored_items = list(zip(docs, metas, distances, scores))
    scored_items.sort(key=lambda x: x[3], reverse=True)

    # 同一父块只保留最高分子块，避免重复父块资料挤占上下文窗口。
    seen_parent_ids = set()
    unique_items = []
    for doc, meta, dist, score in scored_items:
        parent_id = (meta or {}).get("parent_id")
        # 同一父块的多个子块共享上下文；旧数据无 parent_id 时按正文去重。
        dedupe_key = ("parent", parent_id) if parent_id else ("document", doc)
        if dedupe_key in seen_parent_ids:
            continue
        seen_parent_ids.add(dedupe_key)
        unique_items.append((doc, meta, dist, score))

    score_threshold = calculate_rerank_threshold(score for _, _, _, score in unique_items)
    logger.debug(
        "Reranker threshold selected: threshold=%.4f, candidates=%d",
        score_threshold,
        len(unique_items),
    )
    # 没有片段通过阈值时返回空结果，由上层严格拒答而不是通用补全。
    filtered_items = [
        (doc, meta, dist, score)
        for doc, meta, dist, score in unique_items
        if score >= score_threshold
    ]

    if not filtered_items:
        if return_raw and return_generation:
            return "", [], [], kb_generation
        if return_raw:
            return "", [], []
        if return_generation:
            return "", [], kb_generation
        return "", []

    final_items = filtered_items[:top_k]

    context_parts = []
    sources = []
    raw_results = []

    for index, (doc, meta, dist, score) in enumerate(final_items, 1):
        citation_id = f"S{index}"
        meta_safe = meta or {}
        source = meta_safe.get("source", "未知文件")
        chapter = meta_safe.get("chapter", "未知章节")
        page = meta_safe.get("page")
        paragraph = meta_safe.get("paragraph")
        # 子块负责命中，父块负责提供完整上下文。
        parent_text = meta_safe.get("parent_text", "")
        context_text = parent_text if parent_text else doc

        location_parts = []
        if page is not None:
            location_parts.append(f"第 {page} 页")
        if paragraph is not None:
            location_parts.append(f"第 {paragraph} 段")
        location = f"，{'，'.join(location_parts)}" if location_parts else ""

        context_parts.append(
            f"[{citation_id} | 来源：{source}，章节：{chapter}{location}]\n{context_text}"
        )
        sources.append(f"{source} | {chapter}{location}")

        raw_results.append({
            "citation_id": citation_id,
            "document": doc,
            "parent_text": parent_text,
            "parent_id": meta_safe.get("parent_id"),
            "source": source,
            "chapter": chapter,
            "page": page,
            "paragraph": paragraph,
            "vector_distance": float(dist) if dist else 0.0,
            "rerank_score": float(score)
        })

    context = "\n\n".join(context_parts)[:MAX_CONTEXT_LENGTH]

    if return_raw and return_generation:
        return context, sources, raw_results, kb_generation
    if return_raw:
        return context, sources, raw_results
    if return_generation:
        return context, sources, kb_generation
    return context, sources
