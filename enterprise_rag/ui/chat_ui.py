"""Streamlit 聊天界面：原生流式输出、来源展示和显式 G2/起草操作。"""

from __future__ import annotations

import logging
from uuid import uuid4

import streamlit as st

from ..config import DRAFT_ENABLED, FALLBACK_ENABLED
from ..core.constants import DISPLAY_MESSAGE_LIMIT, MAX_CHAT_ROUNDS, MAX_MESSAGES
from ..core.state import get_state
from ..services.chat_service import ChatService
from ..utils.cleaner import render_markdown

logger = logging.getLogger(__name__)
USER_AVATAR = ":material/person:"
ASSISTANT_AVATAR = ":material/smart_toy:"
GENERAL_NOTICE_HTML = (
    '<div class="general-notice">'
    '<span aria-hidden="true">!</span>'
    "以下内容由通用大模型生成，未依据企业知识库，请自行核验。"
    "</div>"
)
DRAFT_CAPTION = "基于已检索资料起草"


def _append_message(message: dict) -> None:
    """追加单条会话消息，并限制 session_state 中的历史总量。"""
    messages = list(get_state("messages", []))
    messages.append(message)
    st.session_state.messages = messages[-MAX_MESSAGES:]


def _mark_action(request_id: str, field: str, action: str, message: dict) -> None:
    """登记显式操作，但在生成成功前不消费原消息上的操作按钮。"""
    # 点击回调先锁住本会话的生成入口。下一次 rerun 会直接执行 pending action，
    # 失败后由执行器释放锁并恢复按钮，避免流式阶段重复提交同一请求。
    st.session_state.generating = True
    st.session_state.pending_chat_action = {
        "action": action,
        "query": message.get("query", ""),
        "retrieval_query": message.get("retrieval_query", ""),
        "request_id": request_id,
        "used_field": field,
    }
    st.rerun()


def _set_action_used(request_id: str, field: str) -> None:
    """仅在显式生成成功后提交按钮使用状态，失败时保留重试入口。"""
    if not request_id or field not in {"fallback_used", "draft_used"}:
        return
    messages = list(get_state("messages", []))
    for index, candidate in enumerate(messages):
        if candidate.get("request_id") == request_id:
            updated = dict(candidate)
            updated[field] = True
            messages[index] = updated
            break
    st.session_state.messages = messages


def _is_action_pending(request_id: str, action: str) -> bool:
    """判断当前消息的显式操作是否已进入本轮执行，防止重复点击。"""
    pending = get_state("pending_chat_action", {}) or {}
    return bool(
        request_id
        and pending.get("request_id") == request_id
        and pending.get("action") == action
    )


def _is_action_busy(request_id: str, action: str) -> bool:
    """显式动作待执行或正在生成时，禁用按钮以阻止重复提交。"""
    return bool(
        get_state("generating", False)
        or _is_action_pending(request_id, action)
    )


def _can_render_sources(message: dict) -> bool:
    """只有知识库回答且确实有来源时，才展示引用面板。"""
    return bool(
        message.get("sources")
        and not message.get("is_general")
        and not message.get("is_reject")
    )


def render_sources(sources: list[str] | None) -> None:
    """以默认折叠面板展示知识库来源；空来源不创建面板。"""
    if not sources:
        return
    with st.expander(f"引用来源 ({len(sources)})", expanded=False):
        for source in sources:
            st.write(f"- {source}")


def _render_answer_context(*, is_general: bool, is_draft: bool) -> None:
    """在正文前渲染回答类型提示，提示文本不写入消息正文。"""
    if is_general:
        st.markdown(GENERAL_NOTICE_HTML, unsafe_allow_html=True)
    if is_draft:
        st.caption(DRAFT_CAPTION)


def render_messages() -> None:
    """回放最近会话，保证正文、回答类型提示和来源展示规则一致。"""
    all_messages = list(get_state("messages", []))
    start = max(0, len(all_messages) - MAX_CHAT_ROUNDS * 2)
    visible = all_messages[start:]
    if len(visible) > DISPLAY_MESSAGE_LIMIT:
        visible = visible[-DISPLAY_MESSAGE_LIMIT:]

    for message in visible:
        role = message.get("role")
        if role == "user":
            with st.chat_message("user", avatar=USER_AVATAR):
                st.markdown(str(message.get("content", "")), unsafe_allow_html=False)
            continue
        if role != "assistant":
            continue

        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            _render_answer_context(
                is_general=bool(message.get("is_general")),
                is_draft=bool(message.get("is_draft")),
            )
            st.markdown(str(message.get("content", "")), unsafe_allow_html=False)
            if _can_render_sources(message):
                render_sources(message.get("sources"))
            if get_state("debug_mode", False) and message.get("thought"):
                with st.expander("回答依据", expanded=False):
                    st.text(message["thought"])

            request_id = message.get("request_id")
            if (
                request_id
                and FALLBACK_ENABLED
                and message.get("fallback_allowed")
                and not message.get("fallback_used")
            ):
                if st.button(
                    "改用通用办公回答",
                    key=f"fallback_{request_id}",
                    icon=":material/auto_awesome:",
                    disabled=_is_action_busy(request_id, "general"),
                ):
                    _mark_action(request_id, "fallback_used", "general", message)

            if (
                request_id
                and DRAFT_ENABLED
                and message.get("draft_allowed")
                and not message.get("draft_used")
            ):
                if st.button(
                    "基于资料起草",
                    key=f"draft_{request_id}",
                    icon=":material/edit_note:",
                    disabled=_is_action_busy(request_id, "draft"),
                ):
                    _mark_action(request_id, "draft_used", "draft", message)


def _text_chunks(stream):
    """过滤服务层元数据，仅把非空文本交给 Streamlit 原生流渲染器。"""
    for chunk in stream:
        if isinstance(chunk, str) and chunk:
            yield chunk


def _render_with_write_stream(
    stream,
    *,
    status_text: str,
    meta_provider=None,
) -> tuple[str, bool]:
    """使用唯一的原生逐字渲染路径，不在 UI 层拆分字符或阻塞线程。"""
    status = st.status(status_text, expanded=False, type="compact")
    received_text = False

    def text_stream():
        nonlocal received_text
        for chunk in _text_chunks(stream):
            received_text = True
            yield chunk

    try:
        result = st.write_stream(text_stream(), cursor="▌")
    except Exception:
        status.update(label="生成失败", state="error", expanded=False)
        raise

    content = result if isinstance(result, str) else ""
    meta = meta_provider() if meta_provider is not None else {}
    meta = meta or {}
    generation_failed = bool(
        meta.get("is_interrupted")
        or meta.get("is_empty")
        or meta.get("action_failed")
    )
    status.update(
        label=(
            "生成失败，可重试"
            if generation_failed
            else ("回答完成" if received_text else "回答完成（无内容）")
        ),
        state="error" if generation_failed else "complete",
        expanded=False,
    )
    return content, received_text


def _run_pending_action(action: dict) -> None:
    """执行用户明确选择的 G2 或资料起草操作，并保存完整元数据。"""
    query = str(action.get("query", "")).strip()
    if not query:
        return
    action_type = action.get("action")
    retrieval_query = str(action.get("retrieval_query", "")).strip() or query
    request_id = str(action.get("request_id", "")).strip()
    used_field = str(action.get("used_field", "")).strip()
    service = ChatService()
    st.session_state.generating = True
    is_general = action_type == "general"
    is_draft = action_type == "draft"
    if is_general:
        stream = service.general_stream(query)
    elif is_draft:
        stream = service.draft_stream(query, retrieval_query)
    else:
        st.session_state.generating = False
        return

    # 回答类型提示在实时输出和历史回放中使用同一渲染入口，但不会写入正文。
    content = ""
    received_text = False
    render_failed = False
    try:
        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            _render_answer_context(is_general=is_general, is_draft=is_draft)
            status_text = (
                "正在生成通用办公回答..."
                if is_general
                else "正在检索资料并生成草稿..."
            )
            content, received_text = _render_with_write_stream(
                stream,
                status_text=status_text,
                meta_provider=lambda: getattr(service, "_last_meta", {}),
            )
            if not received_text:
                content = "抱歉，未能生成有效回答，请稍后重试。"
                st.markdown(render_markdown(content), unsafe_allow_html=False)
    except Exception:
        render_failed = True
        logger.exception("Explicit chat action failed")
        content = "抱歉，处理请求时出现错误，请稍后重试。"
        st.markdown(render_markdown(content), unsafe_allow_html=False)
    finally:
        st.session_state.generating = False

    meta = getattr(service, "_last_meta", {}) or {}
    final_content = str(meta.get("content") or content)
    action_succeeded = bool(
        received_text
        and final_content.strip()
        and not render_failed
        and not meta.get("is_interrupted")
        and not meta.get("is_empty")
        and not meta.get("action_failed")
        and (is_general or bool(meta.get("sources")))
    )
    if action_succeeded:
        _set_action_used(request_id, used_field)
    _append_message(
        {
            "role": "assistant",
            "content": final_content,
            "thought": meta.get("thought"),
            "sources": meta.get("sources", []),
            "is_general": is_general,
            "is_draft": is_draft,
            "is_interrupted": bool(meta.get("is_interrupted")),
        }
    )
    st.rerun()


def handle_user_input() -> None:
    """接收问题、调用唯一原生流渲染器，并将最终回答写入会话历史。"""
    pending_action = st.session_state.pop("pending_chat_action", None)
    if pending_action:
        _run_pending_action(pending_action)
        return
    if st.session_state.get("generating", False):
        return

    pending = st.session_state.pop("pending_question", None)
    typed_question = st.chat_input(
        "输入问题，按 Enter 发送...",
        submit_mode="disable",
    )
    prompt = pending or typed_question
    if not prompt or not prompt.strip():
        return

    prompt = prompt.strip()
    st.session_state.chat_started = True
    st.session_state.generating = True
    _append_message({"role": "user", "content": prompt})

    response_content = ""
    service = ChatService()
    try:
        with st.chat_message("assistant", avatar=ASSISTANT_AVATAR):
            response_content, received_text = _render_with_write_stream(
                service.chat_stream(prompt),
                status_text="正在检索并整理知识库资料...",
                meta_provider=lambda: getattr(service, "_last_meta", {}),
            )
            if not received_text:
                response_content = "抱歉，未能生成有效回答，请重新提问。"
                st.markdown(render_markdown(response_content), unsafe_allow_html=False)
    except Exception:
        logger.exception("Chat service failed")
        response_content = "抱歉，处理请求时出现错误，请稍后重试。"
        st.markdown(render_markdown(response_content), unsafe_allow_html=False)
    finally:
        st.session_state.generating = False

    meta = getattr(service, "_last_meta", {}) or {}
    _append_message(
        {
            "role": "assistant",
            "content": meta.get("content") or response_content,
            "thought": meta.get("thought"),
            "sources": meta.get("sources", []),
            "request_id": uuid4().hex,
            "query": meta.get("query") or prompt,
            "retrieval_query": meta.get("retrieval_query") or prompt,
            "fallback_allowed": bool(meta.get("fallback_allowed")),
            "fallback_used": False,
            "draft_allowed": bool(meta.get("draft_allowed")),
            "draft_used": False,
            "is_reject": bool(meta.get("is_reject")),
            "is_general": bool(meta.get("is_general")),
            "is_interrupted": bool(meta.get("is_interrupted")),
        }
    )
    st.rerun()


def render_welcome() -> None:
    """在首次对话前展示三个可直接触发的业务问题入口。"""
    st.markdown(
        """
        <section class="welcome-panel">
            <h2>从知识库开始对话</h2>
            <p>选择一个业务主题，或直接输入你的问题。</p>
        </section>
        <div class="quick-label">推荐提问方向</div>
        """,
        unsafe_allow_html=True,
    )
    prompts = [
        ("福利政策", "员工福利、补贴、保险", "公司有哪些福利政策？", "quick_benefit"),
        ("请假制度", "年假、病假、考勤", "年假制度是什么？", "quick_leave"),
        ("报销流程", "费用报销、审批、标准", "报销流程是什么？", "quick_expense"),
    ]
    columns = st.columns(len(prompts), gap="small")
    for column, (title, detail, question, key) in zip(columns, prompts):
        with column:
            if st.button(f"{title}\n{detail}", key=key):
                st.session_state.pending_question = question
                st.session_state.chat_started = True
                st.rerun()


def render_chat() -> None:
    """渲染标题、知识库空状态、历史消息和底部输入框。"""
    st.markdown(
        """
        <section class="chat-header">
            <div>
                <div class="chat-header__eyebrow">KNOWLEDGE CONSOLE</div>
                <h1>企业知识库智能助手</h1>
            </div>
            <div class="chat-header__seal">✓</div>
        </section>
        """,
        unsafe_allow_html=True,
    )
    messages = get_state("messages", [])
    status = get_state("system_status", {}) or {}
    upload_tasks = get_state("upload_tasks", {}) or {}
    active_upload = any(
        task.get("status") in {"pending", "processing"}
        for task in upload_tasks.values()
        if isinstance(task, dict)
    )
    if int(status.get("doc_count", 0) or 0) == 0 and not active_upload:
        st.info("知识库中暂无文档。请先在左侧上传并解析 PDF、DOCX、TXT 或 MD 文件。")
    if active_upload:
        st.info("知识库正在更新，当前检索结果可能不完整。")
    if not messages and not get_state("chat_started", False):
        render_welcome()
    render_messages()
    handle_user_input()
