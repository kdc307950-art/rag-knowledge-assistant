"""检索辅助函数的惰性导出，避免导入包时提前加载模型。

Keeping this package initializer lightweight prevents ``rag.chunker`` imports
from pulling in ``rag.retriever`` while the vector store is still importing.
"""

__all__ = [
    "retrieve_context",
    "load_reranker",
    "get_reranker",
    "split_by_chapters",
    "create_parent_child_chunks",
    "rebuild_bm25",
    "hybrid_search",
]


def __getattr__(name):
    if name == "retrieve_context":
        from .retriever import retrieve_context

        return retrieve_context
    if name == "load_reranker":
        from .reranker import load_reranker

        return load_reranker
    if name == "get_reranker":
        from .reranker import get_reranker

        return get_reranker
    if name in {"split_by_chapters", "create_parent_child_chunks"}:
        from .chunker import create_parent_child_chunks, split_by_chapters

        return {
            "split_by_chapters": split_by_chapters,
            "create_parent_child_chunks": create_parent_child_chunks,
        }[name]
    if name in {"rebuild_bm25", "hybrid_search"}:
        from .hybrid import hybrid_search, rebuild_bm25

        return {"rebuild_bm25": rebuild_bm25, "hybrid_search": hybrid_search}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
