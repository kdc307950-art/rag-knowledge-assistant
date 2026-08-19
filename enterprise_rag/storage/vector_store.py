"""持久化 Chroma 向量存储：串行化写入，并按需维护 BM25 索引。"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
import uuid

import chromadb

from ..config import (
    EMBEDDING_BATCH_SIZE,
    HYBRID_ALPHA,
    KB_DATA_DIR,
    USE_HYBRID_SEARCH,
    VECTOR_WRITE_BATCH_SIZE,
)
from ..core.exceptions import (
    DocumentAuthorizationError,
    DocumentException,
    DocumentGovernanceError,
    KnowledgeBaseBusyError,
)
from .acl import (
    can_create_document,
    can_replace_document,
    can_delete_document,
    document_visible_to_user,
    normalize_acl_metadata,
)
from ..storage.embedding import get_embedding_model
from ..storage.document_governance import (
    metadata_for_source,
    retrieval_policy,
    source_allowed_for_retrieval,
)
from ..storage.kb_manifest import ManifestSnapshot, get_manifest
from ..rag.chunker import create_parent_child_chunks, split_by_chapters

logger = logging.getLogger(__name__)

# Chroma 写入、删除和 BM25 重建必须串行执行，避免并发上传破坏索引状态。
_VECTOR_LOCK = threading.RLock()
_MUTATION_CONDITION = threading.Condition(threading.RLock())
_bm25_dirty = True
_batch_update_depth = 0
_batch_mutated = False
_destructive_mutation = False


class SourceMetadataReadError(RuntimeError):
    """The manifest could not be read, so mutation authorization is unknown."""


def _upload_actor(metadata: dict | None) -> dict | None:
    """Reconstruct the server-attached principal used by the worker recheck."""
    if not metadata:
        return None
    principal_id = str(metadata.get("principal_id") or "").strip()
    if not principal_id:
        return None
    raw_roles = metadata.get("principal_roles") or []
    if isinstance(raw_roles, str):
        roles = [item.strip() for item in raw_roles.split(",") if item.strip()]
    else:
        roles = [str(item).strip() for item in raw_roles if str(item).strip()]
    return {
        "id": principal_id,
        "department": str(metadata.get("principal_department") or "").strip(),
        "roles": roles,
    }


def _active_revision_filter(
    snapshot: ManifestSnapshot,
    *,
    policy: str | None = None,
    access_context: dict | None = None,
) -> dict | None:
    """将文档治理策略转换为可见 revision；未确认时整个普通检索 fail-closed。"""
    manifest = get_manifest()
    policy = policy or retrieval_policy()
    # ``unresolved`` is a deployment state, not an incomplete authority filter.
    # Returning the subset that happens to be authoritative would silently
    # conceal an unresolved relationship in another active source.
    if policy == "unresolved" and snapshot.active_revisions:
        raise DocumentGovernanceError("文档生效关系未确认")
    revisions: list[str] = []
    for source, revision in snapshot.active_revisions.items():
        metadata = manifest.get_source_metadata(source) or metadata_for_source(source)
        if source_allowed_for_retrieval(metadata, policy=policy) and (
            access_context is None or document_visible_to_user(metadata, access_context)
        ):
            revisions.append(revision)
    if not revisions:
        return None
    return {"revision_id": {"$in": revisions}}


def _ensure_manifest_initialized(collection) -> ManifestSnapshot:
    """一次性迁移可判定的旧记录；歧义来源保持不可见并要求重新上传。"""
    manifest = get_manifest()
    snapshot = manifest.snapshot()
    if snapshot.initialized:
        return snapshot

    legacy = collection.get(include=["metadatas"])
    ids = list(legacy.get("ids") or [])
    metadatas = list(legacy.get("metadatas") or [])
    if not ids:
        manifest.mark_initialized()
        return manifest.snapshot()

    grouped: dict[str, list[tuple[str, dict]]] = {}
    for record_id, metadata in zip(ids, metadatas):
        meta = dict(metadata or {})
        source = str(meta.get("source") or "").strip()
        if source:
            grouped.setdefault(source, []).append((record_id, meta))

    for source, records in grouped.items():
        version_keys = {
            str(meta.get("revision_id") or meta.get("content_hash") or meta.get("file_id") or "")
            for _record_id, meta in records
        }
        version_keys.discard("")
        if len(version_keys) != 1:
            logger.error("旧知识库来源存在多个无法判定先后的版本，已隔离: %s", source)
            continue
        version_key = next(iter(version_keys))
        legacy_payload = f"{source}\0{version_key}".encode("utf-8")
        revision_id = (
            version_key
            if all(meta.get("revision_id") == version_key for _id, meta in records)
            else f"legacy_{hashlib.sha256(legacy_payload).hexdigest()}"
        )
        update_metas = []
        for _record_id, meta in records:
            updated = dict(meta)
            updated["revision_id"] = revision_id
            updated["ingest_state"] = "active"
            updated["record_schema"] = 2
            update_metas.append(updated)
        collection.update(ids=[item[0] for item in records], metadatas=update_metas)
        content_hash = str(records[0][1].get("content_hash") or version_key)
        manifest.commit_source(source, revision_id, content_hash, len(records))
    manifest.mark_initialized()
    return manifest.snapshot()


def _snapshot_for_read(collection) -> ManifestSnapshot:
    """在状态闸门和向量锁都已持有时捕获稳定快照。"""
    return _ensure_manifest_initialized(collection)


_chroma_client = None
_kb_collection = None
# 必须是可重入锁：get_kb_collection 会在锁内调用 get_chroma_client，
# 普通 Lock 会在首次初始化的嵌套路径上同线程死锁。
_resource_lock = threading.RLock()


def get_chroma_client():
    global _chroma_client
    if _chroma_client is None:
        with _resource_lock:
            if _chroma_client is None:
                KB_DATA_DIR.mkdir(parents=True, exist_ok=True)
                _chroma_client = chromadb.PersistentClient(path=str(KB_DATA_DIR))
    return _chroma_client


def get_kb_collection():
    global _kb_collection
    if _kb_collection is None:
        with _resource_lock:
            if _kb_collection is None:
                _kb_collection = get_chroma_client().get_or_create_collection(
                    name="kb_text", metadata={"hnsw:space": "cosine"}
                )
    return _kb_collection


def _reset_kb_collection_cache() -> None:
    """删除集合后清空缓存；下次访问重新获取新集合。"""
    global _kb_collection
    with _resource_lock:
        _kb_collection = None


def get_vector_store_health() -> tuple[bool, str]:
    """执行轻量真实读取，区分空知识库与损坏的 Chroma/HNSW 索引。

    Chroma 启动初期 compactor 可能尚未就绪，HNSW 索引读取会瞬态失败；
    先短等并重试一次，避免把启动时序问题误报为索引损坏。
    """
    if is_knowledge_base_busy():
        return True, "更新中"
    with _VECTOR_LOCK:
        collection = get_kb_collection()
        for attempt in range(2):
            try:
                collection.get(limit=1, include=["metadatas"])
                return True, "就绪"
            except Exception as exc:
                message = str(exc)
                if "hnsw" not in message.lower() and "index" not in message.lower():
                    logger.error("向量库健康检查失败: %s", exc, exc_info=True)
                    return False, "连接异常"
                if attempt == 0:
                    logger.warning("HNSW 索引健康检查瞬态失败，短等后重试: %s", exc)
                    time.sleep(1.0)
                    continue
                logger.error("Chroma HNSW 索引健康检查失败: %s", exc, exc_info=True)
                return False, "索引异常"
    return False, "索引异常"  # 不可达，仅满足类型收窄


def rebuild_knowledge_base() -> bool:
    """删除损坏集合并重建空集合；仅供用户显式确认后的恢复操作调用。"""
    global _bm25_dirty, _batch_mutated
    if not _try_begin_destructive_mutation():
        return False
    try:
        with _VECTOR_LOCK:
            client = get_chroma_client()
            try:
                client.delete_collection("kb_text")
            except Exception as exc:
                # 集合不存在可以直接继续；其他错误必须终止，避免清单与物理库失配。
                if "does not exist" not in str(exc).lower() and "not found" not in str(exc).lower():
                    logger.error("删除损坏知识库集合失败: %s", exc, exc_info=True)
                    return False
            _reset_kb_collection_cache()
            get_manifest().clear()
            get_kb_collection()
            _bm25_dirty = True
            _batch_mutated = False
            return True
    finally:
        _end_destructive_mutation()


_hybrid_available = False
try:
    from ..rag.hybrid import hybrid_search, rebuild_bm25

    _hybrid_available = True
except ImportError:
    logger.warning("混合检索依赖未安装，使用纯向量检索")

    def rebuild_bm25(*args, **kwargs):
        return None

    def hybrid_search(*args, **kwargs):
        return {"documents": [], "metadatas": [], "ids": []}


_use_hybrid = USE_HYBRID_SEARCH and _hybrid_available


def ensure_bm25(collection=None, *, retrieval_policy: str | None = None) -> None:
    """知识库变更后按需重建 BM25 索引。"""
    global _bm25_dirty
    if not _use_hybrid:
        return
    with _VECTOR_LOCK:
        # 批量入库期间继续使用旧 BM25 快照，避免查询恰好插入时
        # 针对中间状态反复全量重建。向量召回仍会使用已提交的新数据。
        if _batch_update_depth > 0 or not _bm25_dirty:
            return
        # 延迟到首次检索或写入结束后重建，避免每个分块写入都重复构建倒排索引。
        target = collection or get_kb_collection()
        snapshot = _ensure_manifest_initialized(target)
        where = _active_revision_filter(snapshot, policy=retrieval_policy)
        if where is None:
            rebuild_bm25(target, where={"revision_id": "__no_active_revision__"})
        else:
            rebuild_bm25(target, where=where)
        _bm25_dirty = False


def _refresh_indexes_after_mutation(collection) -> None:
    """数据已提交后完成派生索引刷新；索引失败不回滚已成功的数据写入。"""
    global _bm25_dirty
    _bm25_dirty = True
    try:
        # 派生索引保留全部可比较的 active 文档；实际查询仍按本次治理策略过滤。
        ensure_bm25(collection, retrieval_policy="all_active")
    except Exception:
        # 保留 dirty 状态，让下一次混合检索重试重建；数据操作和缓存失效不能
        # 因派生索引瞬时失败而被误报为失败。
        _bm25_dirty = True
        logger.exception("BM25 重建失败，将在下次检索时重试")


def begin_batch_index_updates() -> None:
    """登记上传批次；破坏性操作执行期间等待，避免清空后又写回。"""
    global _batch_update_depth
    with _MUTATION_CONDITION:
        while _destructive_mutation:
            _MUTATION_CONDITION.wait()
        _batch_update_depth += 1


def finalize_batch_index_updates(*, mutated: bool = True) -> None:
    """结束批量入库，并在所有并行批次完成后只刷新一次派生索引。"""
    global _batch_mutated, _batch_update_depth
    with _MUTATION_CONDITION:
        _batch_mutated = _batch_mutated or mutated
        if _batch_update_depth > 0:
            _batch_update_depth -= 1
        if _batch_update_depth > 0 or not _batch_mutated:
            if _batch_update_depth == 0:
                _MUTATION_CONDITION.notify_all()
            return
        _batch_mutated = False
        # 最后一个批次的刷新也属于上传事务边界；刷新完成前不允许清空或删除。
        with _VECTOR_LOCK:
            _refresh_indexes_after_mutation(get_kb_collection())
        _MUTATION_CONDITION.notify_all()


def is_knowledge_base_busy() -> bool:
    """返回是否存在上传批次或破坏性知识库操作。"""
    with _MUTATION_CONDITION:
        return _batch_update_depth > 0 or _destructive_mutation


def _try_begin_destructive_mutation() -> bool:
    """原子申请删除/清空闸门；上传期间直接拒绝而不是与其交错。"""
    global _destructive_mutation
    with _MUTATION_CONDITION:
        if _destructive_mutation or _batch_update_depth > 0:
            return False
        _destructive_mutation = True
        return True


def _end_destructive_mutation() -> None:
    global _destructive_mutation
    with _MUTATION_CONDITION:
        _destructive_mutation = False
        _MUTATION_CONDITION.notify_all()


def _make_file_ids(file_name: str, content: str) -> tuple[str, str]:
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    # 文件名参与主键计算：相同内容但不同来源仍应视为两份独立资料。
    file_id = hashlib.sha256(
        f"{file_name}\0{content}".encode("utf-8")
    ).hexdigest()
    return file_id, content_hash


def _source_location(parent_text: str, source_segments: list[dict] | None) -> dict:
    if not source_segments:
        return {}
    normalized_parent = "".join(parent_text.split())
    matched = []
    for segment in source_segments:
        segment_text = str(segment.get("text", ""))
        normalized_segment = "".join(segment_text.split())
        if not normalized_segment or not normalized_parent:
            continue
        probe = normalized_parent[:80]
        if probe in normalized_segment or normalized_segment[:80] in normalized_parent:
            matched.append(segment)
    if not matched:
        return {}
    location = {}
    pages = sorted({int(item["page"]) for item in matched if item.get("page") is not None})
    paragraphs = sorted({int(item["paragraph"]) for item in matched if item.get("paragraph") is not None})
    if pages:
        location["page"] = pages[0] if len(pages) == 1 else ",".join(map(str, pages))
    if paragraphs:
        location["paragraph"] = paragraphs[0] if len(paragraphs) == 1 else ",".join(map(str, paragraphs))
    return location


def add_document_to_kb(
    file_name: str,
    content: str,
    source_segments: list[dict] | None = None,
    *,
    refresh_indexes: bool = True,
    metadata: dict | None = None,
) -> str:
    """新增或替换单个文档，返回 ``added`` 或 ``skipped``。

    批量上传时可关闭即时索引刷新，并在整批结束后调用
    :func:`finalize_batch_index_updates`，避免每个文档都全量重建 BM25。
    """
    global _bm25_dirty
    # 整个“去重 -> 分块 -> 向量写入 -> 旧数据清理”过程保持原子性。
    with _VECTOR_LOCK:
        collection = get_kb_collection()
        manifest = get_manifest()
        _ensure_manifest_initialized(collection)
        file_id, content_hash = _make_file_ids(file_name, content)
        governance_metadata = metadata_for_source(file_name)
        acl_metadata = normalize_acl_metadata(metadata)
        combined_metadata = {**governance_metadata, **acl_metadata}
        active_source = manifest.get_source(file_name)
        existing_metadata = (
            manifest.get_source_metadata(file_name) if active_source else None
        )
        actor = _upload_actor(metadata)
        if actor is not None:
            allowed = (
                can_replace_document(actor, existing_metadata)
                and can_create_document(actor, combined_metadata)
                if active_source
                else can_create_document(actor, combined_metadata)
            )
            if not allowed:
                raise DocumentAuthorizationError(
                    f"当前账号不能创建或替换文档：{file_name}"
                )
        if active_source and active_source.get("content_hash") == content_hash and (
            existing_metadata or {}
        ) == combined_metadata:
            logger.info("文件 %s 内容未变，跳过更新", file_name)
            return "skipped"

        # 每次尝试使用唯一 revision，重试不会复用 Chroma 主键。
        revision_id = uuid.uuid4().hex
        old_revision = active_source.get("active_revision") if active_source else None

        # 先按章节保留文档结构，再生成父子分块：子块用于召回，父块用于回答上下文。
        chapters = split_by_chapters(content)
        # 边分块边写入，不在内存中同时保留整份大文档的所有子块。
        pending_chunks: list[str] = []
        pending_metas: list[dict] = []
        added_ids: list[str] = []
        chunk_index = 0
        global_parent_index = 0

        def flush_pending_chunks() -> None:
            """将当前小批次向量化并写入 Chroma。"""
            if not pending_chunks:
                return
            start_index = chunk_index - len(pending_chunks)
            batch_ids = [
                f"{revision_id}_{start_index + offset}"
                for offset in range(len(pending_chunks))
            ]
            embeddings = get_embedding_model().encode(
                pending_chunks,
                normalize_embeddings=True,
                batch_size=EMBEDDING_BATCH_SIZE,
            ).tolist()
            collection.add(
                ids=batch_ids,
                embeddings=embeddings,
                documents=list(pending_chunks),
                metadatas=list(pending_metas),
            )
            added_ids.extend(batch_ids)
            pending_chunks.clear()
            pending_metas.clear()

        try:
            for chapter_title, chapter_text in chapters:
                if not chapter_text.strip():
                    continue
                children, parent_metas = create_parent_child_chunks(chapter_text, file_id)
                # chunker 在每个章节内都从 parent_0 重新编号。入库层必须
                # 转换成文件级唯一 ID，否则检索去重会把后续章节误当成重复父块。
                chapter_parent_ids: dict[str, str] = {}
                chapter_parent_locations: dict[str, dict] = {}
                for child, parent_meta in zip(children, parent_metas):
                    local_parent_id = str(
                        parent_meta.get("parent_id")
                        or parent_meta.get("parent_index")
                        or parent_meta["parent_text"]
                    )
                    if local_parent_id not in chapter_parent_ids:
                        chapter_parent_ids[local_parent_id] = (
                            f"{file_id}_parent_{global_parent_index}"
                        )
                        chapter_parent_locations[local_parent_id] = _source_location(
                            parent_meta["parent_text"], source_segments
                        )
                        global_parent_index += 1
                    metadata = {
                        "file_id": file_id,
                        "content_hash": content_hash,
                        "revision_id": revision_id,
                        "ingest_state": "staging",
                        "record_schema": 2,
                        "source": file_name,
                        "chapter": chapter_title,
                        "parent_id": chapter_parent_ids[local_parent_id],
                        "parent_text": parent_meta["parent_text"],
                        "chunk_index": chunk_index,
                    }
                    metadata.update(combined_metadata)
                    # 同一父块通常会拆成多个子块；页码/段落匹配只计算一次。
                    metadata.update(chapter_parent_locations[local_parent_id])
                    pending_chunks.append(child)
                    pending_metas.append(metadata)
                    chunk_index += 1
                    if len(pending_chunks) >= VECTOR_WRITE_BATCH_SIZE:
                        flush_pending_chunks()

            flush_pending_chunks()
            if not added_ids:
                logger.warning("文件 %s 无有效内容，跳过入库", file_name)
                return "skipped"

            # 提交前按唯一 revision 校验完整分块集，防止 Chroma 静默忽略重复 ID。
            staged = collection.get(
                where={"revision_id": revision_id}, include=["metadatas"]
            )
            staged_ids = list(staged.get("ids") or [])
            if len(staged_ids) != len(added_ids) or set(staged_ids) != set(added_ids):
                raise RuntimeError(
                    f"新版本分块校验失败：预期 {len(added_ids)}，实际 {len(staged_ids)}"
                )

            manifest.commit_source(
                file_name,
                revision_id,
                content_hash,
                len(added_ids),
                metadata=combined_metadata,
            )
            active_metas = []
            for metadata in list(staged.get("metadatas") or []):
                updated = dict(metadata or {})
                updated["ingest_state"] = "active"
                active_metas.append(updated)
            try:
                collection.update(ids=staged_ids, metadatas=active_metas)
            except Exception:
                # 可见性只由 manifest 决定；状态标签更新失败不影响已提交版本。
                logger.exception("新版本已提交，但辅助状态标签更新失败: %s", file_name)

            # 清理该来源的全部非激活 revision，包括上次中断遗留的 staging。
            try:
                source_data = collection.get(where={"source": file_name}, include=["metadatas"])
                obsolete_ids = [
                    record_id
                    for record_id, metadata in zip(
                        source_data.get("ids") or [], source_data.get("metadatas") or []
                    )
                    if (metadata or {}).get("revision_id") != revision_id
                ]
                if obsolete_ids:
                    collection.delete(ids=obsolete_ids)
            except Exception:
                logger.exception("旧版本物理分块清理失败，逻辑版本已安全切换: %s", file_name)
        except Exception as exc:
            storage_may_have_changed = False
            committed = manifest.get_source(file_name)
            revision_committed = bool(
                committed and committed.get("active_revision") == revision_id
            )
            if revision_committed:
                storage_may_have_changed = True
            elif added_ids:
                try:
                    collection.delete(ids=added_ids)
                except Exception:
                    logger.exception("清理失败批次时发生错误")
                    storage_may_have_changed = True
            if storage_may_have_changed:
                _bm25_dirty = True
            raise DocumentException(
                f"文件 {file_name} 入库失败: {exc}",
                storage_may_have_changed=storage_may_have_changed,
            ) from exc

        # 单文件操作立即刷新；批量管线只标记 dirty，整批结束后再刷新一次。
        if refresh_indexes:
            _refresh_indexes_after_mutation(collection)
        else:
            _bm25_dirty = True
        return "added"


def delete_document_authorized(
    file_name: str,
    access_context: dict | None = None,
) -> dict | None:
    """Atomically authorize and delete one source.

    The manifest metadata used for authorization and the logical/physical
    deletion are protected by the same mutation gate and vector lock.  This
    closes the window where a caller could be authorized against one ACL
    snapshot and delete a later revision with different ownership.

    ``None`` means the source was not active when the mutation lock was
    acquired.  The returned mapping is the exact ACL snapshot used for the
    authorization decision and is suitable for audit logging.
    """
    global _bm25_dirty
    if not _try_begin_destructive_mutation():
        logger.warning("知识库正在更新，拒绝删除文件: %s", file_name)
        raise KnowledgeBaseBusyError("知识库正在更新")
    try:
        with _VECTOR_LOCK:
            collection = get_kb_collection()
            manifest = get_manifest()
            _ensure_manifest_initialized(collection)
            active = manifest.get_source(file_name)
            if active is None:
                return None
            try:
                metadata = manifest.get_source_metadata(file_name)
            except Exception as exc:
                raise SourceMetadataReadError("知识库元数据暂不可用") from exc
            metadata = dict(metadata or {})
            if access_context is not None and not can_delete_document(
                access_context, metadata
            ):
                raise DocumentAuthorizationError(
                    f"当前账号不能删除文档：{file_name}"
                )
            manifest.delete_source(file_name)
            old_data = collection.get(where={"revision_id": active["active_revision"]})
            old_ids = old_data.get("ids", [])
            if old_ids:
                try:
                    collection.delete(ids=old_ids)
                except Exception:
                    logger.exception("来源已逻辑删除，但物理分块清理失败: %s", file_name)
            _refresh_indexes_after_mutation(collection)
            return metadata
    finally:
        _end_destructive_mutation()


def delete_document(file_name: str) -> bool:
    """Backward-compatible internal deletion helper.

    Callers that have an authenticated principal must use
    :func:`delete_document_authorized`; this wrapper intentionally preserves
    the historical bool/error-swallowing contract for internal maintenance
    and existing tests.
    """
    try:
        return delete_document_authorized(file_name) is not None
    except Exception as exc:
        logger.error("删除文件失败 %s: %s", file_name, exc, exc_info=True)
        return False


def clear_all_documents() -> bool:
    global _bm25_dirty
    if not _try_begin_destructive_mutation():
        logger.warning("知识库正在更新，拒绝清空操作")
        return False
    try:
        with _VECTOR_LOCK:
            collection = get_kb_collection()
            manifest = get_manifest()
            snapshot = _ensure_manifest_initialized(collection)
            had_active_data = bool(snapshot.active_revisions)
            manifest.clear()
            all_ids = collection.get().get("ids", [])
            if all_ids:
                for start in range(0, len(all_ids), 10000):
                    try:
                        collection.delete(ids=all_ids[start : start + 10000])
                    except Exception:
                        logger.exception("知识库已逻辑清空，但部分物理分块清理失败")
                        break
            if had_active_data:
                _refresh_indexes_after_mutation(collection)
            return True
    except Exception as exc:
        logger.error("清空知识库失败: %s", exc, exc_info=True)
        return False
    finally:
        _end_destructive_mutation()


def list_documents(access_context: dict | None = None) -> list[str]:
    with _VECTOR_LOCK:
        try:
            collection = get_kb_collection()
            snapshot = _ensure_manifest_initialized(collection)
            manifest = get_manifest()
            return sorted(
                source for source in snapshot.active_revisions
                if access_context is None or document_visible_to_user(
                    manifest.get_source_metadata(source) or metadata_for_source(source),
                    access_context,
                )
            )
        except Exception as exc:
            logger.error("获取文件列表失败: %s", exc, exc_info=True)
            return []


def get_doc_count(access_context: dict | None = None) -> int:
    """返回向量分块数，适用于“知识块”指标而非真实文件数。"""
    with _VECTOR_LOCK:
        try:
            collection = get_kb_collection()
            snapshot = _ensure_manifest_initialized(collection)
            if access_context is None:
                return sum(snapshot.chunk_counts.values())
            manifest = get_manifest()
            return sum(
                count
                for source, count in snapshot.chunk_counts.items()
                if document_visible_to_user(
                    manifest.get_source_metadata(source) or metadata_for_source(source),
                    access_context,
                )
            )
        except Exception:
            return 0


def get_document_count(access_context: dict | None = None) -> int:
    """返回按来源去重后的真实文档数量。"""
    return len(list_documents(access_context))


def get_source_metadata(file_name: str) -> dict | None:
    """Return ACL metadata, distinguishing a missing source from legacy ACL.

    ``{}`` means the source exists but predates ACL metadata and must be
    fail-closed for non-admin mutations. ``None`` means the source does not
    exist and can therefore be uploaded as a new document.
    """
    with _VECTOR_LOCK:
        try:
            manifest = get_manifest()
            if manifest.get_source(file_name) is None:
                return None
            return manifest.get_source_metadata(file_name)
        except Exception as exc:
            logger.exception("获取来源元数据失败: %s", file_name)
            raise SourceMetadataReadError("知识库元数据暂不可用") from exc


def update_doc_count() -> None:
    """刷新文档数、知识块数与向量库状态。"""
    from ..core.state import get_state, set_state

    healthy, vector_status = get_vector_store_health()
    if not healthy:
        set_state(
            "system_status",
            {
                **get_state("system_status", {}),
                "doc_count": 0,
                "chunk_count": 0,
                "vector_status": vector_status,
            },
        )
        return
    chunk_count = get_doc_count()
    document_count = get_document_count()
    set_state(
        "system_status",
        {
            **get_state("system_status", {}),
            "doc_count": document_count,
            "chunk_count": chunk_count,
            "vector_status": "就绪" if chunk_count > 0 else "空",
        },
    )


def search(
    query_text: str,
    n_results: int = 10,
    *,
    retrieval_policy: str | None = None,
    access_context: dict | None = None,
):
    with _MUTATION_CONDITION:
        if _batch_update_depth > 0 or _destructive_mutation:
            raise KnowledgeBaseBusyError("知识库正在更新")
        with _VECTOR_LOCK:
            collection = get_kb_collection()
            snapshot = _snapshot_for_read(collection)
            where = _active_revision_filter(snapshot, policy=retrieval_policy, access_context=access_context)
            if where is None:
                return {
                    "ids": [[]],
                    "documents": [[]],
                    "metadatas": [[]],
                    "distances": [[]],
                    "_kb_generation": snapshot.generation,
                }
            query_embedding = get_embedding_model().encode(
                [query_text], normalize_embeddings=True
            ).tolist()
            results = collection.query(
                query_embeddings=query_embedding,
                n_results=max(1, int(n_results)),
                where=where,
                include=["documents", "metadatas", "distances"],
            )
            results["_kb_generation"] = snapshot.generation
            return results


def hybrid_search_wrapper(
    query_text: str,
    n_results: int = 50,
    *,
    retrieval_policy: str | None = None,
    access_context: dict | None = None,
):
    if not _use_hybrid:
        search_kwargs = {"n_results": n_results}
        if retrieval_policy is not None:
            search_kwargs["retrieval_policy"] = retrieval_policy
        if access_context is not None:
            search_kwargs["access_context"] = access_context
        return search(query_text, **search_kwargs)
    with _MUTATION_CONDITION:
        if _batch_update_depth > 0 or _destructive_mutation:
            raise KnowledgeBaseBusyError("知识库正在更新")
        with _VECTOR_LOCK:
            collection = get_kb_collection()
            snapshot = _snapshot_for_read(collection)
            where = _active_revision_filter(snapshot, policy=retrieval_policy, access_context=access_context)
            if where is None:
                return {
                    "ids": [[]],
                    "documents": [[]],
                    "metadatas": [[]],
                    "distances": [[]],
                    "_kb_generation": snapshot.generation,
                }
            # 混合检索将关键词匹配与向量语义召回合并，对专有名词更稳健。
            # BM25 is process-wide derived state. Build it from the complete
            # governance-visible corpus; the request-specific ACL `where` is
            # still enforced before BM25 candidates leave storage.
            ensure_bm25(collection, retrieval_policy="all_active")
            results = hybrid_search(
                query_text,
                collection,
                top_k=n_results,
                alpha=HYBRID_ALPHA,
                where=where,
            )
            docs = results.get("documents") or []
            metas = results.get("metadatas") or []
            distances = [0.0] * len(docs)
            return {
                "documents": [docs],
                "metadatas": [metas],
                "distances": [distances],
                "_kb_generation": snapshot.generation,
            }


def get_search_function():
    return hybrid_search_wrapper if _use_hybrid else search
