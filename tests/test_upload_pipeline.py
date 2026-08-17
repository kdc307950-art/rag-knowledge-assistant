"""文档上传管线的并发、清理与批量索引回归测试。"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import Future
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace


class Upload(BytesIO):
    """模拟 UploadedFile，并禁止旧实现通过 getvalue 整份复制。"""

    def __init__(self, name: str, data: bytes):
        super().__init__(data)
        self.name = name
        self.size = len(data)

    def getvalue(self):  # pragma: no cover - 被调用即应失败
        raise AssertionError("后台上传不应调用 getvalue() 复制整份文件")


class FakeSlots:
    def __init__(self, accepted: bool = True):
        self.accepted = accepted
        self.releases = 0

    def acquire(self, blocking=False):
        assert blocking is False
        return self.accepted

    def release(self):
        self.releases += 1


def test_multi_file_upload_is_staged_and_queued_without_full_memory_copy(
    monkeypatch, tmp_path
):
    """多个小文件也必须立即入队，不在 Streamlit 主线程解析。"""
    from enterprise_rag.services import document_service

    captured = {}
    slots = FakeSlots()
    monkeypatch.setattr(document_service, "UPLOAD_STAGING_DIR", tmp_path / "staging")
    monkeypatch.setattr(document_service, "_UPLOAD_SLOTS", slots)
    monkeypatch.setattr(document_service, "cleanup_stale_staging_files", lambda: None)
    monkeypatch.setattr(document_service, "begin_batch_index_updates", lambda: None)
    monkeypatch.setattr(
        document_service,
        "finalize_batch_index_updates",
        lambda *, mutated: None,
    )
    monkeypatch.setattr(
        document_service,
        "_register_upload_task",
        lambda task_id, info: captured.update(task_id=task_id, task=info),
    )
    monkeypatch.setattr(
        document_service,
        "_submit_upload_task",
        lambda task_id, files, staging_dir: (
            captured.update(
                submitted=task_id,
                files=files,
                staging_dir=staging_dir,
            )
            or object()
        ),
    )

    result = document_service.DocumentService().process_uploads(
        [Upload("a.txt", b"alpha"), Upload("b.txt", b"beta")]
    )

    assert result["success"] is True
    assert result["async_launched"] == 1
    assert captured["submitted"] == captured["task_id"]
    assert [Path(item["path"]).read_bytes() for item in captured["files"]] == [
        b"alpha",
        b"beta",
    ]
    assert slots.releases == 0


def test_full_upload_queue_rejects_before_creating_staging_files(monkeypatch, tmp_path):
    """队列达到上限时立即拒绝，不继续占用磁盘和内存。"""
    from enterprise_rag.services import document_service

    monkeypatch.setattr(document_service, "UPLOAD_STAGING_DIR", tmp_path / "staging")
    monkeypatch.setattr(document_service, "_UPLOAD_SLOTS", FakeSlots(accepted=False))

    result = document_service.DocumentService().process_uploads(
        [Upload("queued.txt", b"content")]
    )

    assert result["success"] is False
    assert result["busy"] is True
    staging_root = tmp_path / "staging"
    assert not staging_root.exists() or not any(staging_root.iterdir())


def test_parallel_parser_never_exceeds_configured_worker_limit(monkeypatch):
    """解析应真实重叠，但同时执行数不得超过配置上限。"""
    from enterprise_rag.services import document_service

    active = 0
    maximum = 0
    lock = threading.Lock()
    two_workers_started = threading.Event()

    def parse(item):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            if active >= 2:
                two_workers_started.set()
        two_workers_started.wait(timeout=0.5)
        time.sleep(0.01)
        with lock:
            active -= 1
        return {"name": item["name"], "content": "text", "segments": []}

    monkeypatch.setattr(document_service, "UPLOAD_PARSE_WORKERS", 2)
    monkeypatch.setattr(document_service, "_parse_staged_file", parse)

    results = document_service._parse_files_bounded(
        [{"name": f"{index}.txt", "path": "unused"} for index in range(6)]
    )

    assert len(results) == 6
    assert maximum == 2


def test_streaming_parser_keeps_only_worker_window_of_completed_bodies(monkeypatch):
    """消费者未取走结果前，不得继续解析整批文件并把所有正文堆在内存中。"""
    from enterprise_rag.services import document_service

    release_first = threading.Event()
    started = []
    lock = threading.Lock()

    def parse(item):
        with lock:
            started.append(item["name"])
        if item["name"] == "0.txt":
            release_first.wait(timeout=1)
        return {"name": item["name"], "content": item["name"], "segments": []}

    monkeypatch.setattr(document_service, "UPLOAD_PARSE_WORKERS", 2)
    monkeypatch.setattr(document_service, "_parse_staged_file", parse)
    iterator = document_service._iter_parsed_files_bounded(
        [{"name": f"{index}.txt", "path": "unused"} for index in range(6)]
    )

    first_result = []
    consumer = threading.Thread(target=lambda: first_result.append(next(iterator)))
    consumer.start()
    time.sleep(0.05)
    with lock:
        assert set(started).issubset({"0.txt", "1.txt"})
        assert len(started) <= 2

    release_first.set()
    consumer.join(timeout=2)
    assert not consumer.is_alive()
    assert first_result[0]["name"] == "0.txt"
    assert len(list(iterator)) == 5


def test_worker_writes_serially_refreshes_once_and_cleans_staging(monkeypatch, tmp_path):
    """解析结果可并行准备，Chroma 入库顺序和批次刷新必须可预期。"""
    from enterprise_rag.services import document_service

    staging = tmp_path / "task"
    staging.mkdir()
    files = []
    for name in ("a.txt", "b.txt", "c.txt"):
        path = staging / name
        path.write_text(name, encoding="utf-8")
        files.append({"name": name, "path": str(path), "size": path.stat().st_size})

    events = []
    monkeypatch.setattr(
        document_service,
        "_iter_parsed_files_bounded",
        lambda items, progress_callback=None: iter([
            {"name": item["name"], "content": item["name"], "segments": []}
            for item in items
        ]),
    )
    monkeypatch.setattr(
        document_service,
        "begin_batch_index_updates",
        lambda: events.append("begin"),
    )

    def add(name, _content, source_segments=None, refresh_indexes=True):
        events.append(("add", name, refresh_indexes))
        return "skipped" if name == "b.txt" else "added"

    monkeypatch.setattr(document_service, "add_document_to_kb", add)
    monkeypatch.setattr(
        document_service,
        "finalize_batch_index_updates",
        lambda *, mutated: events.append(("finalize", mutated)),
    )
    monkeypatch.setattr(document_service, "get_document_count", lambda: 2)
    monkeypatch.setattr(document_service, "get_doc_count", lambda: 8)

    document_service._upload_worker("task", files, str(staging))

    assert events == [
        "begin",
        ("add", "a.txt", False),
        ("add", "b.txt", False),
        ("add", "c.txt", False),
        ("finalize", True),
    ]
    assert not staging.exists()
    task = document_service._UPLOAD_TASKS["task"]
    assert task["status"] == "done"
    assert task["added_count"] == 2
    assert task["skipped_count"] == 1
    assert set(task["timings"]) == {"parse", "index", "refresh", "total"}


def test_worker_isolates_file_failure_and_always_cleans_staging(monkeypatch, tmp_path):
    """一个文件失败不得取消同批其他文件，暂存目录也不得残留。"""
    from enterprise_rag.services import document_service

    staging = tmp_path / "failed-task"
    staging.mkdir()
    monkeypatch.setattr(
        document_service,
        "_iter_parsed_files_bounded",
        lambda _items, progress_callback=None: iter([
            {"name": "bad.txt", "error": "解析失败"},
            {"name": "good.txt", "content": "ok", "segments": []},
        ]),
    )
    monkeypatch.setattr(document_service, "begin_batch_index_updates", lambda: None)
    monkeypatch.setattr(
        document_service,
        "add_document_to_kb",
        lambda *_args, **_kwargs: "added",
    )
    finalized = []
    monkeypatch.setattr(
        document_service,
        "finalize_batch_index_updates",
        lambda *, mutated: finalized.append(mutated),
    )
    monkeypatch.setattr(document_service, "get_document_count", lambda: 1)
    monkeypatch.setattr(document_service, "get_doc_count", lambda: 1)

    document_service._upload_worker(
        "failed-task",
        [
            {"name": "bad.txt", "path": "unused"},
            {"name": "good.txt", "path": "unused"},
        ],
        str(staging),
    )

    task = document_service._UPLOAD_TASKS["failed-task"]
    assert task["status"] == "partial"
    assert task["added_count"] == 1
    assert task["fail_list"] == ["bad.txt: 解析失败"]
    assert finalized == [True]
    assert not staging.exists()


def test_concurrent_batches_coalesce_bm25_and_cache_refresh(monkeypatch):
    """多个后台批次重叠时，最后一个结束者才执行一次全库刷新。"""
    from enterprise_rag.storage import vector_store

    marker = object()
    events = []
    monkeypatch.setattr(vector_store, "_batch_update_depth", 0)
    monkeypatch.setattr(vector_store, "_batch_mutated", False)
    monkeypatch.setattr(vector_store, "_bm25_dirty", False)
    monkeypatch.setattr(vector_store, "_use_hybrid", True)
    monkeypatch.setattr(vector_store, "get_kb_collection", lambda: marker)
    monkeypatch.setattr(
        vector_store,
        "_ensure_manifest_initialized",
        lambda _collection: SimpleNamespace(active_revisions={}),
    )
    monkeypatch.setattr(
        vector_store,
        "rebuild_bm25",
        lambda collection, **_kwargs: events.append(("bm25", collection)),
    )

    vector_store.begin_batch_index_updates()
    vector_store.begin_batch_index_updates()
    vector_store.finalize_batch_index_updates(mutated=True)
    assert events == []
    vector_store.finalize_batch_index_updates(mutated=True)

    assert events == [("bm25", marker)]


def test_parent_ids_are_unique_across_chapters_and_location_is_cached(monkeypatch, tmp_path):
    """不同章节不得共用 parent_id，同父块也不应重复扫描页码元数据。"""
    from enterprise_rag.storage import vector_store

    class Collection:
        def __init__(self):
            self.metadatas = []
            self.records = {}

        def get(self, where=None, ids=None, **_kwargs):
            selected = list(self.records.items())
            if ids is not None:
                selected = [(key, value) for key, value in selected if key in ids]
            if where:
                selected = [
                    (key, value)
                    for key, value in selected
                    if all(value["metadata"].get(field) == expected for field, expected in where.items())
                ]
            return {
                "ids": [key for key, _value in selected],
                "metadatas": [value["metadata"] for _key, value in selected],
                "documents": [value["document"] for _key, value in selected],
            }

        def add(self, ids, embeddings, documents, metadatas):
            self.metadatas.extend(metadatas)
            for key, document, metadata in zip(ids, documents, metadatas):
                self.records[key] = {"document": document, "metadata": metadata}

        def delete(self, ids):
            for key in ids:
                self.records.pop(key, None)

        def update(self, ids, metadatas):
            for key, metadata in zip(ids, metadatas):
                self.records[key]["metadata"] = metadata

    collection = Collection()
    from enterprise_rag.storage.kb_manifest import KnowledgeBaseManifest

    manifest = KnowledgeBaseManifest(tmp_path / "manifest.sqlite3")
    location_calls = []
    monkeypatch.setattr(vector_store, "get_kb_collection", lambda: collection)
    monkeypatch.setattr(vector_store, "get_manifest", lambda: manifest)
    monkeypatch.setattr(
        vector_store,
        "split_by_chapters",
        lambda _content: [("第一章", "A"), ("第二章", "B")],
    )
    monkeypatch.setattr(
        vector_store,
        "create_parent_child_chunks",
        lambda content, file_id: (
            [f"{content}-1", f"{content}-2"],
            [
                {"parent_id": f"{file_id}_parent_0", "parent_text": content},
                {"parent_id": f"{file_id}_parent_0", "parent_text": content},
            ],
        ),
    )
    monkeypatch.setattr(
        vector_store,
        "_source_location",
        lambda parent, _segments: location_calls.append(parent) or {"page": 1},
    )
    monkeypatch.setattr(
        vector_store,
        "get_embedding_model",
        lambda: type(
            "Embedding",
            (),
            {
                "encode": staticmethod(
                    lambda chunks, **_kwargs: type(
                        "Vectors", (), {"tolist": lambda self: [[0.1] for _ in chunks]}
                    )()
                )
            },
        )(),
    )

    assert (
        vector_store.add_document_to_kb(
            "chapters.txt", "content", refresh_indexes=False
        )
        == "added"
    )

    parent_ids = [metadata["parent_id"] for metadata in collection.metadatas]
    assert parent_ids[0] == parent_ids[1]
    assert parent_ids[2] == parent_ids[3]
    assert parent_ids[0] != parent_ids[2]
    assert location_calls == ["A", "B"]


def test_stale_cleanup_never_deletes_active_task_directory(monkeypatch, tmp_path):
    """即使目录时间已超过 TTL，pending/processing 任务也必须保留原文件。"""
    from enterprise_rag.services import document_service

    staging_root = tmp_path / "staging"
    active = staging_root / "active-task"
    stale = staging_root / "stale-task"
    active.mkdir(parents=True)
    stale.mkdir()
    old_time = time.time() - 3600
    active.touch()
    stale.touch()
    import os

    os.utime(active, (old_time, old_time))
    os.utime(stale, (old_time, old_time))
    monkeypatch.setattr(document_service, "UPLOAD_STAGING_DIR", staging_root)
    monkeypatch.setattr(document_service, "UPLOAD_STAGING_TTL_SECONDS", 60)
    monkeypatch.setattr(
        document_service,
        "_UPLOAD_TASKS",
        {"active-task": {"status": "processing"}},
    )
    monkeypatch.setattr(document_service, "_UPLOAD_FUTURES", {})

    document_service.cleanup_stale_staging_files(now=time.time())

    assert active.exists()
    assert not stale.exists()


def test_submit_failure_releases_slot_and_marks_registered_task_error(monkeypatch, tmp_path):
    """执行器拒绝任务时不能留下队列槽位或永久 pending 状态。"""
    from enterprise_rag.services import document_service

    slots = FakeSlots()
    tasks = {}
    monkeypatch.setattr(document_service, "UPLOAD_STAGING_DIR", tmp_path / "staging")
    monkeypatch.setattr(document_service, "_UPLOAD_SLOTS", slots)
    monkeypatch.setattr(document_service, "_UPLOAD_TASKS", tasks)
    monkeypatch.setattr(document_service, "cleanup_stale_staging_files", lambda: None)
    monkeypatch.setattr(document_service, "begin_batch_index_updates", lambda: None)
    finalized = []
    monkeypatch.setattr(
        document_service,
        "finalize_batch_index_updates",
        lambda *, mutated: finalized.append(mutated),
    )
    monkeypatch.setattr(
        document_service,
        "_register_upload_task",
        lambda task_id, info: document_service._set_upload_task(task_id, info),
    )
    monkeypatch.setattr(
        document_service,
        "_submit_upload_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("executor stopped")),
    )

    result = document_service.DocumentService().process_uploads(
        [Upload("a.txt", b"content")]
    )

    assert result["success"] is False
    assert slots.releases == 1
    assert finalized == [False]
    task = next(iter(tasks.values()))
    assert task["status"] == "error"
    staging_root = tmp_path / "staging"
    assert not staging_root.exists() or not any(staging_root.iterdir())


def test_task_history_cleanup_preserves_terminal_task_with_running_future(monkeypatch):
    """worker 已写终态、但 Future 回调尚未执行时不能淘汰任务快照。"""
    from enterprise_rag.services import document_service

    in_flight = Future()
    tasks = {
        "in-flight": {"status": "done", "updated_at": 1},
        "old": {"status": "done", "updated_at": 2},
    }
    monkeypatch.setattr(document_service, "_UPLOAD_TASKS", tasks)
    monkeypatch.setattr(document_service, "_UPLOAD_FUTURES", {"in-flight": in_flight})

    document_service.DocumentService().clear_finished_tasks(keep_last=0)

    assert "in-flight" in tasks
    assert "old" not in tasks


def test_task_polling_preserves_current_terminal_task_during_history_cleanup(monkeypatch):
    """轮询最旧终态任务时，清理历史也必须先返回它而不是 404。"""
    from backend.api import upload as upload_api
    from enterprise_rag.services import document_service

    current_task_id = "current"
    tasks = {
        current_task_id: {"status": "done", "updated_at": 0},
        **{
            f"finished-{index}": {"status": "done", "updated_at": index}
            for index in range(1, 21)
        },
    }
    monkeypatch.setattr(document_service, "_UPLOAD_TASKS", tasks)
    monkeypatch.setattr(document_service, "_UPLOAD_FUTURES", {})

    response = asyncio.run(upload_api.get_task(current_task_id))

    assert response["status"] == "done"
    assert current_task_id in tasks
    assert "finished-1" not in tasks
    assert len(tasks) == 20


def test_staging_enforces_actual_bytes_not_only_declared_size(monkeypatch, tmp_path):
    """客户端声明的 size 偏小时，实际流入字节仍必须触发单文件上限。"""
    from enterprise_rag.services import document_service

    upload = Upload("large.txt", b"0123456789")
    upload.size = 1
    monkeypatch.setattr(document_service, "MAX_FILE_SIZE_MB", 0)
    destination = tmp_path / "large.txt"

    try:
        document_service._stream_upload_to_path(upload, destination, max_bytes=5)
    except ValueError as exc:
        assert "实际大小超过允许上限" in str(exc)
    else:  # pragma: no cover - 未抛错即为安全回归
        raise AssertionError("实际字节超限时必须拒绝")


def test_real_concurrent_batches_refresh_only_after_both_finish(monkeypatch):
    """两个真实线程重叠完成时，只允许最后一个结束者刷新一次。"""
    from enterprise_rag.storage import vector_store

    marker = object()
    events = []
    barrier = threading.Barrier(2)
    monkeypatch.setattr(vector_store, "_batch_update_depth", 0)
    monkeypatch.setattr(vector_store, "_batch_mutated", False)
    monkeypatch.setattr(vector_store, "_destructive_mutation", False)
    monkeypatch.setattr(vector_store, "get_kb_collection", lambda: marker)
    monkeypatch.setattr(
        vector_store,
        "_refresh_indexes_after_mutation",
        lambda collection: events.append(collection),
    )

    def finish_batch():
        vector_store.begin_batch_index_updates()
        barrier.wait(timeout=2)
        vector_store.finalize_batch_index_updates(mutated=True)

    threads = [threading.Thread(target=finish_batch) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads)
    assert events == [marker]


def test_destructive_operation_is_rejected_while_upload_batch_is_active(monkeypatch):
    """跨会话清空不能与正在执行的上传批次交错。"""
    from enterprise_rag.storage import vector_store

    monkeypatch.setattr(vector_store, "_batch_update_depth", 0)
    monkeypatch.setattr(vector_store, "_batch_mutated", False)
    monkeypatch.setattr(vector_store, "_destructive_mutation", False)
    vector_store.begin_batch_index_updates()
    try:
        assert vector_store.clear_all_documents() is False
        assert vector_store.delete_document("a.txt") is False
    finally:
        vector_store.finalize_batch_index_updates(mutated=False)


def test_manifest_generation_change_invalidates_old_l1_and_l2_entries(monkeypatch, tmp_path):
    """manifest 换代后旧代际 key 的 L1/L2 条目都不得再被业务路径命中。"""
    import streamlit as st

    from enterprise_rag.core import state
    from enterprise_rag.services.cache_service import CacheService
    from enterprise_rag.storage.cache import PersistentAnswerCache
    from enterprise_rag.storage.kb_manifest import KnowledgeBaseManifest

    backend = PersistentAnswerCache(tmp_path / "answer_cache.sqlite3")
    manifest = KnowledgeBaseManifest(tmp_path / "manifest.sqlite3")
    manifest.mark_initialized()
    service = CacheService(persistent_cache=backend, manifest=manifest)
    st.session_state.clear()
    state.init_state()
    key = service.make_key("问题", "", kb_generation=0)
    service.set(key, {"content": "旧答案"}, expected_generation=0)
    assert service.get(key, expected_generation=0) == {"content": "旧答案"}

    manifest.commit_source("manual.txt", "revision-1", "hash-1", 1)

    assert service.get(key, expected_generation=0) is None
    assert service.set("new", {"content": "不应写入"}, expected_generation=0) is False
    assert backend.get("new") is None
