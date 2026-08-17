"""框架无关的核心模块导入烟测。"""

import importlib


def test_core_modules_import_without_ui_runtime():
    modules = [
        "enterprise_rag.config",
        "enterprise_rag.core.state",
        "enterprise_rag.agent.memory",
        "enterprise_rag.llm.client",
        "enterprise_rag.llm.schema",
        "enterprise_rag.services.cache_service",
        "enterprise_rag.services.chat_service",
        "enterprise_rag.services.document_service",
        "enterprise_rag.services.entity_service",
        "enterprise_rag.utils.cleaner",
        "enterprise_rag.utils.loader",
        "enterprise_rag.rag.chunker",
        "enterprise_rag.rag.hybrid",
        "enterprise_rag.rag.reranker",
        "enterprise_rag.rag.retriever",
    ]

    for module in modules:
        importlib.import_module(module)
