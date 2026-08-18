"""文档上传管线：磁盘暂存、有界后台任务、并行解析与串行入库。"""

from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path

from ..config import (
    RUNTIME_DATA_DIR,
    UPLOAD_MAX_BATCH_MB,
    UPLOAD_PARSE_WORKERS,
    UPLOAD_QUEUE_CAPACITY,
    UPLOAD_STAGING_TTL_SECONDS,
    UPLOAD_TASK_WORKERS,
)
from ..core.constants import MAX_FILE_SIZE_MB
from ..core.exceptions import DocumentException
from ..storage.vector_store import (
    add_document_to_kb,
    begin_batch_index_updates,
    delete_document,
    finalize_batch_index_updates,
    get_doc_count,
    get_document_count,
    list_documents,
)
from ..utils.loader import read_file
from ..utils.logger import log_audit_event

logger = logging.getLogger(__name__)

UPLOAD_STAGING_DIR = RUNTIME_DATA_DIR / "upload_staging"

# 上传任务快照为进程级数据：后台线程只更新 _UPLOAD_TASKS，
# 前端/API 层凭 task_id 轮询读取，不再需要会话级可见性映射。
_UPLOAD_TASKS: dict[str, dict] = {}
_UPLOAD_TASKS_LOCK = threading.RLock()

# 全局只创建固定数量的后台工作线程。队列另用信号量限制，
# 避免多个用户连续提交时无界堆积任务和暂存文件。
_UPLOAD_EXECUTOR = ThreadPoolExecutor(
    max_workers=UPLOAD_TASK_WORKERS,
    thread_name_prefix="rag-upload",
)
_UPLOAD_SLOTS = threading.BoundedSemaphore(UPLOAD_QUEUE_CAPACITY)
_UPLOAD_FUTURES: dict[str, Future] = {}


def _set_upload_task(task_id: str, task_info: dict) -> None:
    with _UPLOAD_TASKS_LOCK:
        current = dict(_UPLOAD_TASKS.get(task_id, {}))
        current.update(task_info)
        current["updated_at"] = time.time()
        _UPLOAD_TASKS[task_id] = current


def _register_upload_task(task_id: str, task_info: dict) -> None:
    """登记上传任务到进程级快照，供任务轮询接口读取。"""
    _set_upload_task(task_id, task_info)


def _safe_upload_name(original_name: str, index: int) -> str:
    """保留扩展名并隔离路径字符，防止上传名称越界写入。"""
    normalized = Path(str(original_name or f"upload-{index}")).name
    return f"{index:04d}_{normalized}"


def _stream_upload_to_path(
    upload,
    destination: Path,
    *,
    max_bytes: int | None = None,
) -> int:
    """把上传对象分块落盘，避免 ``getvalue()`` 再复制整份大文件。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(upload, "seek"):
        upload.seek(0)
    written = 0
    with destination.open("wb") as target:
        while True:
            chunk = upload.read(1024 * 1024)
            if not chunk:
                break
            if max_bytes is not None and written + len(chunk) > max_bytes:
                raise ValueError(f"文件 {upload.name} 的实际大小超过允许上限")
            target.write(chunk)
            written += len(chunk)
    if hasattr(upload, "seek"):
        upload.seek(0)
    return written


def _cleanup_staging_dir(staging_dir: Path) -> bool:
    try:
        shutil.rmtree(staging_dir, ignore_errors=False)
    except FileNotFoundError:
        return True
    except OSError:
        logger.exception("清理上传暂存目录失败: %s", staging_dir)
        return False
    return True


def cleanup_stale_staging_files(now: float | None = None) -> None:
    """删除因进程退出等原因遗留的过期上传暂存目录。"""
    if not UPLOAD_STAGING_DIR.exists():
        return
    with _UPLOAD_TASKS_LOCK:
        active_task_ids = {
            task_id
            for task_id, task in _UPLOAD_TASKS.items()
            if task.get("status") in {"pending", "processing"}
        }
        active_task_ids.update(
            task_id
            for task_id, future in _UPLOAD_FUTURES.items()
            if not future.done()
        )
    cutoff = (now if now is not None else time.time()) - UPLOAD_STAGING_TTL_SECONDS
    try:
        children = list(UPLOAD_STAGING_DIR.iterdir())
    except OSError:
        logger.exception("枚举上传暂存根目录失败: %s", UPLOAD_STAGING_DIR)
        return
    for child in children:
        if not child.is_dir() or child.name in active_task_ids:
            continue
        try:
            if child.stat().st_mtime < cutoff:
                _cleanup_staging_dir(child)
        except OSError:
            logger.exception("检查上传暂存目录失败: %s", child)


def _parse_staged_file(file_info: dict) -> dict:
    """解析单个暂存文件，返回可交给串行入库阶段的结构化结果。"""
    started = time.perf_counter()
    path = Path(file_info["path"])
    try:
        with path.open("rb") as file_obj:
            parsed = read_file(file_obj, show_error=False, return_metadata=True)
        content = parsed.get("text", "") if isinstance(parsed, dict) else parsed
        segments = parsed.get("segments", []) if isinstance(parsed, dict) else []
        if not content or not content.strip():
            return {
                "name": file_info["name"],
                "error": "未解析到有效文本",
                "parse_seconds": time.perf_counter() - started,
            }
        return {
            "name": file_info["name"],
            "content": content,
            "segments": segments,
            "parse_seconds": time.perf_counter() - started,
        }
    except Exception as exc:
        logger.exception("文件解析失败: %s", file_info["name"])
        return {
            "name": file_info["name"],
            "error": str(exc),
            "parse_seconds": time.perf_counter() - started,
        }


def _iter_parsed_files_bounded(file_info_list: list[dict], progress_callback=None):
    """按原上传顺序产出解析结果，同时限制已解析正文的内存驻留数量。"""
    worker_count = min(UPLOAD_PARSE_WORKERS, len(file_info_list))
    if worker_count <= 1:
        for completed, item in enumerate(file_info_list, start=1):
            result = _parse_staged_file(item)
            if progress_callback:
                progress_callback(completed, len(file_info_list))
            yield result
        return

    # 只预取 worker_count 个文件。消费者每取走一个结果才提交下一个，
    # 因而不会先解析完整批次再让所有正文同时滞留在 parsed_by_index 中。
    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="rag-parse",
    ) as executor:
        next_submit = 0
        next_yield = 0
        completed_count = 0
        pending: dict[Future, int] = {}
        ready: dict[int, dict] = {}

        def submit_one() -> bool:
            nonlocal next_submit
            if next_submit >= len(file_info_list):
                return False
            index = next_submit
            pending[executor.submit(_parse_staged_file, file_info_list[index])] = index
            next_submit += 1
            return True

        for _ in range(worker_count):
            submit_one()

        while pending:
            done, _not_done = wait(tuple(pending), return_when=FIRST_COMPLETED)
            for future in done:
                index = pending.pop(future)
                try:
                    ready[index] = future.result()
                except Exception as exc:
                    item = file_info_list[index]
                    logger.exception("并行解析任务异常: %s", item["name"])
                    ready[index] = {
                        "name": item["name"],
                        "error": str(exc),
                        "parse_seconds": 0.0,
                    }
                completed_count += 1
                if progress_callback:
                    progress_callback(completed_count, len(file_info_list))

            while next_yield in ready:
                result = ready.pop(next_yield)
                next_yield += 1
                submit_one()
                yield result


def _parse_files_bounded(
    file_info_list: list[dict], progress_callback=None
) -> list[dict]:
    """兼容测试和扩展调用；生产上传管线使用流式迭代器避免整批驻留。"""
    return list(_iter_parsed_files_bounded(file_info_list, progress_callback))


def _upload_worker(
    task_id: str,
    file_info_list: list[dict],
    staging_dir: str,
    batch_reserved: bool = False,
) -> None:
    """执行整批后台管线：并行解析，再串行向量化和写入 Chroma。"""
    task_started = time.perf_counter()
    display_name = "、".join(info["name"] for info in file_info_list)
    total = len(file_info_list)
    _set_upload_task(
        task_id,
        {
            "name": display_name,
            "status": "processing",
            "stage": "parsing",
            "progress": 2,
            "message": f"正在并行解析 0/{total}...",
        },
    )

    added_count = 0
    skipped_count = 0
    fail_list: list[str] = []
    phase_seconds: dict[str, float] = {}
    mutation_started = batch_reserved
    batch_mutated = False

    try:
        if not mutation_started:
            begin_batch_index_updates()
            mutation_started = True
        parsed_files = iter(
            _iter_parsed_files_bounded(
                file_info_list,
                progress_callback=lambda completed, count: _set_upload_task(
                    task_id,
                    {
                        "stage": "parsing",
                        "progress": 5 + int(completed / max(count, 1) * 20),
                        "message": f"正在并行解析 {completed}/{count}...",
                    },
                ),
            )
        )
        parse_wait_seconds = 0.0
        index_seconds = 0.0
        completed = 0
        while True:
            parse_wait_started = time.perf_counter()
            try:
                parsed = next(parsed_files)
            except StopIteration:
                parse_wait_seconds += time.perf_counter() - parse_wait_started
                break
            parse_wait_seconds += time.perf_counter() - parse_wait_started

            # 每份正文解析完成后立即进入串行向量化；处理完成即释放引用，
            # 峰值内存约为解析 worker 数量，而不是整批文件正文总和。
            if parsed.get("error"):
                fail_list.append(f"{parsed['name']}: {parsed['error']}")
                completed += 1
                continue
            _set_upload_task(
                task_id,
                {
                    "stage": "indexing",
                    "progress": 25 + int(completed / max(total, 1) * 65),
                    "message": f"正在向量化并入库 {completed + 1}/{total}...",
                },
            )
            file_started = time.perf_counter()
            try:
                outcome = add_document_to_kb(
                    parsed["name"],
                    parsed["content"],
                    source_segments=parsed.get("segments", []),
                    refresh_indexes=False,
                )
                if outcome == "added":
                    added_count += 1
                    batch_mutated = True
                elif outcome == "skipped":
                    skipped_count += 1
                else:
                    fail_list.append(f"{parsed['name']}: 未知入库结果 {outcome}")
            except DocumentException as exc:
                logger.exception("文档入库失败: %s", parsed["name"])
                # 写入或回滚状态不确定时，整批必须刷新 BM25 派生索引。
                batch_mutated = batch_mutated or bool(
                    getattr(exc, "storage_may_have_changed", False)
                )
                fail_list.append(f"{parsed['name']}: {exc}")
            except Exception as exc:
                logger.exception("文档入库异常: %s", parsed["name"])
                fail_list.append(f"{parsed['name']}: {exc}")
            finally:
                elapsed = time.perf_counter() - file_started
                index_seconds += elapsed
                logger.info(
                    "上传入库耗时 task_id=%s file=%s seconds=%.3f",
                    task_id,
                    parsed["name"],
                    elapsed,
                )
            completed += 1

        # 解析线程与串行入库会重叠；这里只记录等待解析结果的关键路径耗时，
        # 避免同一段入库时间同时计入 parse 和 index。
        phase_seconds["parse"] = parse_wait_seconds
        phase_seconds["index"] = index_seconds
    except Exception as exc:
        logger.exception("后台上传任务失败: %s", task_id)
        fail_list.append(f"后台任务: {exc}")
    finally:
        refresh_started = time.perf_counter()
        if mutation_started:
            try:
                finalize_batch_index_updates(mutated=batch_mutated)
            except Exception as exc:
                # Chroma 数据已提交时，BM25 失败应保留 dirty 状态供下次检索重试，
                # 但不能把已成功的文档误报为全部失败。
                logger.exception("批量入库后索引刷新失败: %s", task_id)
                fail_list.append(f"派生索引刷新失败: {exc}")
        phase_seconds["refresh"] = time.perf_counter() - refresh_started
        cleanup_ok = _cleanup_staging_dir(Path(staging_dir))
        if not cleanup_ok:
            fail_list.append("上传暂存目录清理失败，系统将在后续任务中重试")

        try:
            doc_count = get_document_count()
            chunk_count = get_doc_count()
        except Exception:
            logger.exception("上传任务完成后读取知识库统计失败")
            doc_count = None
            chunk_count = None

        if added_count == 0 and fail_list:
            status = "error"
        elif fail_list:
            status = "partial"
        else:
            status = "done"

        if status == "done":
            message = f"处理完成：新增 {added_count} 个"
            if skipped_count:
                message += f"，未变化跳过 {skipped_count} 个"
        elif status == "partial":
            message = f"部分完成：新增 {added_count} 个"
            if skipped_count:
                message += f"，未变化跳过 {skipped_count} 个"
            message += f"，失败 {len(fail_list)} 个"
        else:
            message = f"处理失败：{fail_list[-1] if fail_list else '未知错误'}"

        try:
            from backend.observability.metrics import mark_upload_result

            mark_upload_result(status)
        except Exception:
            logger.debug("记录上传指标失败", exc_info=True)
        log_audit_event(
            "upload_completed",
            status=status,
            file_count=total,
            added_count=added_count,
            skipped_count=skipped_count,
            failed_count=len(fail_list),
        )

        phase_seconds["total"] = time.perf_counter() - task_started
        logger.info(
            "批量上传完成 task_id=%s files=%d added=%d skipped=%d failed=%d "
            "parse=%.3fs index=%.3fs refresh=%.3fs total=%.3fs",
            task_id,
            total,
            added_count,
            skipped_count,
            len(fail_list),
            phase_seconds.get("parse", 0.0),
            phase_seconds.get("index", 0.0),
            phase_seconds.get("refresh", 0.0),
            phase_seconds["total"],
        )
        _set_upload_task(
            task_id,
            {
                "name": display_name,
                "status": status,
                "stage": "done",
                "progress": 100 if status in {"done", "partial"} else 0,
                "success_count": added_count,
                "added_count": added_count,
                "skipped_count": skipped_count,
                "fail_list": fail_list,
                "cleanup_pending": not cleanup_ok,
                "doc_count": doc_count,
                "chunk_count": chunk_count,
                "timings": phase_seconds,
                "message": message,
            },
        )


def _release_upload_slot(task_id: str, future: Future) -> None:
    with _UPLOAD_TASKS_LOCK:
        _UPLOAD_FUTURES.pop(task_id, None)
    _UPLOAD_SLOTS.release()
    try:
        future.result()
    except Exception:
        # Worker 内已负责记录业务失败，这里只防止 Future 异常被静默吞掉。
        logger.exception("上传 Future 异常: %s", task_id)


def _submit_upload_task(
    task_id: str,
    file_info_list: list[dict],
    staging_dir: Path,
) -> Future:
    future = _UPLOAD_EXECUTOR.submit(
        _upload_worker,
        task_id,
        file_info_list,
        str(staging_dir),
        True,
    )
    with _UPLOAD_TASKS_LOCK:
        _UPLOAD_FUTURES[task_id] = future
    future.add_done_callback(lambda done: _release_upload_slot(task_id, done))
    return future


class DocumentService:
    def process_uploads(self, uploaded_files):
        """验证后将整批文件暂存到磁盘，并交给有界后台队列处理。"""
        total_files = list(uploaded_files)
        if not total_files:
            return {"success": False, "error": "未选择文件"}

        oversized = [
            file.name
            for file in total_files
            if int(getattr(file, "size", 0) or 0)
            > MAX_FILE_SIZE_MB * 1024 * 1024
        ]
        if oversized:
            return {
                "success": False,
                "error": (
                    f"以下文件超过 {MAX_FILE_SIZE_MB}MB 限制：{', '.join(oversized)}"
                ),
            }

        total_bytes = sum(int(getattr(file, "size", 0) or 0) for file in total_files)
        max_batch_bytes = UPLOAD_MAX_BATCH_MB * 1024 * 1024
        if total_bytes > max_batch_bytes:
            return {
                "success": False,
                "error": (
                    f"本批文件合计 {total_bytes / 1024 / 1024:.1f}MB，"
                    f"超过 {UPLOAD_MAX_BATCH_MB}MB 批次上限"
                ),
            }

        # 即使是多个小文件，解析和向量化也不应占用 API 事件循环线程。
        if not _UPLOAD_SLOTS.acquire(blocking=False):
            return {
                "success": False,
                "busy": True,
                "error": (
                    f"上传队列已满（最多 {UPLOAD_QUEUE_CAPACITY} 个执行中/等待任务），"
                    "请稍后重试"
                ),
            }

        task_id = f"upload_{uuid.uuid4().hex}"
        staging_dir = UPLOAD_STAGING_DIR / task_id
        file_info_list: list[dict] = []
        staging_started = time.perf_counter()
        batch_reserved = False
        submitted = False
        task_registered = False
        try:
            # 从暂存阶段起就登记批次，确保其他会话不能在排队/解析期间清空知识库。
            begin_batch_index_updates()
            batch_reserved = True
            display_name = "、".join(file.name for file in total_files)
            _register_upload_task(
                task_id,
                {
                    "name": display_name,
                    "status": "pending",
                    "stage": "staging",
                    "progress": 0,
                    "file_count": len(total_files),
                    "total_bytes": total_bytes,
                    "message": "正在将上传内容暂存到磁盘...",
                },
            )
            task_registered = True
            cleanup_stale_staging_files()
            staging_dir.mkdir(parents=True, exist_ok=False)
            staged_bytes = 0
            for index, upload in enumerate(total_files):
                stored_path = staging_dir / _safe_upload_name(upload.name, index)
                remaining_batch = max_batch_bytes - staged_bytes
                max_file_bytes = MAX_FILE_SIZE_MB * 1024 * 1024
                written = _stream_upload_to_path(
                    upload,
                    stored_path,
                    max_bytes=min(max_file_bytes, remaining_batch),
                )
                staged_bytes += written
                expected = int(getattr(upload, "size", written) or written)
                if expected and written != expected:
                    raise OSError(
                        f"文件 {upload.name} 暂存不完整：预期 {expected} 字节，实际 {written} 字节"
                    )
                file_info_list.append(
                    {"name": upload.name, "path": str(stored_path), "size": written}
                )

            _set_upload_task(
                task_id,
                {
                    "status": "pending",
                    "stage": "queued",
                    "progress": 0,
                    "message": "已暂存，正在等待后台处理...",
                },
            )
            _submit_upload_task(task_id, file_info_list, staging_dir)
            submitted = True
        except Exception as exc:
            cleanup_ok = _cleanup_staging_dir(staging_dir)
            if task_registered:
                _set_upload_task(
                    task_id,
                    {
                        "status": "error",
                        "stage": "done",
                        "progress": 0,
                        "message": f"任务提交失败：{exc}",
                        "fail_list": [str(exc)],
                        "cleanup_pending": not cleanup_ok,
                    },
                )
            logger.exception("上传文件暂存或提交失败")
            return {"success": False, "error": str(exc)}
        finally:
            if not submitted:
                if batch_reserved:
                    finalize_batch_index_updates(mutated=False)
                _UPLOAD_SLOTS.release()

        staging_seconds = time.perf_counter() - staging_started
        logger.info(
            "上传任务入队 task_id=%s files=%d bytes=%d staging=%.3fs",
            task_id,
            len(total_files),
            total_bytes,
            staging_seconds,
        )
        return {
            "success": True,
            "success_count": 0,
            "added_count": 0,
            "skipped_count": 0,
            "fail_list": [],
            "async_launched": 1,
            "async_tasks": [task_id],
            "message": (
                f"已将 {len(total_files)} 个文件加入后台队列，"
                "可在上传区查看解析和入库进度。"
            ),
        }

    def get_upload_tasks(self):
        """返回全部进程级上传任务快照；前端按 task_id 轮询。"""
        with _UPLOAD_TASKS_LOCK:
            return {
                task_id: dict(task_info)
                for task_id, task_info in _UPLOAD_TASKS.items()
            }

    def clear_finished_tasks(
        self,
        keep_last: int = 3,
        *,
        preserve_task_id: str | None = None,
    ):
        """清理旧终态任务，但绝不影响当前轮询或仍由 Future 执行的任务。"""
        keep_last = max(0, keep_last)
        with _UPLOAD_TASKS_LOCK:
            done_ids = [
                task_id
                for task_id in _UPLOAD_TASKS
                if _UPLOAD_TASKS.get(task_id, {}).get("status")
                in {"done", "partial", "error"}
            ]
            done_ids.sort(
                key=lambda task_id: _UPLOAD_TASKS.get(task_id, {}).get(
                    "updated_at", 0
                )
            )
            protected_task_ids = {
                task_id
                for task_id, future in _UPLOAD_FUTURES.items()
                if not future.done()
            }
            if preserve_task_id:
                protected_task_ids.add(preserve_task_id)

            # 终态快照可能在 worker 写入最终状态后、Future 回调移除前短暂
            # 同时存在于 _UPLOAD_FUTURES。此时仍视为活跃任务，不能为满足
            # 历史数量限制而删除；被本次 GET 查询的任务同样必须能返回。
            excess = max(0, len(done_ids) - keep_last)
            for task_id in done_ids:
                if excess == 0:
                    break
                if task_id in protected_task_ids:
                    continue
                _UPLOAD_TASKS.pop(task_id, None)
                excess -= 1

    def list_documents(self):
        return list_documents()

    def delete_document(self, file_name):
        result = delete_document(file_name)
        if result:
            from ..storage.vector_store import update_doc_count

            update_doc_count()
        return result
