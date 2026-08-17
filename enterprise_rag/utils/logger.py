"""项目级结构化日志、事故台账与未捕获异常记录。"""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import threading
from typing import Any


PACKAGE_LOGGER_NAME = "enterprise_rag"
_MANAGED_HANDLER_PREFIX = "enterprise_rag."
_INCIDENT_LOCK = threading.RLock()
_EXCEPTION_HOOKS_INSTALLED = False

# 只保留可复盘的工程事实；不包含用户原文、文档正文、密钥或个人信息。
_HISTORICAL_INCIDENTS = (
    {
        "incident_id": "INC-2026-08-history-rewrite",
        "severity": "high",
        "summary": "独立实体问题被上一轮历史主题污染。",
        "cause": "查询改写对独立问题错误携带历史上下文。",
        "resolution": "仅对明确指代型追问使用历史改写，独立问题保持原始查询。",
    },
    {
        "incident_id": "INC-2026-08-rag-routing",
        "severity": "high",
        "summary": "预判式意图路由绕过知识库并触发通用模型幻觉。",
        "cause": "检索发生前由分类结果决定是否进入 RAG。",
        "resolution": "改为 RAG-First：除问候白名单外先检索，再依据结果回答或拒答；通用办公模式需用户显式触发。",
    },
    {
        "incident_id": "INC-2026-08-hnsw-recovery",
        "severity": "critical",
        "summary": "Chroma HNSW 索引损坏导致知识库检索服务不可用。",
        "cause": "运行数据中的向量索引无法加载，报错包含 Error loading hnsw index。",
        "resolution": "增加向量库健康检查和用户确认后的索引重建入口；向量库与 manifest 作为同一恢复单元维护。",
    },
    {
        "incident_id": "INC-2026-08-reranker-status",
        "severity": "medium",
        "summary": "Reranker 懒加载状态在侧栏中不够明确。",
        "cause": "模型文件可用与模型已完成真实加载没有区分。",
        "resolution": "区分待加载、已加载和不可用状态，并保留模型名称供排查。",
    },
)


def _redact(value: str) -> str:
    """移除日志中可能出现的密钥和值较长的 Bearer Token。"""
    text = str(value)
    for name in ("DASHSCOPE_API_KEY", "OPENAI_API_KEY"):
        secret = os.getenv(name)
        if secret:
            text = text.replace(secret, "***REDACTED***")
    text = re.sub(r"(?i)(bearer\s+)[a-z0-9_\-.]{12,}", r"\1***REDACTED***", text)
    return re.sub(r"(?i)sk[-_][a-z0-9_\-.]{8,}", "***REDACTED***", text)


class _JsonFormatter(logging.Formatter):
    """将运行日志输出为一行 JSON，便于后续检索和集中采集。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": _redact(record.getMessage()),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            payload["exception"] = _redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _write_json_line(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def _seed_historical_incidents(log_dir: Path) -> None:
    """首次运行时写入已复盘的高价值事故，避免每次启动重复追加。"""
    path = log_dir / "incident_history.jsonl"
    existing_ids: set[str] = set()
    if path.is_file():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    incident_id = json.loads(line).get("incident_id")
                except json.JSONDecodeError:
                    continue
                if incident_id:
                    existing_ids.add(str(incident_id))
        except OSError:
            return
    with _INCIDENT_LOCK:
        for incident in _HISTORICAL_INCIDENTS:
            if incident["incident_id"] not in existing_ids:
                _write_json_line(
                    path,
                    {"record_type": "historical_incident", "recorded_at": "2026-08-14", **incident},
                )


def _install_exception_hooks() -> None:
    """记录主线程和后台线程中未被业务层捕获的异常。"""
    global _EXCEPTION_HOOKS_INSTALLED
    if _EXCEPTION_HOOKS_INSTALLED:
        return
    _EXCEPTION_HOOKS_INSTALLED = True
    previous_sys_hook = sys.excepthook
    previous_thread_hook = threading.excepthook

    def log_sys_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            previous_sys_hook(exc_type, exc_value, exc_traceback)
            return
        logging.getLogger(f"{PACKAGE_LOGGER_NAME}.unhandled").critical(
            "未捕获的主线程异常",
            exc_info=(exc_type, exc_value, exc_traceback),
        )

    def log_thread_exception(args: threading.ExceptHookArgs) -> None:
        logging.getLogger(f"{PACKAGE_LOGGER_NAME}.unhandled").critical(
            "未捕获的后台线程异常: thread=%s",
            getattr(args.thread, "name", "unknown"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        previous_thread_hook(args)

    sys.excepthook = log_sys_exception
    threading.excepthook = log_thread_exception


def setup_logger(
    *,
    log_dir: Path | str | None = None,
    level: str | int = logging.INFO,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 7,
    reset: bool = False,
) -> logging.Logger:
    """配置控制台与可滚动文件日志，并返回项目根 logger。"""
    package_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    resolved_level = logging._nameToLevel.get(str(level).upper(), level)
    resolved_level = resolved_level if isinstance(resolved_level, int) else logging.INFO
    directory = Path(log_dir or os.getenv("RAG_LOG_DIR", "logs")).expanduser()

    if reset:
        for handler in list(package_logger.handlers):
            if handler.get_name().startswith(_MANAGED_HANDLER_PREFIX):
                package_logger.removeHandler(handler)
                handler.close()

    if not any(handler.get_name() == f"{_MANAGED_HANDLER_PREFIX}app" for handler in package_logger.handlers):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            formatter = _JsonFormatter()
            app_handler = RotatingFileHandler(
                directory / "app.log", maxBytes=max(1024, int(max_bytes)),
                backupCount=max(1, int(backup_count)), encoding="utf-8",
            )
            app_handler.set_name(f"{_MANAGED_HANDLER_PREFIX}app")
            app_handler.setLevel(logging.INFO)
            app_handler.setFormatter(formatter)
            error_handler = RotatingFileHandler(
                directory / "error.log", maxBytes=max(1024, int(max_bytes)),
                backupCount=max(1, int(backup_count)), encoding="utf-8",
            )
            error_handler.set_name(f"{_MANAGED_HANDLER_PREFIX}error")
            error_handler.setLevel(logging.ERROR)
            error_handler.setFormatter(formatter)
            package_logger.addHandler(app_handler)
            package_logger.addHandler(error_handler)
            _seed_historical_incidents(directory)
        except OSError as exc:
            logging.getLogger().warning("日志目录不可用，已退回控制台日志: %s", exc)

    package_logger.setLevel(resolved_level)
    package_logger.propagate = True
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        root_logger.addHandler(console_handler)
    root_logger.setLevel(resolved_level)
    _install_exception_hooks()
    package_logger.info("日志系统已就绪: directory=%s level=%s", directory, logging.getLevelName(resolved_level))
    for handler in package_logger.handlers:
        handler.flush()
    return package_logger
