"""验证回答缓存只使用 manifest 代际，并在读写竞态时 fail closed。"""


def _init_state():
    from enterprise_rag.services import cache_service

    cache_service._l1_cache.clear()


def test_l1_cache_hit_is_rejected_when_manifest_changes_during_lookup(tmp_path):
    """L1 取值后代际变化时，旧答案必须被丢弃并从会话缓存移除。"""
    from enterprise_rag.services import cache_service
    from enterprise_rag.services.cache_service import CacheService
    from enterprise_rag.storage.cache import PersistentAnswerCache

    _init_state()
    reads = iter([7, 8])
    service = CacheService(
        persistent_cache=PersistentAnswerCache(tmp_path / "cache.sqlite3"),
        generation_reader=lambda: next(reads),
    )
    cache_service._l1_cache["key"] = {"content": "旧答案"}

    assert service.get("key", expected_generation=7) is None
    assert "key" not in cache_service._l1_cache


def test_l2_cache_hit_is_rejected_when_manifest_changes_during_lookup(tmp_path):
    """L2 命中后代际变化时，不得回填 L1 或返回旧答案。"""
    from enterprise_rag.services import cache_service
    from enterprise_rag.services.cache_service import CacheService
    from enterprise_rag.storage.cache import PersistentAnswerCache

    _init_state()
    backend = PersistentAnswerCache(tmp_path / "cache.sqlite3")
    backend.set("key", {"content": "旧答案"})
    reads = iter([7, 8])
    service = CacheService(
        persistent_cache=backend,
        generation_reader=lambda: next(reads),
    )

    assert service.get("key", expected_generation=7) is None
    assert "key" not in cache_service._l1_cache


def test_cache_write_is_rolled_back_when_manifest_changes_after_set(tmp_path):
    """持久化刚完成时若代际变化，应同步清理 L1/L2 的竞态写入。"""
    from enterprise_rag.services import cache_service
    from enterprise_rag.services.cache_service import CacheService
    from enterprise_rag.storage.cache import PersistentAnswerCache

    _init_state()
    backend = PersistentAnswerCache(tmp_path / "cache.sqlite3")
    reads = iter([7, 8])
    service = CacheService(
        persistent_cache=backend,
        generation_reader=lambda: next(reads),
    )

    assert service.set(
        "key", {"content": "旧答案"}, expected_generation=7
    ) is False
    assert backend.get("key") is None
    assert "key" not in cache_service._l1_cache


def test_manifest_commit_naturally_changes_cache_key(tmp_path):
    """只提交 manifest 即可失效旧答案，不依赖第二个缓存代际数据库。"""
    from enterprise_rag.services import cache_service
    from enterprise_rag.services.cache_service import CacheService
    from enterprise_rag.storage.cache import PersistentAnswerCache
    from enterprise_rag.storage.kb_manifest import KnowledgeBaseManifest

    manifest = KnowledgeBaseManifest(tmp_path / "manifest.sqlite3")
    manifest.mark_initialized()
    service = CacheService(
        persistent_cache=PersistentAnswerCache(tmp_path / "cache.sqlite3"),
        generation_reader=lambda: manifest.snapshot().generation,
    )
    old_key = service.make_key("问题", "")
    service.persistent_cache.set(old_key, {"content": "旧答案"})

    manifest.commit_source("a.txt", "revision-1", "hash-1", 1)
    new_key = service.make_key("问题", "")

    assert old_key != new_key
    assert service.get(new_key, expected_generation=1) is None


def test_rag_cache_hit_rechecks_generation_before_yield():
    """即使扩展缓存绕过服务层双检，RAG 返回前仍会执行最后一次复核。"""
    from enterprise_rag.services import rag_service

    decision = rag_service.RetrievalDecision(
        query="问题",
        retrieval_query="问题",
        context="旧资料",
        sources=["manual.txt"],
        raw_results=[{"rerank_score": 0.9}],
        history_summary="",
        kb_generation=7,
    )
    reads = iter([7, 8])

    class RacingCache:
        def get_generation(self):
            return next(reads)

        def make_key(self, *_args, **_kwargs):
            return "key"

        def get(self, _key, **_kwargs):
            return {"content": "不应返回的旧答案", "sources": []}

    service = rag_service.RagService()
    service.cache_service = RacingCache()

    visible = "".join(
        service._stream_from_decision(decision, template="{context}", mode="rag")
    )

    assert visible == "知识库刚刚完成更新，请重新提交问题以使用最新资料。"
    assert service._last_meta["is_kb_stale"] is True
    assert service._last_meta.get("from_cache") is not True
