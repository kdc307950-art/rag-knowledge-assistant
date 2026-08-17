"""使用真实 Chroma 验证知识库 manifest 的两阶段提交与查询隔离。"""

from __future__ import annotations

import hashlib

import chromadb
import pytest


class DeterministicEmbedding:
    """提供无需下载模型的确定性二维向量，测试仍使用真实 Chroma 查询。"""

    @staticmethod
    def encode(texts, **_kwargs):
        import numpy as np

        vectors = []
        for text in texts:
            normalized = str(text)
            if "new" in normalized or "新版" in normalized:
                vectors.append([1.0, 0.0])
            elif "old" in normalized or "旧版" in normalized:
                vectors.append([0.0, 1.0])
            else:
                vectors.append([0.7, 0.7])
        return np.asarray(vectors, dtype=float)


@pytest.fixture
def real_store(monkeypatch, tmp_path):
    """隔离真实 Chroma、manifest 和回答缓存，并重置进程级闸门。"""
    from enterprise_rag.storage import vector_store
    from enterprise_rag.storage.kb_manifest import KnowledgeBaseManifest

    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    collection = client.get_or_create_collection(
        name="manifest_consistency", metadata={"hnsw:space": "cosine"}
    )
    manifest = KnowledgeBaseManifest(tmp_path / "manifest.sqlite3")
    manifest.mark_initialized()

    monkeypatch.setattr(vector_store, "get_kb_collection", lambda: collection)
    monkeypatch.setattr(vector_store, "get_manifest", lambda: manifest)
    monkeypatch.setattr(
        vector_store, "get_embedding_model", lambda: DeterministicEmbedding()
    )
    monkeypatch.setattr(vector_store, "_use_hybrid", False)
    monkeypatch.setattr(vector_store, "_batch_update_depth", 0)
    monkeypatch.setattr(vector_store, "_batch_mutated", False)
    monkeypatch.setattr(vector_store, "_destructive_mutation", False)
    monkeypatch.setattr(vector_store, "_bm25_dirty", True)
    return vector_store, collection, manifest


def _insert_revision(collection, source: str, revision: str, text: str, *, complete=True):
    """直接写入物理 revision，模拟提交前进程中断留下的 staging 数据。"""
    count = 2 if complete else 1
    ids = [f"{revision}_{index}" for index in range(count)]
    documents = [f"{text}-{index}" for index in range(count)]
    metadatas = [
        {
            "source": source,
            "revision_id": revision,
            "ingest_state": "staging",
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "file_id": f"file-{revision}",
            "parent_id": f"parent-{revision}-{index}",
            "parent_text": document,
            "chunk_index": index,
        }
        for index, document in enumerate(documents)
    ]
    collection.add(
        ids=ids,
        documents=documents,
        embeddings=DeterministicEmbedding.encode(documents).tolist(),
        metadatas=metadatas,
    )
    return ids


def _visible_documents(vector_store, query="new"):
    result = vector_store.search(query, n_results=20)
    return list(result["documents"][0]), result


def test_uncommitted_staging_revision_is_invisible(real_store):
    """未写入 manifest 的完整 staging 分块不能被查询、列表或计数看到。"""
    vector_store, collection, _manifest = real_store
    _insert_revision(collection, "orphan.txt", "staging-only", "new orphan")

    documents, result = _visible_documents(vector_store)

    assert documents == []
    assert result["_kb_generation"] == 0
    assert vector_store.list_documents() == []
    assert vector_store.get_doc_count() == 0
    assert collection.count() == 2


@pytest.mark.parametrize("complete", [False, True], ids=["partial", "complete"])
def test_retry_replaces_stale_staging_revision(real_store, complete):
    """无论上次 staging 完整与否，重试成功后都只提交新的随机 revision。"""
    vector_store, collection, manifest = real_store
    stale_ids = _insert_revision(
        collection, "retry.txt", "stale-revision", "old stale", complete=complete
    )
    monkeypatch_content = "new retry content"

    assert (
        vector_store.add_document_to_kb(
            "retry.txt", monkeypatch_content, refresh_indexes=False
        )
        == "added"
    )

    active = manifest.get_source("retry.txt")
    assert active is not None
    assert active["active_revision"] != "stale-revision"
    physical = collection.get(where={"source": "retry.txt"}, include=["metadatas"])
    assert not set(stale_ids) & set(physical["ids"])
    assert {
        metadata["revision_id"] for metadata in physical["metadatas"]
    } == {active["active_revision"]}
    documents, _result = _visible_documents(vector_store, "new")
    assert documents
    assert all("new retry" in document for document in documents)


def test_committed_new_revision_stays_visible_when_old_cleanup_fails(
    real_store, monkeypatch
):
    """manifest 切换后即使旧分块删除失败，逻辑查询也只能返回新版。"""
    vector_store, collection, manifest = real_store
    old_ids = _insert_revision(collection, "replace.txt", "old-revision", "old data")
    manifest.commit_source("replace.txt", "old-revision", "old-hash", len(old_ids))

    original_delete = collection.delete

    def fail_old_cleanup(*, ids=None, **kwargs):
        if ids and set(ids) == set(old_ids):
            raise RuntimeError("simulated cleanup failure")
        return original_delete(ids=ids, **kwargs)

    monkeypatch.setattr(collection, "delete", fail_old_cleanup)

    assert (
        vector_store.add_document_to_kb(
            "replace.txt", "new replacement", refresh_indexes=False
        )
        == "added"
    )

    active = manifest.get_source("replace.txt")
    assert active["active_revision"] != "old-revision"
    physical = collection.get(where={"source": "replace.txt"}, include=["metadatas"])
    assert set(old_ids).issubset(set(physical["ids"]))
    assert len({meta["revision_id"] for meta in physical["metadatas"]}) == 2
    documents, _result = _visible_documents(vector_store, "new")
    assert documents
    assert all("new replacement" in document for document in documents)


def test_search_returns_answer_cache_generation(real_store):
    """查询结果必须携带回答缓存代际，供生成前后做一致性复核。"""
    vector_store, _collection, manifest = real_store

    empty_result = vector_store.search("anything")
    assert empty_result["_kb_generation"] == 0

    assert (
        vector_store.add_document_to_kb(
            "generation.txt", "new generation", refresh_indexes=True
        )
        == "added"
    )
    result = vector_store.search("new")
    assert result["_kb_generation"] == manifest.snapshot().generation == 1


@pytest.mark.parametrize("use_hybrid", [False, True], ids=["vector", "hybrid"])
def test_active_upload_batch_rejects_vector_and_hybrid_queries(
    real_store, monkeypatch, use_hybrid
):
    """上传批次活跃期间，两种检索入口都必须在访问 Chroma 前快速熔断。"""
    from enterprise_rag.core.exceptions import KnowledgeBaseBusyError

    vector_store, collection, _manifest = real_store
    monkeypatch.setattr(vector_store, "_use_hybrid", use_hybrid)
    monkeypatch.setattr(
        vector_store,
        "hybrid_search",
        lambda *_args, **_kwargs: pytest.fail("忙碌期间不应进入混合检索"),
    )

    vector_store.begin_batch_index_updates()
    try:
        with pytest.raises(KnowledgeBaseBusyError):
            if use_hybrid:
                vector_store.hybrid_search_wrapper("query")
            else:
                vector_store.search("query")
        assert collection.count() == 0
    finally:
        vector_store.finalize_batch_index_updates(mutated=False)
