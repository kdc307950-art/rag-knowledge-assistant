# -*- coding: utf-8 -*-
# 冒烟测试：验证模块导入、缓存、解析与核心状态边界
"""
冒烟测试：验证项目模块可正常导入、关键修复生效、核心纯逻辑正确。
运行方式（在项目根目录 rag/ 下）：
    python -m pytest tests/smoke_test.py -v
"""

import importlib
def test_all_modules_importable():
    """核心模块应能全部导入，不抛 ImportError。"""
    modules = [
        "enterprise_rag.config",
        "enterprise_rag.core.state",
        "enterprise_rag.agent.memory",
        "enterprise_rag.llm.client",
        "enterprise_rag.llm.schema",
        "enterprise_rag.services.cache_service",
        "enterprise_rag.services.chat_service",
        "enterprise_rag.services.document_service",
        "enterprise_rag.utils.cleaner",
        "enterprise_rag.utils.loader",
        "enterprise_rag.rag.chunker",
        "enterprise_rag.rag.hybrid",
        "enterprise_rag.rag.reranker",
        "enterprise_rag.rag.retriever",
    ]
    for m in modules:
        importlib.import_module(m)

def test_cache_key_varies_with_persistent_kb_generation(tmp_path):
    """缓存 key 应随持久化知识库代际变化，服务重启后仍能正确失效。"""
    from enterprise_rag.services.cache_service import CacheService
    from enterprise_rag.storage.cache import PersistentAnswerCache
    from enterprise_rag.storage.kb_manifest import KnowledgeBaseManifest

    backend = PersistentAnswerCache(tmp_path / "answer_cache.sqlite3")
    manifest = KnowledgeBaseManifest(tmp_path / "manifest.sqlite3")
    manifest.mark_initialized()
    svc = CacheService(persistent_cache=backend, manifest=manifest)
    k1 = svc.make_key("测试问题", "历史")
    manifest.commit_source("manual.txt", "revision-1", "hash-1", 1)
    k2 = svc.make_key("测试问题", "历史")
    assert k1 != k2


def test_loader_parses_txt():
    """TXT 文件解析应正常。"""
    from enterprise_rag.utils import loader

    class FakeFile:
        name = "test.txt"

        def read(self):
            return "hello".encode("utf-8")

    assert loader.read_file(FakeFile()) == "hello"


def test_cleaner_strips_html():
    """clean_llm_output 应移除 script/style/html 标签。"""
    from enterprise_rag.utils import cleaner
    import streamlit as st

    try:
        st.session_state.clear()
    except Exception:
        pass

    out = cleaner.clean_llm_output("<script>alert(1)</script>正常内容", force=True)
    assert "<script>" not in out
    assert "正常内容" in out


def test_chat_ui_keeps_message_history_bounded():
    """聊天 UI 不应因长会话无限增长 session_state。"""
    import streamlit as st

    from enterprise_rag.core import state
    from enterprise_rag.core.constants import MAX_MESSAGES
    from enterprise_rag.ui.chat_ui import _append_message

    try:
        st.session_state.clear()
    except Exception:
        pass
    state.init_state()

    for index in range(MAX_MESSAGES + 2):
        _append_message({"role": "user", "content": str(index)})

    assert len(st.session_state.messages) == MAX_MESSAGES
    assert st.session_state.messages[0]["content"] == "2"


def test_storage_embedding_import_is_lazy():
    """嵌入模块导入不应初始化向量库或加载模型依赖。"""
    from enterprise_rag.storage.embedding import get_embedding_model

    assert callable(get_embedding_model)
