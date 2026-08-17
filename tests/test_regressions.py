from io import BytesIO
import json
import threading
import time
from types import SimpleNamespace
import importlib.util
import os
from pathlib import Path

import pytest


def _load_config_copy(monkeypatch, **environment):
    """隔离加载一份配置模块，避免污染当前测试进程中的正式配置。"""
    import enterprise_rag.config as current_config

    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    module_name = f"enterprise_rag._config_test_{time.time_ns()}"
    spec = importlib.util.spec_from_file_location(module_name, Path(current_config.__file__))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_config_rejects_non_finite_numeric_environment(monkeypatch):
    config = _load_config_copy(
        monkeypatch,
        ANSWER_CACHE_TTL_SECONDS="inf",
        RERANK_BATCH_SIZE="nan",
    )

    assert config.ANSWER_CACHE_TTL_SECONDS == 86_400
    assert config.RERANK_BATCH_SIZE == 16


def test_runtime_data_directory_moves_vector_store_as_one_unit(monkeypatch, tmp_path):
    """运行目录迁移时，向量库必须始终位于同一数据根目录内。"""
    data_dir = tmp_path / "runtime"
    monkeypatch.delenv("RAG_KB_DIR", raising=False)
    config = _load_config_copy(monkeypatch, RAG_DATA_DIR=str(data_dir))

    assert config.RUNTIME_DATA_DIR == data_dir
    assert config.KB_DATA_DIR == data_dir / "kb_data"


def test_config_rejects_separate_vector_store_override(monkeypatch, tmp_path):
    """禁止把 Chroma 与 manifest 配置到不同根目录，避免逻辑清单错配。"""
    with pytest.raises(ValueError, match="RAG_KB_DIR 已停用"):
        _load_config_copy(
            monkeypatch,
            RAG_DATA_DIR=str(tmp_path / "runtime"),
            RAG_KB_DIR=str(tmp_path / "vectors"),
        )


def test_dashscope_uses_provider_specific_key_before_generic_openai_key(monkeypatch):
    """连接 DashScope 时必须使用专用 Key，避免误用系统中的 OpenAI Key。"""
    config = _load_config_copy(
        monkeypatch,
        OPENAI_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1",
        OPENAI_API_KEY="generic-key",
        DASHSCOPE_API_KEY="dashscope-key",
    )

    assert config.API_KEY == "dashscope-key"
    assert config.API_KEY_ENV_NAME == "DASHSCOPE_API_KEY"


def test_non_dashscope_endpoint_keeps_generic_openai_key(monkeypatch):
    """非 DashScope 兼容接口仍沿用 OPENAI_API_KEY。"""
    config = _load_config_copy(
        monkeypatch,
        OPENAI_BASE_URL="https://api.openai.com/v1",
        OPENAI_API_KEY="generic-key",
        DASHSCOPE_API_KEY="dashscope-key",
    )

    assert config.API_KEY == "generic-key"
    assert config.API_KEY_ENV_NAME == "OPENAI_API_KEY"


def test_default_kb_directory_is_nested_under_runtime_data(monkeypatch, tmp_path):
    """向量库应始终随运行数据目录整体迁移。"""
    data_dir = tmp_path / "runtime"
    monkeypatch.delenv("RAG_KB_DIR", raising=False)
    config = _load_config_copy(monkeypatch, RAG_DATA_DIR=str(data_dir))

    assert config.RUNTIME_DATA_DIR == data_dir
    assert config.KB_DATA_DIR == data_dir / "kb_data"


@pytest.mark.parametrize(
    ("status", "ready", "expected"),
    [
        ("已加载", True, ("已加载", "ready")),
        ("已加载：BAAI/bge-reranker-v2-m3", True, ("已加载", "ready")),
        ("待加载（首次使用时下载）", True, ("待加载", "processing")),
        ("离线模式缺少重排模型", False, ("不可用", "error")),
    ],
)
def test_sidebar_model_badge_is_short_and_semantically_correct(status, ready, expected):
    """侧栏只显示稳定短标签，完整就绪原因留给悬停提示。"""
    from enterprise_rag.ui.sidebar import _model_badge

    assert _model_badge(status, ready) == expected


def test_vector_store_health_reports_hnsw_corruption(monkeypatch):
    """HNSW 读取失败必须暴露为索引异常，不能被伪装成空知识库。"""
    from enterprise_rag.storage import vector_store

    class BrokenCollection:
        def get(self, **_kwargs):
            raise RuntimeError("Error loading hnsw index")

    monkeypatch.setattr(vector_store, "is_knowledge_base_busy", lambda: False)
    monkeypatch.setattr(vector_store, "get_kb_collection", lambda: BrokenCollection())

    assert vector_store.get_vector_store_health() == (False, "索引异常")


def test_vector_store_health_accepts_empty_collection(monkeypatch):
    """空集合的轻量读取成功时，应报告就绪而不是索引异常。"""
    from enterprise_rag.storage import vector_store

    class EmptyCollection:
        def get(self, **_kwargs):
            return {"ids": []}

    monkeypatch.setattr(vector_store, "is_knowledge_base_busy", lambda: False)
    monkeypatch.setattr(vector_store, "get_kb_collection", lambda: EmptyCollection())

    assert vector_store.get_vector_store_health() == (True, "就绪")


class FakeCollection:
    def __init__(self):
        self.records = {}

    def get(self, where=None, limit=None, include=None, ids=None):
        if ids is not None:
            selected = [(item_id, self.records[item_id]) for item_id in ids if item_id in self.records]
        elif where:
            def matches(record):
                metadata = record["metadata"]
                for key, expected in where.items():
                    actual = metadata.get(key)
                    if isinstance(expected, dict) and "$in" in expected:
                        if actual not in expected["$in"]:
                            return False
                    elif actual != expected:
                        return False
                return True

            selected = [
                (item_id, record)
                for item_id, record in self.records.items()
                if matches(record)
            ]
        else:
            selected = list(self.records.items())
        if limit is not None:
            selected = selected[:limit]
        return {
            "ids": [item_id for item_id, _ in selected],
            "metadatas": [record["metadata"] for _, record in selected],
            "documents": [record["document"] for _, record in selected],
        }

    def add(self, ids, embeddings, documents, metadatas):
        for item_id in ids:
            if item_id in self.records:
                raise ValueError(f"duplicate id: {item_id}")
        for item_id, document, metadata in zip(ids, documents, metadatas):
            self.records[item_id] = {"document": document, "metadata": metadata}

    def delete(self, ids):
        for item_id in ids:
            self.records.pop(item_id, None)

    def update(self, ids, metadatas):
        for item_id, metadata in zip(ids, metadatas):
            if item_id in self.records:
                self.records[item_id]["metadata"] = metadata


class EncodedVectors:
    def __init__(self, size):
        self.size = size

    def tolist(self):
        return [[0.1] for _ in range(self.size)]


def _patch_vector_store(monkeypatch, tmp_path, *, initialize_manifest=True):
    from enterprise_rag.storage import vector_store

    collection = FakeCollection()
    monkeypatch.setattr(vector_store, "get_kb_collection", lambda: collection)
    from enterprise_rag.storage.kb_manifest import KnowledgeBaseManifest

    manifest = KnowledgeBaseManifest(tmp_path / "kb_manifest.sqlite3")
    if initialize_manifest:
        manifest.mark_initialized()
    monkeypatch.setattr(vector_store, "get_manifest", lambda: manifest)
    monkeypatch.setattr(vector_store, "_use_hybrid", False)
    monkeypatch.setattr(vector_store, "split_by_chapters", lambda content: [("chapter", content)])
    monkeypatch.setattr(
        vector_store,
        "create_parent_child_chunks",
        lambda content, file_id: ([content], [{"parent_id": file_id, "parent_text": content}]),
    )
    monkeypatch.setattr(
        vector_store,
        "get_embedding_model",
        lambda: SimpleNamespace(encode=lambda chunks, **_: EncodedVectors(len(chunks))),
    )
    return vector_store, collection


def test_identical_content_uses_distinct_ids_for_different_filenames(monkeypatch, tmp_path):
    vector_store, collection = _patch_vector_store(monkeypatch, tmp_path)

    assert vector_store.add_document_to_kb("a.txt", "same content") == "added"
    assert vector_store.add_document_to_kb("b.txt", "same content") == "added"

    assert len(collection.records) == 2
    assert {record["metadata"]["source"] for record in collection.records.values()} == {
        "a.txt",
        "b.txt",
    }


def test_concurrent_duplicate_upload_is_idempotent(monkeypatch, tmp_path):
    vector_store, collection = _patch_vector_store(monkeypatch, tmp_path)
    results = []
    errors = []

    def upload():
        try:
            results.append(vector_store.add_document_to_kb("same.txt", "content"))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=upload) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert not errors
    assert sorted(results) == ["added", "skipped"]
    assert len(collection.records) == 1


def test_bm25_rebuilds_only_after_a_mutation(monkeypatch):
    from enterprise_rag.storage import vector_store

    calls = []
    marker = object()
    monkeypatch.setattr(vector_store, "_use_hybrid", True)
    monkeypatch.setattr(vector_store, "_bm25_dirty", True)
    monkeypatch.setattr(
        vector_store,
        "_ensure_manifest_initialized",
        lambda _collection: SimpleNamespace(active_revisions={}),
    )
    monkeypatch.setattr(
        vector_store,
        "rebuild_bm25",
        lambda collection, **_kwargs: calls.append(collection),
    )

    vector_store.ensure_bm25(marker)
    vector_store.ensure_bm25(marker)

    assert calls == [marker]


@pytest.mark.parametrize("operation", ["add", "delete", "clear"])
def test_successful_collection_mutation_survives_bm25_failure(monkeypatch, tmp_path, operation):
    """BM25 是派生索引，重建失败不能改变已提交的 Chroma 操作结果。"""
    vector_store, collection = _patch_vector_store(monkeypatch, tmp_path)
    events = []
    monkeypatch.setattr(vector_store, "_use_hybrid", True)
    monkeypatch.setattr(vector_store, "_bm25_dirty", False)

    def fail_rebuild(_collection, **_kwargs):
        events.append("bm25")
        raise RuntimeError("temporary BM25 failure")

    monkeypatch.setattr(vector_store, "rebuild_bm25", fail_rebuild)

    if operation == "add":
        assert vector_store.add_document_to_kb("manual.txt", "new content") == "added"
        assert collection.records
    else:
        revision_id = "existing-revision"
        content_hash = "existing-content"
        collection.records["existing"] = {
            "document": "existing content",
            "metadata": {
                "source": "manual.txt",
                "revision_id": revision_id,
                "content_hash": content_hash,
                "file_id": content_hash,
            },
        }
        vector_store.get_manifest().commit_source(
            "manual.txt", revision_id, content_hash, 1
        )
        if operation == "delete":
            assert vector_store.delete_document("manual.txt") is True
        else:
            assert vector_store.clear_all_documents() is True
        assert not collection.records

    # manifest 提交本身就是唯一代际切换；派生索引失败时只保留 BM25 dirty。
    assert events == ["bm25"]
    assert vector_store._bm25_dirty is True


def test_document_listing_reads_only_metadata_and_counts_unique_sources(monkeypatch, tmp_path):
    """侧栏文件指标按来源统计，不能把向量分块数误报成文件数。"""
    vector_store, collection = _patch_vector_store(
        monkeypatch, tmp_path, initialize_manifest=False
    )
    collection.records = {
        "a-1": {
            "document": "chunk a-1",
            "metadata": {"source": "a.txt", "content_hash": "a-hash", "file_id": "a-hash"},
        },
        "a-2": {
            "document": "chunk a-2",
            "metadata": {"source": "a.txt", "content_hash": "a-hash", "file_id": "a-hash"},
        },
        "b-1": {
            "document": "chunk b-1",
            "metadata": {"source": "b.txt", "content_hash": "b-hash", "file_id": "b-hash"},
        },
    }
    includes = []
    original_get = collection.get

    def record_get(*args, **kwargs):
        includes.append(kwargs.get("include"))
        return original_get(*args, **kwargs)

    monkeypatch.setattr(collection, "get", record_get)

    assert vector_store.list_documents() == ["a.txt", "b.txt"]
    assert includes[0] == ["metadatas"]
    assert vector_store.get_document_count() == 2


def test_hybrid_search_falls_back_to_bm25_when_vector_search_has_no_candidates(monkeypatch):
    from enterprise_rag.rag import hybrid

    class FakeCollection:
        def query(self, **_kwargs):
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

        def get(self, ids, include):
            assert include == ["documents", "metadatas"]
            return {"ids": ids, "documents": ["entity introduction"], "metadatas": [{"source": "story.txt"}]}

    class FakeBm25:
        def get_scores(self, _tokens):
            return [0.0, 7.0]

    monkeypatch.setattr(
        hybrid,
        "get_embedding_model",
        lambda: SimpleNamespace(
            encode=lambda *_args, **_kwargs: SimpleNamespace(tolist=lambda: [[0.1, 0.2]])
        ),
    )
    monkeypatch.setattr(hybrid, "bm25_index", FakeBm25())
    monkeypatch.setattr(hybrid, "doc_id_list", ["other", "story"])

    result = hybrid.hybrid_search("entity", FakeCollection(), top_k=1)

    assert result["ids"] == ["story"]


def test_hybrid_search_returns_empty_result_for_empty_collection(monkeypatch):
    """空知识库不能把 Chroma 的空 ids 参数错误升级为检索服务异常。"""
    from enterprise_rag.rag import hybrid

    class EmptyCollection:
        def query(self, **_kwargs):
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

        def get(self, **_kwargs):
            raise AssertionError("空候选不应调用 collection.get(ids=[])")

    monkeypatch.setattr(
        hybrid,
        "get_embedding_model",
        lambda: SimpleNamespace(
            encode=lambda *_args, **_kwargs: SimpleNamespace(tolist=lambda: [[0.1, 0.2]])
        ),
    )
    monkeypatch.setattr(hybrid, "bm25_index", None)
    monkeypatch.setattr(hybrid, "doc_id_list", [])
    monkeypatch.setattr(hybrid, "documents_list", [])

    assert hybrid.hybrid_search("任意问题", EmptyCollection()) == {
        "ids": [],
        "documents": [],
        "metadatas": [],
    }


def test_hybrid_search_promotes_exact_entity_mentions(monkeypatch):
    from enterprise_rag.rag import hybrid

    class FakeCollection:
        def query(self, **_kwargs):
            return {
                "ids": [["unrelated"]],
                "documents": [["unrelated text"]],
                "metadatas": [[{"source": "other.txt"}]],
                "distances": [[0.1]],
            }

        def get(self, ids, include):
            assert include == ["documents", "metadatas"]
            return {"ids": ids, "documents": ["孔德草介绍"], "metadatas": [{"source": "story.txt"}]}

    class FakeBm25:
        def get_scores(self, _tokens):
            return [0.0, 0.0]

    monkeypatch.setattr(
        hybrid,
        "get_embedding_model",
        lambda: SimpleNamespace(
            encode=lambda *_args, **_kwargs: SimpleNamespace(tolist=lambda: [[0.1, 0.2]])
        ),
    )
    monkeypatch.setattr(hybrid, "bm25_index", FakeBm25())
    monkeypatch.setattr(hybrid, "doc_id_list", ["unrelated", "story"])
    monkeypatch.setattr(hybrid, "documents_list", ["unrelated text", "孔德草是故事人物"])

    result = hybrid.hybrid_search("孔德草是谁", FakeCollection(), top_k=1)

    assert result["ids"] == ["story"]


def test_retriever_returns_two_values_without_raw_results(monkeypatch):
    from enterprise_rag.rag import retriever

    monkeypatch.setattr(
        retriever,
        "get_search_function",
        lambda: lambda query, n_results: {
            "documents": [["document"]],
            "metadatas": [[{"source": "source.txt"}]],
            "distances": [[0.1]],
        },
    )
    monkeypatch.setattr(
        retriever,
        "get_reranker",
        lambda: SimpleNamespace(predict=lambda pairs: [0.9]),
    )

    result = retriever.retrieve_context("question", return_raw=False)
    assert len(result) == 2


def test_stream_generation_error_propagates(monkeypatch):
    from enterprise_rag.core.exceptions import LLMException
    from enterprise_rag.llm import client

    def fail(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(client, "_call_llm", fail)
    with pytest.raises(LLMException):
        list(client.generate_answer_stream("system", []))


def test_llm_retry_policy_skips_authentication_and_client_errors():
    """401/403/400 属于配置错误，不能触发退避重试。"""
    from openai import AuthenticationError, BadRequestError, PermissionDeniedError
    from enterprise_rag.llm.client import _is_retryable_llm_error

    response = SimpleNamespace(status_code=401, headers={}, request=SimpleNamespace())
    assert _is_retryable_llm_error(AuthenticationError("invalid key", response=response, body=None)) is False

    response.status_code = 400
    assert _is_retryable_llm_error(BadRequestError("bad request", response=response, body=None)) is False

    response.status_code = 403
    assert _is_retryable_llm_error(PermissionDeniedError("forbidden", response=response, body=None)) is False


def test_loader_accepts_common_windows_gbk_text():
    from enterprise_rag.utils.loader import read_file

    class FakeFile:
        name = "legacy.txt"

        def read(self):
            return "中文内容".encode("gbk")

    assert read_file(FakeFile()) == "中文内容"


def test_loader_returns_paragraph_metadata_without_changing_default_contract():
    from enterprise_rag.utils.loader import read_file

    class FakeFile:
        name = "notes.md"

        def read(self):
            return "第一段\n\n第二段".encode("utf-8")

    assert read_file(FakeFile()) == "第一段\n\n第二段"
    parsed = read_file(FakeFile(), return_metadata=True)
    assert parsed["text"] == "第一段\n\n第二段"
    assert parsed["segments"] == [
        {"paragraph": 1, "text": "第一段"},
        {"paragraph": 2, "text": "第二段"},
    ]


def test_loader_rewinds_reused_file_object():
    """同一个上传对象被重复解析时，两次结果必须一致。"""
    from enterprise_rag.utils.loader import read_file

    upload = BytesIO("可重复读取".encode("utf-8"))
    upload.name = "notes.txt"

    assert read_file(upload) == "可重复读取"
    assert read_file(upload) == "可重复读取"


def test_retriever_includes_page_and_paragraph_in_sources(monkeypatch):
    from enterprise_rag.rag import retriever

    monkeypatch.setattr(
        retriever,
        "get_search_function",
        lambda: lambda query, n_results: {
            "documents": [["document"]],
            "metadatas": [[{"source": "manual.pdf", "chapter": "全文", "page": 3, "paragraph": 2}]],
            "distances": [[0.1]],
        },
    )
    monkeypatch.setattr(
        retriever,
        "get_reranker",
        lambda: SimpleNamespace(predict=lambda pairs: [0.9]),
    )

    _, sources, raw = retriever.retrieve_context("question", return_raw=True)
    assert sources == ["manual.pdf | 全文，第 3 页，第 2 段"]
    assert raw[0]["page"] == 3
    assert raw[0]["paragraph"] == 2


def test_chat_message_material_avatars_render():
    from streamlit.testing.v1 import AppTest
    from enterprise_rag.ui.chat_ui import ASSISTANT_AVATAR, USER_AVATAR

    app = AppTest.from_string(
        f'''
import streamlit as st
st.chat_message("user", avatar={USER_AVATAR!r}).write("ok")
st.chat_message("assistant", avatar={ASSISTANT_AVATAR!r}).write("ok")
'''
    ).run()

    assert len(app.exception) == 0


def test_standalone_question_does_not_use_history_rewrite(monkeypatch):
    from enterprise_rag.llm import client

    def unexpected_call(*args, **kwargs):
        raise AssertionError("standalone question should not call the rewrite model")

    monkeypatch.setattr(client, "_call_llm", unexpected_call)
    assert (
        client.rewrite_query(
            "用户：年假制度是什么？\n助手：年假为五天。",
            "程心是一个什么样的人",
        )
        == "程心是一个什么样的人"
    )


@pytest.mark.parametrize(
    "question",
    [
        "程心是谁",
        "云天明是谁",
        "程心是什么人",
        "程心为什么按不下按钮",
        "公司有哪些福利政策？",
        "年假制度是什么？",
        "如何申请报销？",
        "什么是绩效考核？",
    ],
)
def test_explicit_subject_questions_do_not_inherit_previous_topic(monkeypatch, question):
    from enterprise_rag.llm import client

    def unexpected_call(*args, **kwargs):
        raise AssertionError("explicit questions must not be rewritten from history")

    monkeypatch.setattr(client, "_call_llm", unexpected_call)
    history = "用户：年假制度是什么？\n助手：年假为五天。"

    assert client.needs_history_rewrite(history, question) is False
    assert client.rewrite_query(history, question) == question


def test_contextual_follow_up_can_use_history_rewrite(monkeypatch):
    from enterprise_rag.llm import client

    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="程心是谁"))]
    )
    monkeypatch.setattr(client, "_call_llm", lambda *args, **kwargs: response)

    assert client.rewrite_query("用户：介绍程心。", "那她是谁？") == "程心是谁"


def _write_entity_store(monkeypatch, tmp_path):
    from enterprise_rag.services import entity_service

    store = tmp_path / "entity_blocks.json"
    store.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entities": [
                    {
                        "entity_id": "chengxin",
                        "names": ["程心", "Cheng Xin"],
                        "type": "人物",
                        "auto_trigger": True,
                        "blocks": [
                            {
                                "block_id": "chengxin_overview",
                                "level": "overview",
                                "tags": ["身份"],
                                "content": "程心是《三体》系列的重要人物。",
                                "sensitivity": None,
                            },
                            {
                                "block_id": "chengxin_background",
                                "level": "detail",
                                "tags": ["背景"],
                                "content": "她参与过阶梯计划。",
                                "sensitivity": None,
                            },
                            {
                                "block_id": "chengxin_sword",
                                "level": "detail",
                                "tags": ["执剑人", "威慑"],
                                "content": "她曾担任执剑人。",
                                "sensitivity": "spoiler",
                            },
                            {
                                "block_id": "chengxin_luoji_relation",
                                "level": "relation",
                                "tags": ["罗辑", "区别"],
                                "content": "程心与罗辑代表不同的执剑理念。",
                                "sensitivity": "spoiler",
                            },
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(entity_service, "ENTITY_STORE_PATH", store)
    return entity_service


def test_entity_store_follows_runtime_data_directory():
    """结构化实体数据必须与缓存、背景和默认向量库使用同一运行目录。"""
    from enterprise_rag.config import RUNTIME_DATA_DIR
    from enterprise_rag.services import entity_service

    assert entity_service.ENTITY_STORE_PATH == RUNTIME_DATA_DIR / "entity_blocks.json"


def test_entity_intro_selects_overview_and_non_sensitive_detail(monkeypatch, tmp_path):
    entity_service = _write_entity_store(monkeypatch, tmp_path)

    entity = entity_service.find_entity("程心是谁")
    selected = entity_service.select_blocks(entity, "intro", set(), "程心是谁")

    assert [block.block_id for block in selected] == ["chengxin_overview", "chengxin_background"]
    assert all(block.sensitivity is None for block in selected)


def test_entity_detail_uses_unseen_blocks_and_warns_for_spoilers(monkeypatch, tmp_path):
    import streamlit as st

    from enterprise_rag.core import state

    entity_service = _write_entity_store(monkeypatch, tmp_path)
    st.session_state.clear()
    state.init_state()
    st.session_state.entity_coverage = {
        "chengxin": {
            "presented_blocks": ["chengxin_overview", "chengxin_background"],
            "last_intent": "intro",
        }
    }
    st.session_state.active_entity_id = "chengxin"
    monkeypatch.setattr(entity_service, "generate_answer_stream", lambda *_: iter(["整合后的详细介绍。"] ))

    service = entity_service.EntityConversationService()
    plan = service.plan("帮我详细介绍一下")
    output = "".join(service.answer_stream("帮我详细介绍一下", plan))

    assert plan.intent == "detail"
    assert [block.block_id for block in plan.selected_blocks] == ["chengxin_sword", "chengxin_luoji_relation"]
    assert "⚠️ **剧透提醒**" in output
    assert st.session_state.entity_coverage["chengxin"]["presented_blocks"] == [
        "chengxin_overview",
        "chengxin_background",
        "chengxin_sword",
        "chengxin_luoji_relation",
    ]


def test_entity_specific_question_prioritizes_entity_relation_blocks(monkeypatch, tmp_path):
    entity_service = _write_entity_store(monkeypatch, tmp_path)
    entity = entity_service.find_entity("程心和罗辑的区别")
    relation = next(block for block in entity.blocks if block.level == "relation")
    monkeypatch.setattr(entity_service, "rank_blocks", lambda *_args, **_kwargs: (relation,))

    selected = entity_service.select_blocks(entity, "specific", set(), "程心和罗辑的区别")

    assert selected == (relation,)


def test_chat_service_keeps_configured_entity_on_rag_path(monkeypatch, tmp_path):
    import streamlit as st

    from enterprise_rag.core import state
    from enterprise_rag.services import chat_service

    entity_service = _write_entity_store(monkeypatch, tmp_path)
    st.session_state.clear()
    state.init_state()
    service = chat_service.ChatService()
    captured = {}
    service.rag_service = SimpleNamespace(
        answer_stream=lambda query, history, messages: (
            captured.update({"query": query, "history": history, "messages": messages})
            or iter(["RAG回答。"])
        )
    )

    assert entity_service.find_entity("程心是谁") is not None
    assert "RAG回答。" in "".join(service.chat_stream("程心是谁"))
    assert captured["query"] == "程心是谁"
    assert captured["history"] == ""


def test_independent_question_uses_rag_without_old_history(monkeypatch):
    from enterprise_rag.services import chat_service

    messages = [
        {"role": "user", "content": "年假制度是什么？"},
        {"role": "assistant", "content": "年假为五天。"},
        {"role": "user", "content": "程心是一个什么样的人？"},
    ]
    captured = {}
    monkeypatch.setattr(chat_service, "get_state", lambda *_: messages)
    service = chat_service.ChatService()
    service.rag_service = SimpleNamespace(
        answer_stream=lambda query, history, current_messages: (
            captured.update({"query": query, "history": history, "messages": current_messages})
            or iter(["ok"])
        )
    )

    assert "".join(service.chat_stream("程心是一个什么样的人？")) == "ok"
    assert captured["query"] == "程心是一个什么样的人？"
    assert captured["history"] == ""
    assert captured["messages"] == messages


def test_entity_question_routes_to_rag_without_previous_history(monkeypatch):
    from types import SimpleNamespace

    from enterprise_rag.services import chat_service

    messages = [
        {"role": "user", "content": "公司有哪些福利政策？"},
        {"role": "assistant", "content": "知识库中没有找到相关片段。"},
        {"role": "user", "content": "云天明是谁"},
    ]
    captured = {}
    monkeypatch.setattr(chat_service, "get_state", lambda *_: messages)
    service = chat_service.ChatService()
    monkeypatch.setattr(service.entity_service, "plan", lambda _: None)
    service.rag_service = SimpleNamespace(
        answer_stream=lambda query, history, current_messages: (
            captured.update(
                {"query": query, "history": history, "messages": current_messages}
            )
            or iter(["retrieved"])
        )
    )

    assert "".join(service.chat_stream("云天明是谁")) == "retrieved"
    assert captured["query"] == "云天明是谁"
    assert captured["history"] == ""
    assert captured["messages"] == messages


def test_contextual_follow_up_keeps_old_turns_for_rag_rewrite(monkeypatch):
    from enterprise_rag.services import chat_service

    messages = [
        {"role": "user", "content": "介绍程心。"},
        {"role": "assistant", "content": "程心是小说人物。"},
        {"role": "user", "content": "那她是谁？"},
    ]
    captured = {}
    monkeypatch.setattr(chat_service, "get_state", lambda *_: messages)
    service = chat_service.ChatService()
    service.rag_service = SimpleNamespace(
        answer_stream=lambda query, history, current_messages: (
            captured.update({"query": query, "history": history, "messages": current_messages})
            or iter(["ok"])
        )
    )

    assert "".join(service.chat_stream("那她是谁？")) == "ok"
    assert captured["query"] == "那她是谁？"
    assert "介绍程心。" in captured["history"]
    assert captured["messages"] == messages


def test_independent_rag_question_uses_current_question_only(monkeypatch):
    from enterprise_rag.services import rag_service

    class EmptyCache:
        def make_key(self, *_args, **_kwargs):
            return "test-key"

        def get(self, _key, **_kwargs):
            return None

        def set(self, _key, _value, **_kwargs):
            return None

    messages = [
        {"role": "user", "content": "年假制度是什么？"},
        {"role": "assistant", "content": "年假为五天。"},
        {"role": "user", "content": "程心是一个什么样的人？"},
    ]
    captured = []
    monkeypatch.setattr(rag_service, "CacheService", EmptyCache)
    monkeypatch.setattr(rag_service, "retrieve_context", lambda *_args, **_kwargs: ("程心相关内容", ["story.txt"], [{}]))
    monkeypatch.setattr(rag_service, "generate_answer_stream", lambda _, chat_history: captured.append(chat_history) or iter(["程心是小说人物。 "]))

    list(rag_service.RagService().answer_stream("程心是一个什么样的人？", "用户：年假制度是什么？", messages))

    assert captured == [[{"role": "user", "content": "程心是一个什么样的人？"}]]


def test_cache_key_includes_original_question():
    import streamlit as st

    from enterprise_rag.core import state
    from enterprise_rag.services import cache_service
    from enterprise_rag.services.cache_service import CacheService

    st.session_state.clear()
    state.init_state()
    cache = CacheService()
    assert cache.make_key("年假制度是什么", "history", original_query="程心是一个什么样的人") != cache.make_key(
        "年假制度是什么", "history", original_query="年假制度是什么"
    )


def test_cache_key_uses_complete_long_queries():
    """长问题即使前缀相同，也必须生成不同缓存键。"""
    import streamlit as st

    from enterprise_rag.core import state
    from enterprise_rag.services import cache_service
    from enterprise_rag.services.cache_service import CacheService

    st.session_state.clear()
    state.init_state()
    cache = CacheService()
    prefix = "同一前缀" * 100
    first = prefix + "结尾一"
    second = prefix + "结尾二"

    assert cache.make_key(first, "", original_query=first) != cache.make_key(
        second, "", original_query=second
    )

    history_prefix = "历史上下文" * 100
    assert cache.make_key("固定问题", history_prefix + "甲") != cache.make_key(
        "固定问题", history_prefix + "乙"
    )


def test_cache_key_changes_when_kb_directory_changes(monkeypatch, tmp_path):
    """切换向量库目录时必须隔离旧缓存，即使知识库代际相同。"""
    import streamlit as st

    from enterprise_rag.core import state
    from enterprise_rag.services import cache_service

    st.session_state.clear()
    state.init_state()
    monkeypatch.setattr(cache_service, "KB_DATA_DIR", tmp_path / "first-kb")
    first = cache_service.CacheService().make_key("问题", "")
    monkeypatch.setattr(cache_service, "KB_DATA_DIR", tmp_path / "second-kb")
    second = cache_service.CacheService().make_key("问题", "")

    assert first != second


def test_persistent_answer_cache_uses_lru_and_ttl(tmp_path):
    from enterprise_rag.storage.cache import PersistentAnswerCache

    now = [100.0]
    cache = PersistentAnswerCache(
        tmp_path / "answer_cache.sqlite3",
        max_entries=2,
        ttl_seconds=10,
        clock=lambda: now[0],
    )
    cache.set("first", {"content": "one"})
    now[0] = 101.0
    cache.set("second", {"content": "two"})
    now[0] = 102.0
    assert cache.get("first") == {"content": "one"}
    now[0] = 103.0
    cache.set("third", {"content": "three"})

    assert cache.get("second") is None
    assert cache.get("first") == {"content": "one"}
    now[0] = 113.0
    assert cache.get("third") is None


def test_cache_service_reads_persistent_entries_and_invalidates_by_generation(monkeypatch, tmp_path):
    import streamlit as st

    from enterprise_rag.core import state
    from enterprise_rag.services import cache_service
    from enterprise_rag.services.cache_service import CacheService
    from enterprise_rag.storage.cache import PersistentAnswerCache

    st.session_state.clear()
    state.init_state()
    backend = PersistentAnswerCache(tmp_path / "answer_cache.sqlite3")
    from enterprise_rag.storage.kb_manifest import KnowledgeBaseManifest

    manifest = KnowledgeBaseManifest(tmp_path / "manifest.sqlite3")
    manifest.mark_initialized()
    cache = CacheService(persistent_cache=backend, manifest=manifest)
    first_key = cache.make_key("年假制度是什么", "", original_query="年假制度是什么")
    cache.set(first_key, {"content": "五天", "sources": []})
    cache_service._l1_cache.clear()

    assert cache.get(first_key) == {"content": "五天", "sources": []}
    manifest.commit_source("manual.txt", "revision-1", "hash-1", 1)
    assert cache.make_key("年假制度是什么", "", original_query="年假制度是什么") != first_key


def test_cache_hit_is_discarded_if_manifest_changes_during_read(monkeypatch, tmp_path):
    """L2 命中后 manifest 换代时必须视为未命中，不能返回旧答案或回填 L1。"""
    import streamlit as st

    from enterprise_rag.core import state
    from enterprise_rag.services import cache_service
    from enterprise_rag.services.cache_service import CacheService
    from enterprise_rag.storage.cache import PersistentAnswerCache
    from enterprise_rag.storage.kb_manifest import KnowledgeBaseManifest

    st.session_state.clear()
    state.init_state()
    manifest = KnowledgeBaseManifest(tmp_path / "manifest.sqlite3")
    manifest.mark_initialized()
    backend = PersistentAnswerCache(tmp_path / "answer_cache.sqlite3")
    cache = CacheService(persistent_cache=backend, manifest=manifest)
    key = cache.make_key("问题", "", kb_generation=0)
    backend.set(key, {"content": "旧答案"})
    original_get = backend.get

    def changing_get(cache_key):
        value = original_get(cache_key)
        manifest.commit_source("manual.txt", "revision-1", "hash-1", 1)
        return value

    monkeypatch.setattr(backend, "get", changing_get)

    assert cache.get(key, expected_generation=0) is None
    assert key not in cache_service._l1_cache


def test_cache_write_is_not_visible_if_manifest_changes_during_write(monkeypatch, tmp_path):
    """写缓存期间 manifest 换代时清除 L1，并由旧代际 key 隔离已写入的 L2。"""
    import streamlit as st

    from enterprise_rag.core import state
    from enterprise_rag.services import cache_service
    from enterprise_rag.services.cache_service import CacheService
    from enterprise_rag.storage.cache import PersistentAnswerCache
    from enterprise_rag.storage.kb_manifest import KnowledgeBaseManifest

    st.session_state.clear()
    state.init_state()
    manifest = KnowledgeBaseManifest(tmp_path / "manifest.sqlite3")
    manifest.mark_initialized()
    backend = PersistentAnswerCache(tmp_path / "answer_cache.sqlite3")
    cache = CacheService(persistent_cache=backend, manifest=manifest)
    key = cache.make_key("问题", "", kb_generation=0)
    original_set = backend.set

    def changing_set(cache_key, value):
        original_set(cache_key, value)
        manifest.commit_source("manual.txt", "revision-1", "hash-1", 1)

    monkeypatch.setattr(backend, "set", changing_set)

    assert cache.set(key, {"content": "旧答案"}, expected_generation=0) is False
    assert key not in cache_service._l1_cache
    assert cache.make_key("问题", "") != key


def test_cache_write_failure_removes_uncommitted_l1_entry(monkeypatch, tmp_path):
    """L2 写入失败时不能只留下本进程可见的 L1 条目。"""
    import streamlit as st

    from enterprise_rag.core import state
    from enterprise_rag.services import cache_service
    from enterprise_rag.services.cache_service import CacheService
    from enterprise_rag.storage.cache import PersistentAnswerCache
    from enterprise_rag.storage.kb_manifest import KnowledgeBaseManifest

    st.session_state.clear()
    state.init_state()
    manifest = KnowledgeBaseManifest(tmp_path / "manifest.sqlite3")
    manifest.mark_initialized()
    backend = PersistentAnswerCache(tmp_path / "answer_cache.sqlite3")
    cache = CacheService(persistent_cache=backend, manifest=manifest)
    key = cache.make_key("问题", "", kb_generation=0)
    monkeypatch.setattr(
        backend,
        "set",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    assert cache.set(key, {"content": "答案"}, expected_generation=0) is False
    assert key not in cache_service._l1_cache


def test_rerank_threshold_is_an_absolute_floor(monkeypatch):
    from enterprise_rag.rag import retriever

    monkeypatch.setattr(retriever, "RERANK_SCORE_THRESHOLD", 0.5)

    assert retriever.calculate_rerank_threshold([0.8, 0.7, 0.4]) == pytest.approx(0.5)
    assert retriever.calculate_rerank_threshold([0.45, 0.4]) == pytest.approx(0.5)
    assert retriever.calculate_rerank_threshold([0.2]) == pytest.approx(0.5)


def test_retriever_deduplicates_parent_context(monkeypatch):
    from enterprise_rag.rag import retriever

    monkeypatch.setattr(
        retriever,
        "get_search_function",
        lambda: lambda query, n_results: {
            "documents": [["child-a", "child-b", "child-c"]],
            "metadatas": [[
                {"source": "manual.txt", "parent_id": "parent-1", "parent_text": "shared parent"},
                {"source": "manual.txt", "parent_id": "parent-1", "parent_text": "shared parent"},
                {"source": "manual.txt", "parent_id": "parent-2", "parent_text": "other parent"},
            ]],
            "distances": [[0.1, 0.2, 0.3]],
        },
    )
    monkeypatch.setattr(
        retriever,
        "get_reranker",
        lambda: SimpleNamespace(predict=lambda pairs: [0.95, 0.9, 0.8]),
    )

    context, sources, raw = retriever.retrieve_context("question", return_raw=True)

    assert len(raw) == 2
    assert [item["parent_id"] for item in raw] == ["parent-1", "parent-2"]
    assert context.count("shared parent") == 1


def test_retriever_rejects_candidates_below_absolute_threshold(monkeypatch):
    """所有重排分数低于主阈值时，严格模式必须返回空结果。"""
    from enterprise_rag.rag import retriever

    monkeypatch.setattr(retriever, "RERANK_SCORE_THRESHOLD", 0.5)
    monkeypatch.setattr(
        retriever,
        "get_search_function",
        lambda: lambda query, n_results: {
            "documents": [["weak-a", "weak-b"]],
            "metadatas": [[{"source": "manual.txt"}, {"source": "manual.txt"}]],
            "distances": [[0.1, 0.2]],
        },
    )
    monkeypatch.setattr(
        retriever,
        "get_reranker",
        lambda: SimpleNamespace(predict=lambda pairs: [0.49, 0.35]),
    )

    assert retriever.retrieve_context("question", return_raw=True) == ("", [], [])


def test_grounded_stream_interruption_persists_the_same_visible_content(monkeypatch):
    """流式中断后，UI 收到的文本和服务元数据必须完全一致。"""
    from enterprise_rag.services import rag_service

    decision = rag_service.RetrievalDecision(
        query="问题",
        retrieval_query="问题",
        context="资料",
        sources=["manual.txt | 全文"],
        raw_results=[{"rerank_score": 0.9}],
        history_summary="",
    )

    class EmptyCache:
        def make_key(self, *_args, **_kwargs):
            return "key"

        def get(self, _key, **_kwargs):
            return None

        def set(self, *_args, **_kwargs):
            raise AssertionError("中断回答不能写缓存")

    def broken_stream(*_args, **_kwargs):
        yield "回答前半段"
        raise RuntimeError("network down")

    monkeypatch.setattr(rag_service, "generate_answer_stream", broken_stream)
    service = rag_service.RagService()
    service.cache_service = EmptyCache()

    visible = "".join(
        service._stream_from_decision(
            decision,
            template="{context}",
            mode="rag",
        )
    )

    assert visible == service._last_meta["content"]
    assert service._last_meta["sources"] == []
    assert service._last_meta["is_interrupted"] is True


def test_grounded_generation_change_skips_cache_write(monkeypatch):
    """模型生成期间知识库代际变化时，旧上下文回答只能展示，不能写入新缓存。"""
    from enterprise_rag.services import rag_service

    decision = rag_service.RetrievalDecision(
        query="问题",
        retrieval_query="问题",
        context="旧版资料",
        sources=["manual.txt | 全文"],
        raw_results=[{"rerank_score": 0.9}],
        history_summary="",
        kb_generation=7,
    )
    generation_reads = iter([7, 8])

    class ChangingCache:
        def get_generation(self):
            return next(generation_reads)

        def make_key(self, *_args, **kwargs):
            assert kwargs["kb_generation"] == 7
            return "old-snapshot-key"

        def get(self, _key, **_kwargs):
            return None

        def set(self, *_args, **_kwargs):
            raise AssertionError("代际已变化的回答不得写入缓存")

    monkeypatch.setattr(
        rag_service,
        "generate_answer_stream",
        lambda *_args, **_kwargs: iter(["基于旧快照生成的回答"]),
    )
    service = rag_service.RagService()
    service.cache_service = ChangingCache()

    visible = "".join(
        service._stream_from_decision(decision, template="{context}", mode="rag")
    )

    assert visible == "基于旧快照生成的回答"
    assert service._last_meta["content"] == visible
    assert service._last_meta["cache_skipped_generation_changed"] is True


def test_grounded_interruption_preserves_trailing_whitespace(monkeypatch):
    """中断前已显示的尾随空白不能在元数据中被静默裁掉。"""
    from enterprise_rag.services import rag_service

    decision = rag_service.RetrievalDecision(
        query="问题",
        retrieval_query="问题",
        context="资料",
        sources=["manual.txt | 全文"],
        raw_results=[{"rerank_score": 0.9}],
        history_summary="",
    )

    class EmptyCache:
        def make_key(self, *_args, **_kwargs):
            return "key"

        def get(self, _key, **_kwargs):
            return None

        def set(self, *_args, **_kwargs):
            raise AssertionError("中断回答不能写缓存")

    def broken_stream(*_args, **_kwargs):
        yield "回答前半段  "
        raise RuntimeError("network down")

    monkeypatch.setattr(rag_service, "generate_answer_stream", broken_stream)
    service = rag_service.RagService()
    service.cache_service = EmptyCache()

    visible = "".join(service._stream_from_decision(decision, template="{context}", mode="rag"))

    assert visible == service._last_meta["content"]
    assert visible.startswith("回答前半段  \n\n---")


def test_general_stream_interruption_persists_the_same_visible_content(monkeypatch):
    """G2 中途失败时，保留已显示正文并追加中断标记，不得用错误文本覆盖。"""
    from enterprise_rag.services import rag_service

    def broken_stream(*_args, **_kwargs):
        yield "通用回答前半段 "
        raise RuntimeError("network down")

    monkeypatch.setattr(rag_service, "generate_answer_stream", broken_stream)
    service = rag_service.RagService()
    monkeypatch.setattr(
        service.cache_service,
        "set",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("通用回答不得写入 RAG 缓存")
        ),
    )

    visible = "".join(service.general_stream("写一封请假邮件"))

    assert visible == service._last_meta["content"]
    assert visible.startswith("通用回答前半段 \n\n---")
    assert "通用回答生成中断" in visible
    assert service._last_meta["sources"] == []
    assert service._last_meta["is_general"] is True
    assert service._last_meta["is_interrupted"] is True


def test_grounded_empty_stream_yields_and_records_the_same_fallback(monkeypatch):
    """知识库生成空流时，服务层必须向 UI 发送并记录同一兜底正文。"""
    from enterprise_rag.services import rag_service

    decision = rag_service.RetrievalDecision(
        query="问题",
        retrieval_query="问题",
        context="资料",
        sources=["manual.txt | 全文"],
        raw_results=[{"rerank_score": 0.9}],
        history_summary="",
    )

    class EmptyCache:
        def make_key(self, *_args, **_kwargs):
            return "key"

        def get(self, _key, **_kwargs):
            return None

        def set(self, _key, _value, **_kwargs):
            return None

    monkeypatch.setattr(rag_service, "generate_answer_stream", lambda *_args, **_kwargs: iter(()))
    service = rag_service.RagService()
    service.cache_service = EmptyCache()

    visible = "".join(service._stream_from_decision(decision, template="{context}", mode="rag"))

    assert visible == "抱歉，未能生成有效回答，请重新提问。"
    assert service._last_meta["content"] == visible
    assert service._last_meta["sources"] == []
    assert service._last_meta["is_empty"] is True


def test_grounded_whitespace_only_stream_is_not_cached(monkeypatch):
    """仅空白的模型流应视为空响应，不能带来源或进入答案缓存。"""
    from enterprise_rag.services import rag_service

    decision = rag_service.RetrievalDecision(
        query="问题",
        retrieval_query="问题",
        context="资料",
        sources=["manual.txt | 全文"],
        raw_results=[{"rerank_score": 0.9}],
        history_summary="",
    )

    class RecordingCache:
        def __init__(self):
            self.writes = []

        def make_key(self, *_args, **_kwargs):
            return "key"

        def get(self, _key, **_kwargs):
            return None

        def set(self, key, value, **_kwargs):
            self.writes.append((key, value))

    monkeypatch.setattr(rag_service, "generate_answer_stream", lambda *_args, **_kwargs: iter(["   "]))
    service = rag_service.RagService()
    service.cache_service = RecordingCache()

    visible = "".join(service._stream_from_decision(decision, template="{context}", mode="rag"))

    assert visible == "抱歉，未能生成有效回答，请重新提问。"
    assert visible == service._last_meta["content"]
    assert service._last_meta["sources"] == []
    assert service.cache_service.writes == []


def test_general_empty_stream_yields_and_records_the_same_fallback(monkeypatch):
    """G2 空流同样由服务层统一生成可审计的兜底正文。"""
    from enterprise_rag.services import rag_service

    monkeypatch.setattr(rag_service, "generate_answer_stream", lambda *_args, **_kwargs: iter(()))
    service = rag_service.RagService()

    visible = "".join(service.general_stream("写一封邮件"))

    assert visible == "抱歉，通用办公回答未能生成有效内容，请稍后重试。"
    assert service._last_meta["content"] == visible
    assert service._last_meta["is_general"] is True


def test_general_authentication_failure_returns_actionable_message(monkeypatch):
    """模型返回 401 时应提示检查 DashScope Key，并记录可审计错误码。"""
    from enterprise_rag.services import rag_service

    class AuthenticationFailure(Exception):
        status_code = 401
        code = "invalid_api_key"

    def failing_stream(*_args, **_kwargs):
        raise AuthenticationFailure("invalid key")
        yield  # pragma: no cover - 保持生成器接口

    monkeypatch.setattr(rag_service, "generate_answer_stream", failing_stream)
    service = rag_service.RagService()

    visible = "".join(service.general_stream("写一封邮件"))

    assert "鉴权失败" in visible
    assert "DashScope API Key" in visible
    assert service._last_meta["action_failed"] is True
    assert service._last_meta["error_code"] == "authentication"


def test_general_whitespace_only_stream_returns_one_fallback(monkeypatch):
    """G2 仅空白流不得把空白前缀写入实时正文或历史。"""
    from enterprise_rag.services import rag_service

    monkeypatch.setattr(rag_service, "generate_answer_stream", lambda *_args, **_kwargs: iter(["   "]))
    service = rag_service.RagService()

    visible = "".join(service.general_stream("写一封邮件"))

    assert visible == "抱歉，通用办公回答未能生成有效内容，请稍后重试。"
    assert visible == service._last_meta["content"]
    assert service._last_meta["is_empty"] is True


def test_disabled_draft_resets_previous_metadata(monkeypatch):
    from enterprise_rag.services import rag_service

    monkeypatch.setattr(rag_service, "DRAFT_ENABLED", False)
    service = rag_service.RagService()
    service._last_meta = {"content": "旧回答", "sources": ["old.txt"]}

    assert "".join(service.draft_stream("起草", "资料")) == "基于资料起草功能当前未启用。"
    assert service._last_meta["sources"] == []
    assert service._last_meta["draft_allowed"] is False


def _png_bytes(color):
    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (8, 8), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


class FakeUpload:
    def __init__(self, name, data):
        self.name = name
        self._data = data

    def getvalue(self):
        return self._data


def _patch_background_paths(monkeypatch, tmp_path):
    from enterprise_rag.services import background_service

    data_dir = tmp_path / "data"
    monkeypatch.setattr(background_service, "DATA_DIR", data_dir)
    monkeypatch.setattr(background_service, "BACKGROUND_DIR", data_dir / "backgrounds")
    monkeypatch.setattr(background_service, "BACKGROUND_CONFIG", data_dir / "background.json")
    return background_service


def test_background_image_is_saved_in_data_folder(monkeypatch, tmp_path):
    background_service = _patch_background_paths(monkeypatch, tmp_path)

    name = background_service.save_background(FakeUpload("page.png", _png_bytes("red")))

    assert name.startswith("background_")
    assert (tmp_path / "data" / "backgrounds" / name).is_file()
    assert background_service.get_current_background_name() == name
    assert background_service.get_background_data_url().startswith("data:image/png;base64,")


def test_background_rejects_non_image_content(monkeypatch, tmp_path):
    background_service = _patch_background_paths(monkeypatch, tmp_path)

    with pytest.raises(background_service.BackgroundImageError):
        background_service.save_background(FakeUpload("not-image.png", b"not an image"))


def test_background_rejects_excessive_pixel_dimensions(monkeypatch, tmp_path):
    from PIL import Image

    background_service = _patch_background_paths(monkeypatch, tmp_path)
    buffer = BytesIO()
    Image.new("RGB", (5000, 1), color="red").save(buffer, format="PNG")

    with pytest.raises(background_service.BackgroundImageError, match="分辨率过大"):
        background_service.save_background(FakeUpload("wide.png", buffer.getvalue()))


def test_concurrent_background_saves_leave_one_valid_active_file(monkeypatch, tmp_path):
    background_service = _patch_background_paths(monkeypatch, tmp_path)
    names = []
    errors = []

    def save(color):
        try:
            names.append(
                background_service.save_background(FakeUpload(f"{color}.png", _png_bytes(color)))
            )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=save, args=(color,)) for color in ("red", "blue")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    active_name = background_service.get_current_background_name()
    assert not errors
    assert active_name in names
    assert (tmp_path / "data" / "backgrounds" / active_name).is_file()
