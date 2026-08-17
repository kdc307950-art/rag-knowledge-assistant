# 侧边栏：系统状态、文档上传、文件管理、高级设置
import logging

import streamlit as st
from ..core.state import get_state, clear_chat
from ..services.document_service import DocumentService
from ..storage.vector_store import (
    clear_all_documents,
    rebuild_knowledge_base,
    is_knowledge_base_busy,
    update_doc_count,
)

logger = logging.getLogger(__name__)
from ..storage.embedding import get_model_readiness, get_model_status
from ..rag.reranker import get_reranker_readiness
from ..core.constants import MAX_FILE_SIZE_MB
from ..services.background_service import (
    BackgroundImageError,
    clear_background,
    get_current_background_name,
    save_background,
)


def _model_badge(status: str, ready: bool) -> tuple[str, str]:
    """把模型详细就绪信息压缩为侧栏可稳定展示的短标签。"""
    normalized = (status or "").strip()
    if normalized.startswith("已加载") or normalized == "就绪":
        return "已加载" if normalized.startswith("已加载") else normalized, "ready"
    if not ready:
        return "不可用", "error"
    return "待加载", "processing"

def render_sidebar():
    """按状态、上传、文件管理和设置四个区域渲染侧边栏。"""
    # 上传任务执行时，向量化和 Chroma 写入持有进程级写锁。
    # 侧栏如果每次 rerun 都立即读取全库统计或文件列表，会排队等锁，
    # 表现为“后台异步上传但页面仍卡住”。此处先读取无锁竞争的任务快照。
    active_upload = is_knowledge_base_busy()
    with st.sidebar:
        st.markdown('<h2>📚 知识中心</h2>', unsafe_allow_html=True)
        st.caption("企业私有知识库管理平台")
        st.divider()
        render_status(active_upload=active_upload)
        st.divider()
        render_upload()
        st.divider()
        render_file_management(active_upload=active_upload)
        st.divider()
        render_settings()

def render_status(*, active_upload: bool = False):
    """从持久化知识库刷新文档指标，并展示模型与向量库状态。"""
    # 入库期间使用上一次统计快照；任务完成后快照会一次性刷新。
    if not active_upload:
        update_doc_count()
    status = get_state("system_status", {})
    # 接入真实嵌入模型状态（storage.embedding.get_model_status）
    model_status = get_model_status()
    embedding_ready, embedding_detail = get_model_readiness()
    reranker_ready, reranker_detail = get_reranker_readiness()
    embedding_label, embedding_class = _model_badge(model_status, embedding_ready)
    reranker_label, reranker_class = _model_badge(reranker_detail, reranker_ready)
    vector_status = status.get("vector_status", "就绪")
    vector_class = "ready" if vector_status == "就绪" else "processing" if vector_status in {"空", "检查中"} else "error"
    st.markdown("### 系统状态")
    st.markdown(f"""
    <div class="status-card">
        <div class="status-card__title">运行状态</div>
        <div class="status-card__metric">
            <span class="status-card__number">{status.get('doc_count', 0):,}</span>
            <span class="status-card__unit">份文档</span>
        </div>
        <div class="status-item">
            <span class="label">知识块</span>
            <span class="status-badge ready"><i></i>{status.get('chunk_count', 0):,} 个</span>
        </div>
        <div class="status-item">
            <span class="label">向量库</span>
            <span class="status-badge {vector_class}"><i></i>{vector_status}</span>
        </div>
        <div class="status-item">
            <span class="label">嵌入模型</span>
            <span class="status-badge {embedding_class}" title="{embedding_detail}"><i></i>{embedding_label}</span>
        </div>
        <div class="status-item">
            <span class="label">重排模型</span>
            <span class="status-badge {reranker_class}" title="{reranker_detail}"><i></i>{reranker_label}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)
    if vector_status in {"索引异常", "连接异常"}:
        st.error("知识库索引不可用，提问和文件管理会失败。")
        with st.popover("重建知识库索引"):
            st.caption("将删除现有知识库向量数据，背景图片和回答缓存不受影响。")
            if st.button("确认重建", key="confirm_rebuild_kb", type="primary"):
                if rebuild_knowledge_base():
                    st.success("知识库索引已重建，请重新上传文档。")
                    update_doc_count()
                    st.rerun()
                else:
                    logger.error("用户确认后知识库索引重建失败")
                    st.error("重建失败，请停止其他应用实例后重试。")

def render_async_tasks(*, auto_refresh: bool = False):
    """展示异步上传任务的处理进度与结果。"""
    doc_service = DocumentService()
    # 自动清理已完成任务，避免界面长期堆积
    doc_service.clear_finished_tasks(keep_last=2)
    tasks = doc_service.get_upload_tasks()
    # 后台任务快照属于进程级数据。将最新终态同步回本会话，使聊天区能
    # 正确提示“知识库正在更新”，并在完成后刷新文档计数。
    st.session_state.upload_tasks = {
        task_id: dict(task) for task_id, task in tasks.items()
    }
    completed_tasks = [
        task
        for task in tasks.values()
        if task.get("status") in {"done", "partial", "error"}
    ]
    latest_completed = (
        max(completed_tasks, key=lambda task: task.get("updated_at", 0))
        if completed_tasks
        else None
    )
    if latest_completed and isinstance(latest_completed.get("doc_count"), int):
        current_status = dict(get_state("system_status", {}))
        current_status["doc_count"] = latest_completed["doc_count"]
        if isinstance(latest_completed.get("chunk_count"), int):
            current_status["chunk_count"] = latest_completed["chunk_count"]
            current_status["vector_status"] = (
                "就绪" if latest_completed["chunk_count"] > 0 else "空"
            )
        st.session_state.system_status = current_status
    active = any(
        task.get("status") in {"pending", "processing"}
        for task in tasks.values()
    )
    if not tasks:
        return
    st.markdown("### ⏳ 异步上传任务")
    for task_id, task in tasks.items():
        name = task.get("name", task_id)
        status = task.get("status", "processing")
        progress = task.get("progress", 0)
        message = task.get("message", "")

        if status == "done":
            st.success(f"✅ {name}：{message}")
        elif status == "partial":
            st.warning(f"⚠️ {name}：{message}")
            if task.get("fail_list"):
                with st.expander("查看失败文件", expanded=False):
                    for item in task["fail_list"]:
                        st.write(f"- {item}")
        elif status == "error":
            st.error(f"❌ {name}：{message}")
            if task.get("fail_list"):
                with st.expander("查看失败原因", expanded=False):
                    for item in task["fail_list"]:
                        st.write(f"- {item}")
        elif status == "pending":
            st.info(f"⏳ {name}：{message}")
        else:
            st.progress(progress / 100.0, text=f"📄 {name}：{message}")

    # 定时 fragment 发现任务已经全部进入终态时触发一次完整 rerun，
    # 让页面改用普通渲染并停止后续定时刷新。
    if auto_refresh and not active:
        st.rerun()

    # 后台线程不能直接触发 Streamlit rerun。任务区由独立 fragment 定时刷新，
    # 只重绘该区域，避免上传期间反复执行完整的聊天和检索页面。


def render_async_tasks_fragment():
    """有活动上传时独立轮询；空闲时普通渲染，避免永久定时 rerun。"""
    tasks = DocumentService().get_upload_tasks()
    active = any(task.get("status") in {"pending", "processing"} for task in tasks.values())

    if active:
        @st.fragment(run_every=1.0)
        def _refreshing_task_panel():
            render_async_tasks(auto_refresh=True)

        _refreshing_task_panel()
    else:
        render_async_tasks()

def render_upload():
    """接收批量上传，并将整批文件交给后台入库管线。"""
    st.markdown("### 📤 文档上传")
    st.caption(f"步骤 1：选择文档（支持 PDF / Word / TXT / MD，可批量，单个文件 ≤ {MAX_FILE_SIZE_MB}MB）")

    uploaded_files = st.file_uploader(
        "📂 步骤 1：选择文档",
        type=["pdf", "docx", "txt", "md"],
        accept_multiple_files=True,
        max_upload_size=MAX_FILE_SIZE_MB,
        label_visibility="collapsed",
        help=f"支持批量上传，单个文件不超过 {MAX_FILE_SIZE_MB}MB",
        key="document_upload",
    )

    if st.button("🚀 步骤 2：解析并加入知识库", key="process_upload", type="primary"):
        if not uploaded_files:
            st.warning("请先选择文件")
        else:
            doc_service = DocumentService()
            result = doc_service.process_uploads(uploaded_files)
            if result.get("async_launched", 0) > 0:
                # 整批文件已经进入后台处理，提示用户到下方查看进度。
                st.success(result.get("message", "已将文件加入后台处理队列"))
                # 任务刚入队时不读 Chroma，否则会与后台写入争抢同一把锁。
                st.rerun()
            elif result["success"]:
                message = result.get("message", "处理完成")
                if result.get("fail_list"):
                    st.warning(message)
                    with st.expander("⚠️ 失败文件", expanded=False):
                        for item in result["fail_list"]:
                            st.write(f"- {item}")
                else:
                    st.success(f"✅ {message}")
                st.session_state.kb_version += 1
                update_doc_count()
                st.rerun()
            else:
                error_message = result.get("error") or result.get("message") or "未知错误"
                # 详细异常已由文档服务记录；UI 层不重复写入文件名或解析内容。
                logger.error("文档上传任务未成功提交: has_fail_list=%s", bool(result.get("fail_list")))
                st.error(f"处理失败：{error_message}")
                if result.get("fail_list"):
                    with st.expander("⚠️ 失败文件"):
                        for f in result["fail_list"]:
                            st.write(f"- {f}")

    # 异步上传任务进度展示
    render_async_tasks_fragment()

def render_file_management(*, active_upload: bool = False):
    """列出已入库来源，并提供单文件删除和整库清空入口。"""
    st.markdown("### 📁 文件管理")
    if active_upload:
        st.info("知识库正在更新，文件列表将在任务完成后刷新。")
        return
    doc_service = DocumentService()
    doc_list = doc_service.list_documents()
    if doc_list:
        for doc in doc_list:
            col1, col2 = st.columns([4, 1])
            col1.caption("📄 " + doc)
            with col2:
                with st.popover("🗑️"):
                    st.caption(f"确定删除 **{doc}**？此操作不可恢复。")
                    if st.button("确认删除", key=f"confirm_del_{doc}", type="primary"):
                        if doc_service.delete_document(doc):
                            st.success("已删除")
                            st.session_state.kb_version += 1
                            update_doc_count()
                            st.rerun()
                        else:
                            logger.error("用户请求删除知识库文档失败")
                            st.error("删除失败")
    else:
        st.info("暂无文件")

    st.divider()
    with st.popover("🧹 清空知识库"):
        st.caption("将删除知识库中**所有**文档，此操作不可恢复。")
        if st.button("确认清空全部", key="confirm_clear_all", type="primary"):
            if clear_all_documents():
                st.success("✅ 已清空知识库")
                st.session_state.kb_version += 1
                update_doc_count()
                st.rerun()
            else:
                logger.error("用户请求清空知识库失败")
                st.error("清空失败")

def render_background_settings():
    st.markdown("#### 页面背景")
    current_background = get_current_background_name()
    if current_background:
        st.caption(f"当前背景：{current_background}")

    uploaded_background = st.file_uploader(
        "上传背景图片",
        type=["png", "jpg", "jpeg", "webp"],
        key="background_image_upload",
        max_upload_size=5,
        help="支持 PNG、JPG、JPEG、WEBP，最大 5MB。",
    )
    if st.button("应用背景图片", key="apply_background", type="secondary"):
        if uploaded_background is None:
            st.warning("请先选择背景图片")
        else:
            try:
                saved_name = save_background(uploaded_background)
            except BackgroundImageError as exc:
                logger.warning("背景图片校验失败")
                st.error(str(exc))
            except OSError:
                logger.exception("背景图片保存失败")
                st.error("背景图片保存失败，请检查 data 文件夹权限。")
            else:
                st.success(f"背景图片已保存：{saved_name}")
                st.rerun()

    if current_background and st.button(
        "恢复默认背景", key="clear_background", type="secondary"
    ):
        clear_background()
        st.success("已恢复默认背景")
        st.rerun()


def render_settings():
    st.markdown("### ⚙️ 高级设置")
    render_background_settings()
    st.divider()
    debug_mode = st.checkbox("开启调试模式", value=st.session_state.get("debug_mode", False))
    st.session_state.debug_mode = debug_mode

    if st.button("🧹 清空聊天记录与当前会话缓存", type="secondary"):
        clear_chat()
        st.success("✅ 已清空所有聊天记录与缓存")
        st.rerun()
