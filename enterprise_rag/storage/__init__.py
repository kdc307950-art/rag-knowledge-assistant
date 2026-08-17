"""向量存储与嵌入模型的惰性导出。"""

from .embedding import get_embedding_model, get_model_status, load_embedding_model

_VECTOR_STORE_EXPORTS = {
    "add_document_to_kb",
    "begin_batch_index_updates",
    "clear_all_documents",
    "delete_document",
    "finalize_batch_index_updates",
    "get_document_count",
    "get_doc_count",
    "get_kb_collection",
    "get_search_function",
    "list_documents",
    "update_doc_count",
}

__all__ = [
    "add_document_to_kb",
    "begin_batch_index_updates",
    "clear_all_documents",
    "delete_document",
    "finalize_batch_index_updates",
    "get_document_count",
    "get_doc_count",
    "get_embedding_model",
    "get_kb_collection",
    "get_model_status",
    "get_search_function",
    "list_documents",
    "load_embedding_model",
    "update_doc_count",
]


def __getattr__(name):
    if name in _VECTOR_STORE_EXPORTS:
        from . import vector_store

        return getattr(vector_store, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
