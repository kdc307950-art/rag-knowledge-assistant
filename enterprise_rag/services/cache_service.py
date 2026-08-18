"""回答缓存服务：进程级 L1 + SQLite L2，并按知识库代际自动失效。"""

import hashlib
import hmac
import logging
import os
import threading
from collections import OrderedDict
from collections.abc import Callable

from ..core.constants import MAX_CACHE_SIZE
from ..config import (
    ANSWER_CACHE_TTL_SECONDS,
    KB_DATA_DIR,
    MODEL_VERSION,
    PROMPT_VERSION,
    RETRIEVAL_VERSION,
)
from ..storage.cache import PersistentAnswerCache
from ..storage.kb_manifest import KnowledgeBaseManifest, get_manifest


logger = logging.getLogger(__name__)


def _record_cache(hit: bool, level: str) -> None:
    try:
        from backend.observability.metrics import mark_cache_hit, mark_cache_miss

        (mark_cache_hit if hit else mark_cache_miss)(level)
    except Exception:
        logger.debug("记录缓存指标失败", exc_info=True)

# 缓存协议变化时递增版本号，避免同一问题和知识库代际复用旧答案。
CACHE_SCHEMA_VERSION = "grounded-rag-v6"

# 进程级 L1 缓存：跨会话共享、LRU 有界、线程安全。
# API 化后缓存按知识库代际和查询摘要隔离；HTTP 会话由 backend.session 管理。
_l1_cache: OrderedDict = OrderedDict()
_l1_lock = threading.Lock()

class CacheService:
    """统一管理进程级 L1 LRU 缓存与持久化 TTL/LRU 缓存。"""
    def __init__(
        self,
        persistent_cache: PersistentAnswerCache | None = None,
        manifest: KnowledgeBaseManifest | None = None,
        generation_reader: Callable[[], int] | None = None,
    ):
        self.persistent_cache = persistent_cache or PersistentAnswerCache(
            max_entries=MAX_CACHE_SIZE,
            ttl_seconds=ANSWER_CACHE_TTL_SECONDS,
        )
        self.manifest = manifest
        # 测试或外部适配层可传入同一事务快照的读取函数；生产默认读取 manifest。
        self.generation_reader = generation_reader

    def _manifest(self) -> KnowledgeBaseManifest:
        return self.manifest or get_manifest()

    @staticmethod
    def _digest(value: str) -> str:
        payload = value.encode("utf-8")
        salt = os.getenv("CACHE_SALT", "").encode("utf-8")
        if salt:
            return hmac.new(salt, payload, hashlib.sha256).hexdigest()
        return hashlib.sha256(payload).hexdigest()

    def make_key(
        self,
        enhanced_query,
        history_summary,
        original_query=None,
        mode="rag",
        kb_generation: int | None = None,
    ):
        """组合查询、历史、知识库路径/代际和模型版本生成隔离键。"""
        if kb_generation is None:
            kb_generation = self.get_generation()
        raw = (
            CACHE_SCHEMA_VERSION
            + "|"
            # 使用完整文本计算摘要；截断长问题会造成不同查询命中同一缓存。
            + self._digest(original_query or enhanced_query)
            + "|"
            + self._digest(enhanced_query)
            + "|"
            + self._digest(history_summary)
            + "|"
            + str(kb_generation)
            + "|"
            # 向量库迁移到另一个目录时，即使代际数同为 0，也不能复用旧库答案。
            + self._digest(str(KB_DATA_DIR.resolve(strict=False)))
            + "|"
            + PROMPT_VERSION
            + "|"
            + RETRIEVAL_VERSION
            + "|"
            + MODEL_VERSION
            + "|"
            + mode
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get_generation(self) -> int:
        """读取当前持久化知识库代际，供检索快照写缓存前复核。"""
        if self.generation_reader is not None:
            return int(self.generation_reader())
        return self._manifest().snapshot().generation

    def get(self, key, *, expected_generation: int | None = None):
        """优先读取进程级 L1 缓存，未命中时回源 SQLite 并回填 L1。"""
        if expected_generation is not None and self.get_generation() != expected_generation:
            _record_cache(False, "l1")
            return None
        with _l1_lock:
            value = _l1_cache.get(key)
            if value is not None:
                if expected_generation is not None and self.get_generation() != expected_generation:
                    # L1 已取出后才发现知识库更新，旧答案不能继续留在 L1。
                    _l1_cache.pop(key, None)
                    _record_cache(False, "l1")
                    return None
                _l1_cache.pop(key)
                _l1_cache[key] = value
                _record_cache(True, "l1")
                return value

        _record_cache(False, "l1")

        value = self.persistent_cache.get(key)
        if value is not None:
            if expected_generation is not None and self.get_generation() != expected_generation:
                return None
            with _l1_lock:
                _l1_cache[key] = value
                self._trim_session_cache(_l1_cache)
            _record_cache(True, "l2")
        else:
            _record_cache(False, "l2")
        return value

    def set(self, key, value, *, expected_generation: int | None = None) -> bool:
        """先写进程级 L1，再尽力持久化；L2 失败不影响正常回答。"""
        if expected_generation is not None and self.get_generation() != expected_generation:
            return False
        with _l1_lock:
            _l1_cache.pop(key, None)
            _l1_cache[key] = value
            self._trim_session_cache(_l1_cache)
        try:
            self.persistent_cache.set(key, value)
        except Exception:
            # 持久化缓存只是性能优化，不是生成回答的前置条件。
            logger.exception("持久化回答缓存写入失败")
            with _l1_lock:
                _l1_cache.pop(key, None)
            return False
        if expected_generation is not None and self.get_generation() != expected_generation:
            with _l1_lock:
                _l1_cache.pop(key, None)
            # 写入期间知识库已提交新版本。虽然新代际会生成不同键，仍主动
            # 删除旧 L2 项，避免未来错误回退到旧代际时重新暴露过期答案。
            try:
                self.persistent_cache.delete(key)
            except Exception:
                logger.exception("知识库代际变化后清理旧回答缓存失败")
            return False
        return True

    @staticmethod
    def _trim_session_cache(cache) -> None:
        while len(cache) > MAX_CACHE_SIZE:
            cache.pop(next(iter(cache)))
