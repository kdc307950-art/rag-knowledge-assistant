"""企业知识库助手的 Streamlit 应用入口。"""

import hmac

import streamlit as st

st.set_page_config(
    page_title="企业知识库助手",
    page_icon="✦",
    layout="wide",
    initial_sidebar_state="auto",
)

from enterprise_rag.core.state import init_state
from enterprise_rag.config import (
    APP_PASSWORD,
    LOG_BACKUP_COUNT,
    LOG_DIR,
    LOG_LEVEL,
    LOG_MAX_BYTES,
)
from enterprise_rag.ui.chat_ui import render_chat
from enterprise_rag.ui.sidebar import render_sidebar
from enterprise_rag.utils.logger import setup_logger
from enterprise_rag.services.background_service import get_background_data_url

# 应用入口统一初始化文件日志。业务模块使用 ``logging.getLogger(__name__)``
# 即可自动写入运行日志与错误日志，不需要各自创建文件处理器。
logger = setup_logger(
    log_dir=LOG_DIR,
    level=LOG_LEVEL,
    max_bytes=LOG_MAX_BYTES,
    backup_count=LOG_BACKUP_COUNT,
)


def require_authentication() -> None:
    """配置 APP_PASSWORD 时，在渲染任何知识库内容前完成会话认证。"""
    if not APP_PASSWORD or st.session_state.get("authenticated", False):
        return

    st.title("企业知识库助手")
    st.caption("请输入访问口令")
    password = st.text_input(
        "访问口令",
        type="password",
        key="app_password_input",
        label_visibility="collapsed",
        placeholder="输入访问口令",
    )
    if st.button("登录", type="primary", icon=":material/login:"):
        if hmac.compare_digest(password, APP_PASSWORD):
            st.session_state.authenticated = True
            st.rerun()
        else:
            # 不记录口令内容，只保留一次失败事件用于排查访问配置。
            logger.warning("应用访问认证失败")
            st.error("访问口令错误")
    st.stop()


def inject_css() -> None:
    """注入应用主题、聊天布局、侧栏和响应式样式。"""
    st.markdown(
        """
        <style>
        :root {
            --ink: #262626;
            --ink-soft: #595959;
            --muted: #8c8c8c;
            --brand: #2563eb;
            --brand-soft: #eff6ff;
            --paper: #ffffff;
            --line: #e5e7eb;
            --night: #001529;
            --night-soft: #002140;
            --color-success: #52c41a;
            --color-warning: #faad14;
            --color-error: #ff4d4f;
            --color-disabled: #8c8c8c;
        }

        html, body, [class*="css"] {
            font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
        }

        .stApp {
            background: #f5f7fa;
            color: var(--ink);
        }

        [data-testid="stAppViewContainer"] > .main {
            background: var(--paper);
        }

        [data-testid="stHeader"] {
            background: rgba(252, 251, 247, 0.94);
            border-bottom: 1px solid #e5e7eb;
        }

        [data-testid="stMainBlockContainer"], .block-container {
            max-width: 1180px;
            padding: 2.75rem 3.5rem 8rem;
        }

        [data-testid="stSidebar"] {
            background: var(--night);
            border-right: 1px solid #003a70;
        }

        [data-testid="stSidebar"] > div:first-child {
            background: var(--night);
            padding-top: 1rem;
        }

        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3,
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] label {
            color: #f5f5f5;
        }

        [data-testid="stSidebar"] .stCaption,
        [data-testid="stSidebar"] [data-testid="stMetricLabel"] {
            color: #bfbfbf;
        }

        [data-testid="stSidebar"] [data-testid="stFileUploader"] section p,
        [data-testid="stSidebar"] [data-testid="stFileUploader"] section small,
        [data-testid="stSidebar"] [data-testid="stFileUploader"] section span {
            color: rgba(255, 255, 255, 0.86) !important;
            overflow: visible !important;
            white-space: normal !important;
            line-height: 1.4;
        }

        [data-testid="stSidebar"] hr {
            border-color: #003a70;
        }

        .chat-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1.25rem;
            padding: 1.35rem 1.5rem;
            margin-bottom: 1.5rem;
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.03), 0 1px 6px -1px rgba(0,0,0,0.02);
            backdrop-filter: blur(14px);
            -webkit-backdrop-filter: blur(14px);
        }

        .chat-header__eyebrow {
            color: var(--brand);
            font-size: 0.72rem;
            font-weight: 700;
            letter-spacing: 0.12em;
            margin-bottom: 0.38rem;
        }

        .chat-header h1 {
            color: var(--ink);
            font-size: 2.15rem;
            font-weight: 760;
            letter-spacing: 0;
            line-height: 1.15;
            margin: 0;
        }

        .chat-header__seal {
            flex: 0 0 auto;
            width: 3rem;
            height: 3rem;
            display: grid;
            place-items: center;
            color: #ffffff;
            background: var(--brand);
            border: 2px solid #bfdbfe;
            border-radius: 50%;
            box-shadow: 0 0 0 4px #eff6ff, 0 5px 13px rgba(37, 99, 235, 0.18);
            font-size: 1.2rem;
        }

        .welcome-panel {
            padding: 1.35rem 1.5rem;
            margin-bottom: 0.8rem;
            background: #ffffff;
            border: 1px solid #e5e7eb;
            border-top: 3px solid var(--brand);
            border-radius: 8px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.03), 0 1px 6px -1px rgba(0,0,0,0.02);
            backdrop-filter: blur(14px);
            -webkit-backdrop-filter: blur(14px);
        }

        .welcome-panel h2 {
            margin: 0;
            color: var(--ink);
            font-size: 1.35rem;
            letter-spacing: 0;
        }

        .welcome-panel p {
            color: var(--ink-soft);
            margin: 0.55rem 0 0;
        }

        .quick-label {
            color: var(--muted);
            font-size: 0.74rem;
            font-weight: 700;
            letter-spacing: 0.09em;
            margin: 1.1rem 0 0.6rem;
        }

        .st-key-quick_benefit .stButton > button,
        .st-key-quick_leave .stButton > button,
        .st-key-quick_expense .stButton > button {
            min-height: 5.1rem;
            padding: 0.9rem 1rem;
            text-align: left;
            white-space: pre-line;
            line-height: 1.45;
            background: #ffffff;
            border-color: #dbeafe;
            box-shadow: 0 1px 2px rgba(0,0,0,0.03), 0 1px 6px -1px rgba(0,0,0,0.02);
        }

        .st-key-quick_benefit .stButton > button:hover,
        .st-key-quick_leave .stButton > button:hover,
        .st-key-quick_expense .stButton > button:hover {
            background: var(--brand-soft);
            border-color: var(--brand);
            box-shadow: 0 4px 12px rgba(37, 99, 235, 0.14);
        }

        [data-testid="stChatMessage"] {
            align-items: flex-start;
            background: #ffffff;
            border: 1px solid var(--line);
            border-left: 3px solid var(--brand);
            border-radius: 7px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.03), 0 1px 6px -1px rgba(0,0,0,0.02);
            margin: 0 0 0.85rem;
            padding: 1rem 1.1rem;
        }

        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
            background: #001529;
            border-color: #003a70;
            border-left-color: #60a5fa;
            color: #f5f5f5;
        }

        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) p,
        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) li {
            color: #f5f5f5;
        }

        [data-testid="stChatMessage"] [data-testid="stChatMessageAvatar"] {
            border: 1px solid #bfdbfe;
            border-radius: 50%;
            background: #eff6ff;
        }

        [data-testid="stChatMessage"] p,
        [data-testid="stChatMessage"] li {
            color: var(--ink);
            line-height: 1.76;
        }

        [data-testid="stChatMessage"] code {
            background: #f3f4f6;
            color: #1d4ed8;
            border: 1px solid #dbeafe;
        }

        [data-testid="stBottomBlockContainer"] {
            padding: 0.42rem 0.9rem 0.6rem;
            background: rgba(20, 20, 17, 0.94);
            border-top: 1px solid #1f3b5b;
            backdrop-filter: blur(10px);
            -webkit-backdrop-filter: blur(10px);
        }

        [data-testid="stChatInput"] {
            max-width: 680px;
            margin: 0 auto;
            background: transparent;
            border: 0;
            border-radius: 8px;
            box-shadow: none;
            padding: 0;
        }

        /* Streamlit 1.61 applies secondaryBg to this direct child. */
        [data-testid="stChatInput"] > div {
            background: rgba(26, 26, 26, 0.96) !important;
            border: 1px solid #4b5563 !important;
            border-radius: 8px;
            box-shadow: inset 0 0 0 1px rgba(96, 165, 250, 0.12), 0 10px 24px rgba(0, 0, 0, 0.26);
        }

        [data-testid="stChatInput"]:focus-within > div {
            border-color: #60a5fa;
            box-shadow: inset 0 0 0 1px rgba(96, 165, 250, 0.36), 0 0 0 3px rgba(37, 99, 235, 0.16);
        }

        [data-testid="stChatInput"] textarea,
        [data-testid="stChatInput"] textarea::placeholder {
            color: #f5f5f5;
            caret-color: #60a5fa;
            font-size: 1rem;
            line-height: 1.45;
        }

        [data-testid="stChatInput"] [data-baseweb="textarea"],
        [data-testid="stChatInput"] [data-baseweb="textarea"] > div,
        [data-testid="stChatInput"] [data-baseweb="textarea"] textarea {
            background: transparent !important;
            padding: 0.35rem 0.2rem !important;
        }

        [data-testid="stChatInput"] textarea::placeholder {
            color: #bfbfbf !important;
            opacity: 1;
        }

        [data-testid="stChatInput"] button {
            color: #93c5fd;
            margin-right: 0.08rem;
        }

        [data-testid="stChatInput"] button:disabled {
            color: #6b7280;
        }

        .stButton > button {
            min-height: 2.75rem;
            border: 1px solid #d9d9d9;
            border-radius: 6px;
            background: #ffffff;
            color: #262626;
            font-weight: 600;
            box-shadow: none;
        }

        .stButton > button:hover {
            border-color: var(--brand);
            color: #1d4ed8;
            background: var(--brand-soft);
        }

        .stButton > button[kind="primary"] {
            background: var(--brand);
            border-color: var(--brand);
            color: #ffffff;
        }

        .stButton > button[kind="primary"]:hover {
            background: #1d4ed8;
            border-color: #1d4ed8;
            color: #ffffff;
        }

        [data-testid="stSidebar"] .stButton > button {
            background: #1677ff;
            border-color: #4096ff;
            color: #f4fff8;
        }

        [data-testid="stSidebar"] .stButton > button:hover {
            border-color: #91caff;
            background: #0958d9;
            color: #ffffff;
        }

        [data-testid="stSidebar"] .st-key-process_upload button {
            background: #1677ff;
            border-color: #4096ff;
            color: #f4fff8;
        }

        [data-testid="stSidebar"] .st-key-process_upload button:hover {
            background: #0958d9;
            border-color: #91caff;
            color: #ffffff;
        }

        [data-testid="stSidebar"] [data-testid="stPopoverButton"],
        [data-testid="stSidebar"] [class*="st-key-confirm_del_"] button,
        [data-testid="stSidebar"] .st-key-confirm_clear_all button {
            background: #ff4d4f;
            border-color: #ff7875;
            color: #ffffff;
        }

        [data-testid="stSidebar"] [data-testid="stPopoverButton"]:hover,
        [data-testid="stSidebar"] [class*="st-key-confirm_del_"] button:hover,
        [data-testid="stSidebar"] .st-key-confirm_clear_all button:hover {
            background: #cf1322;
            border-color: #ffaaa5;
            color: #ffffff;
        }

        [data-testid="stSidebar"] .st-key-document_upload [data-testid="stFileUploaderDropzone"] {
            background: #002140;
            border: 1px dashed #3f6f9f !important;
            border-radius: 6px;
            color: #f5f5f5;
        }

        [data-testid="stSidebar"] .st-key-document_upload [data-testid="stFileUploaderDropzone"] p,
        [data-testid="stSidebar"] .st-key-document_upload [data-testid="stFileUploaderDropzone"] span {
            color: #d9d9d9 !important;
        }

        [data-testid="stSidebar"] [data-testid="stMetricValue"] {
            color: #91caff;
        }

        .status-card {
            background: #002140;
            border: 1px solid #1f4d7a;
            border-radius: 6px;
            padding: 0.95rem 1rem;
        }

        .status-card .status-card__title { color: #f5f5f5; font-weight: 700; margin-bottom: 0.55rem; }
        .status-card__metric { display: flex; align-items: baseline; gap: 0.45rem; margin-bottom: 0.6rem; }
        .status-card__number { color: #ffffff; font-size: 1.8rem; font-weight: 700; line-height: 1; }
        .status-card__unit { color: #bfbfbf; font-size: 0.82rem; }
        .status-item { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 0.65rem; padding: 0.28rem 0; }
        .status-item .label { min-width: 0; color: #bfbfbf; white-space: nowrap; }
        .status-badge { display: inline-flex; align-items: center; justify-self: end; gap: 0.35rem; color: #f5f5f5; font-size: 0.82rem; font-weight: 600; white-space: nowrap; }
        .status-badge i { width: 0.48rem; height: 0.48rem; border-radius: 50%; background: #8c8c8c; display: inline-block; }
        .status-badge.ready i { background: var(--color-success); }
        .status-badge.processing i { background: var(--color-warning); }
        .status-badge.error i { background: var(--color-error); }
        .general-notice { display: flex; align-items: center; gap: 0.45rem; margin-bottom: 0.75rem; padding: 0.5rem 0.7rem; background: #fffbe6; border-left: 3px solid var(--color-warning); color: #8c5a00; font-size: 0.86rem; border-radius: 4px; }
        .general-notice span { display: inline-grid; place-items: center; width: 1.1rem; height: 1.1rem; border-radius: 50%; background: var(--color-warning); color: #ffffff; font-weight: 700; }

        [data-testid="stExpander"] {
            border: 1px solid var(--line);
            border-radius: 6px;
            background: #ffffff;
        }

        @media (max-width: 760px) {
            [data-testid="stMainBlockContainer"], .block-container {
                padding: 1.6rem 1rem 7rem;
            }

            .chat-header { padding: 1.1rem 1rem; }
            .chat-header h1 { font-size: 1.65rem; }
            .welcome-panel { padding: 1.1rem 1rem; }
            .chat-header__seal { width: 2.55rem; height: 2.55rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    background_url = get_background_data_url()
    if background_url:
        st.markdown(
            "<style>"
            '.stApp { background-image: url("'
            + background_url
            + '") !important; background-size: cover; background-position: center; '
            "background-attachment: fixed; background-repeat: no-repeat; }"
            '[data-testid="stAppViewContainer"] > .main { '
            "background: radial-gradient(ellipse 92% 72% at 50% 18%, rgba(255, 255, 255, 0.94) 0%, "
            "rgba(248, 250, 252, 0.9) 62%, rgba(239, 246, 255, 0.82) 100%) !important; }"
            '[data-testid="stHeader"] { background: rgba(255, 255, 255, 0.9) !important; '
            "backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); }"
            "</style>",
            unsafe_allow_html=True,
        )


def main() -> None:
    """初始化会话、完成认证并依次渲染侧栏与聊天区。"""
    init_state()
    require_authentication()
    inject_css()
    render_sidebar()
    render_chat()


if __name__ == "__main__":
    main()
