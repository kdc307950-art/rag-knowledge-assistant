"""验证 BM25 始终服从 manifest 可见 revision。"""

from types import SimpleNamespace


def _patch_embedding(monkeypatch, hybrid):
    monkeypatch.setattr(
        hybrid,
        "get_embedding_model",
        lambda: SimpleNamespace(
            encode=lambda *_args, **_kwargs: SimpleNamespace(
                tolist=lambda: [[0.1, 0.2]]
            )
        ),
    )


def test_bm25_rebuild_type_error_never_retries_without_manifest_filter(monkeypatch):
    """实现错误必须上抛，不能降级成无 where 的全库索引。"""
    from enterprise_rag.storage import vector_store

    calls = []
    monkeypatch.setattr(vector_store, "_use_hybrid", True)
    monkeypatch.setattr(vector_store, "_bm25_dirty", True)
    monkeypatch.setattr(
        vector_store,
        "_ensure_manifest_initialized",
        lambda _collection: SimpleNamespace(active_revisions={"a.txt": "active-rev"}),
    )

    def broken_rebuild(_collection, **kwargs):
        calls.append(kwargs.get("where"))
        raise TypeError("internal implementation error")

    monkeypatch.setattr(vector_store, "rebuild_bm25", broken_rebuild)

    try:
        vector_store.ensure_bm25(object(), retrieval_policy="all_active")
    except TypeError as exc:
        assert "internal implementation error" in str(exc)
    else:
        raise AssertionError("BM25 内部 TypeError 不应被无过滤重试吞掉")

    assert calls == [{"revision_id": {"$in": ["active-rev"]}}]
    assert vector_store._bm25_dirty is True


def test_pure_vector_wrapper_preserves_explicit_retrieval_policy(monkeypatch):
    """纯向量回退路径必须与混合检索使用同一治理策略。"""
    from enterprise_rag.storage import vector_store

    calls = []

    def fake_search(query_text, *, n_results, retrieval_policy):
        calls.append((query_text, n_results, retrieval_policy))
        return {"ids": [[]]}

    monkeypatch.setattr(vector_store, "_use_hybrid", False)
    monkeypatch.setattr(vector_store, "search", fake_search)

    assert vector_store.hybrid_search_wrapper(
        "年休假",
        n_results=7,
        retrieval_policy="authoritative",
    ) == {"ids": [[]]}
    assert calls == [("年休假", 7, "authoritative")]


def test_hybrid_final_candidates_drop_inactive_revisions(monkeypatch):
    """旧 revision 即使进入 BM25 候选，最终结果也只能保留激活版本。"""
    from enterprise_rag.rag import hybrid

    class FakeCollection:
        def query(self, **_kwargs):
            return {
                "ids": [[]],
                "documents": [[]],
                "metadatas": [[]],
                "distances": [[]],
            }

        def get(self, ids, include):
            assert include == ["documents", "metadatas"]
            records = {
                "old": ("旧版本内容", {"source": "a.txt", "revision_id": "old-rev"}),
                "active": (
                    "当前版本内容",
                    {"source": "a.txt", "revision_id": "active-rev"},
                ),
            }
            selected = [(item_id, *records[item_id]) for item_id in ids]
            return {
                "ids": [item[0] for item in selected],
                "documents": [item[1] for item in selected],
                "metadatas": [item[2] for item in selected],
            }

    class FakeBm25:
        def get_scores(self, _tokens):
            return [9.0, 8.0]

    _patch_embedding(monkeypatch, hybrid)
    monkeypatch.setattr(hybrid, "bm25_index", FakeBm25())
    monkeypatch.setattr(hybrid, "doc_id_list", ["old", "active"])
    monkeypatch.setattr(hybrid, "documents_list", ["旧版本内容", "当前版本内容"])

    result = hybrid.hybrid_search(
        "内容",
        FakeCollection(),
        top_k=2,
        where={"revision_id": {"$in": ["active-rev"]}},
    )

    assert result == {
        "ids": ["active"],
        "documents": ["当前版本内容"],
        "metadatas": [{"source": "a.txt", "revision_id": "active-rev"}],
    }
