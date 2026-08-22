"""Encrypted, single-tenant quality feedback persistence."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, cast

from cryptography.fernet import Fernet, InvalidToken


FEEDBACK_REASONS = frozenset(
    {
        "not_accurate",
        "missing_source",
        "wrong_source",
        "refusal_unexpected",
        "latency",
        "irrelevant",
        "unsafe",
        "other",
    }
)
REVIEW_RESULTS = frozenset(
    {"recall_failure", "answer_failure", "not_a_failure", "duplicate"}
)


class QualityStoreError(RuntimeError):
    """Base class for quality persistence failures."""


class DuplicateRunError(QualityStoreError):
    pass


class FeedbackNotFoundError(QualityStoreError):
    pass


class FeedbackIneligibleError(QualityStoreError):
    pass


class FeedbackForbiddenError(QualityStoreError):
    pass


class FeedbackConflictError(QualityStoreError):
    pass


class FeedbackRateLimitError(QualityStoreError):
    pass


@dataclass(frozen=True)
class FeedbackResult:
    status: str
    feedback_id: int | None = None


def utc_ms() -> int:
    return int(time.time() * 1000)


class QualityService:
    """Persist runs and feedback in one SQLite database.

    Query and answer material is encrypted before it reaches SQLite. The key
    is deployment-owned and must not be generated or stored by this service.
    """

    def __init__(
        self,
        db_path: Path | str,
        encryption_key: str,
        *,
        retention_days: int = 30,
        max_query_chars: int = 4000,
        max_answer_chars: int = 12000,
        feedback_limit_per_hour: int = 30,
    ) -> None:
        self.db_path = Path(db_path)
        try:
            self._cipher = Fernet(encryption_key.encode("ascii"))
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise ValueError("QUALITY_ENCRYPTION_KEY 必须是有效的 Fernet key") from exc
        self.retention_days = max(1, int(retention_days))
        self.max_query_chars = max(256, int(max_query_chars))
        self.max_answer_chars = max(512, int(max_answer_chars))
        self.feedback_limit_per_hour = max(1, int(feedback_limit_per_hour))
        self._lock = threading.RLock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL UNIQUE,
                    principal_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    query_ciphertext BLOB NOT NULL,
                    answer_ciphertext BLOB,
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    terminal TEXT NOT NULL,
                    error_code TEXT,
                    feedback_eligible INTEGER NOT NULL DEFAULT 0,
                    created_at_ms INTEGER NOT NULL,
                    finalized_at_ms INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_runs_principal_created
                    ON runs(principal_id, created_at_ms DESC);
                CREATE INDEX IF NOT EXISTS idx_runs_created
                    ON runs(created_at_ms);
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
                    principal_id TEXT NOT NULL,
                    verdict TEXT NOT NULL CHECK(verdict IN ('up', 'down')),
                    reason TEXT,
                    review_status TEXT NOT NULL DEFAULT 'pending',
                    adjudication_result TEXT,
                    reviewer_id TEXT,
                    created_at_ms INTEGER NOT NULL,
                    reviewed_at_ms INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_feedback_created
                    ON feedback(created_at_ms);
                CREATE INDEX IF NOT EXISTS idx_feedback_review
                    ON feedback(review_status, created_at_ms DESC);
                """
            )

    def _encrypt(self, value: str, limit: int) -> bytes:
        return self._cipher.encrypt(str(value or "")[:limit].encode("utf-8"))

    def _decrypt(self, value: bytes | None) -> str:
        if not value:
            return ""
        try:
            return self._cipher.decrypt(bytes(value)).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, TypeError, ValueError) as exc:
            raise QualityStoreError("质量记录无法解密，检查 QUALITY_ENCRYPTION_KEY") from exc

    @staticmethod
    def _sources_json(sources: Any) -> str:
        if not isinstance(sources, list):
            sources = []
        safe = [str(item)[:512] for item in sources if str(item).strip()]
        return json.dumps(safe[:50], ensure_ascii=False)

    def reserve_run(
        self,
        *,
        run_id: str,
        message_id: str,
        principal_id: str,
        session_id: str,
        action: str = "chat",
        query: str,
    ) -> None:
        with self._lock, self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO runs(
                        run_id, message_id, principal_id, session_id, action,
                        query_ciphertext, terminal, created_at_ms
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        str(run_id),
                        str(message_id),
                        str(principal_id),
                        str(session_id),
                        str(action),
                        self._encrypt(query, self.max_query_chars),
                        "running",
                        utc_ms(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateRunError("message_id 已存在") from exc

    def finalize_run(
        self,
        *,
        run_id: str,
        terminal: str,
        answer: str = "",
        sources: list[str] | None = None,
        error_code: str | None = None,
        feedback_eligible: bool = False,
    ) -> bool:
        normalized_terminal = str(terminal or "unknown").strip().lower()
        if normalized_terminal not in {"done", "error", "interrupted", "unknown"}:
            normalized_terminal = "unknown"
        answer_ciphertext = (
            self._encrypt(answer, self.max_answer_chars) if answer else None
        )
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE runs SET
                    answer_ciphertext=?, sources_json=?, terminal=?, error_code=?,
                    feedback_eligible=?, finalized_at_ms=?
                WHERE run_id=? AND terminal='running'
                """,
                (
                    answer_ciphertext,
                    self._sources_json(sources),
                    normalized_terminal,
                    str(error_code)[:128] if error_code else None,
                    1 if feedback_eligible else 0,
                    utc_ms(),
                    str(run_id),
                ),
            )
            return cursor.rowcount == 1

    def get_run_by_message(self, message_id: str, *, include_content: bool = False) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runs WHERE message_id=?", (str(message_id),)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["sources"] = json.loads(result.pop("sources_json") or "[]")
        if include_content:
            result["query"] = self._decrypt(result.pop("query_ciphertext"))
            result["answer"] = self._decrypt(result.pop("answer_ciphertext", None))
        else:
            result.pop("query_ciphertext", None)
            result.pop("answer_ciphertext", None)
        result["feedback_eligible"] = bool(result.get("feedback_eligible"))
        return result

    def submit_feedback(
        self,
        *,
        message_id: str,
        principal_id: str,
        verdict: str,
        reason: str | None,
    ) -> FeedbackResult:
        normalized_verdict = str(verdict or "").strip().lower()
        if normalized_verdict not in {"up", "down"}:
            raise ValueError("verdict 必须是 up 或 down")
        if normalized_verdict == "down" and reason not in FEEDBACK_REASONS:
            raise ValueError("reason 不是受支持的反馈原因")
        if normalized_verdict == "up":
            reason = None
        now = utc_ms()
        cutoff = now - 60 * 60 * 1000
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT run_id, principal_id, feedback_eligible FROM runs WHERE message_id=?",
                (str(message_id),),
            ).fetchone()
            if run is None:
                raise FeedbackNotFoundError("回答不存在")
            if not bool(run["feedback_eligible"]):
                raise FeedbackIneligibleError("回答当前不可反馈")
            if str(run["principal_id"]) != str(principal_id):
                raise FeedbackForbiddenError("无权反馈该回答")
            existing = connection.execute(
                "SELECT id, verdict FROM feedback WHERE run_id=?", (run["run_id"],)
            ).fetchone()
            if existing is not None:
                if str(existing["verdict"]) == normalized_verdict:
                    return FeedbackResult("idempotent", int(existing["id"]))
                raise FeedbackConflictError("该回答已有相反反馈")
            count = connection.execute(
                "SELECT COUNT(*) FROM feedback WHERE principal_id=? AND created_at_ms>=?",
                (str(principal_id), cutoff),
            ).fetchone()[0]
            if int(count) >= self.feedback_limit_per_hour:
                raise FeedbackRateLimitError("反馈提交过于频繁，请稍后重试")
            cursor = connection.execute(
                """
                INSERT INTO feedback(run_id, principal_id, verdict, reason, created_at_ms)
                VALUES(?,?,?,?,?)
                """,
                (run["run_id"], str(principal_id), normalized_verdict, reason, now),
            )
            # 同 users.py：INSERT 之后 lastrowid 必定有值，typeshed 标注偏保守。
            return FeedbackResult("created", cast(int, cursor.lastrowid))

    def list_feedback_for_review(
        self, *, status: str = "pending", limit: int = 50
    ) -> list[dict[str, Any]]:
        if status not in {"pending", "accepted", "rejected"}:
            raise ValueError("review status 无效")
        limit = min(100, max(1, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT f.*, r.message_id, r.session_id, r.action, r.query_ciphertext,
                       r.answer_ciphertext, r.sources_json, r.terminal, r.error_code
                FROM feedback f JOIN runs r ON r.run_id=f.run_id
                WHERE f.review_status=? ORDER BY f.created_at_ms DESC LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["query"] = self._decrypt(item.pop("query_ciphertext"))
            item["answer"] = self._decrypt(item.pop("answer_ciphertext", None))
            item["sources"] = json.loads(item.pop("sources_json") or "[]")
            results.append(item)
        return results

    def review_feedback(
        self,
        feedback_id: int,
        *,
        reviewer_id: str,
        adjudication_result: str,
        accepted: bool,
    ) -> bool:
        if adjudication_result not in REVIEW_RESULTS:
            raise ValueError("adjudication_result 无效")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE feedback SET review_status=?, adjudication_result=?,
                    reviewer_id=?, reviewed_at_ms=?
                WHERE id=? AND review_status='pending'
                """,
                (
                    "accepted" if accepted else "rejected",
                    adjudication_result,
                    str(reviewer_id),
                    utc_ms(),
                    int(feedback_id),
                ),
            )
            return cursor.rowcount == 1

    def export_accepted_feedback(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Return reviewed feedback for human dev-set authoring only.

        This deliberately omits user/session identifiers.  It does not infer
        expected evidence or create an evaluation-ready case: a reviewer must
        still annotate those fields from the live document corpus.
        """
        limit = min(1000, max(1, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT f.id, f.verdict, f.reason, f.adjudication_result,
                       f.created_at_ms, f.reviewed_at_ms,
                       r.message_id, r.action, r.query_ciphertext,
                       r.answer_ciphertext, r.sources_json, r.terminal,
                       r.error_code
                FROM feedback f JOIN runs r ON r.run_id=f.run_id
                WHERE f.review_status='accepted'
                ORDER BY f.reviewed_at_ms ASC, f.id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["query"] = self._decrypt(item.pop("query_ciphertext"))
            item["answer"] = self._decrypt(item.pop("answer_ciphertext", None))
            item["sources"] = json.loads(item.pop("sources_json") or "[]")
            results.append(item)
        return results

    def purge_expired(self, *, now_ms: int | None = None) -> dict[str, int]:
        cutoff = (utc_ms() if now_ms is None else int(now_ms)) - self.retention_days * 86400000
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            feedback_cursor = connection.execute(
                "DELETE FROM feedback WHERE created_at_ms < ?", (cutoff,)
            )
            runs_cursor = connection.execute(
                "DELETE FROM runs WHERE created_at_ms < ?", (cutoff,)
            )
            return {"feedback": feedback_cursor.rowcount, "runs": runs_cursor.rowcount}


_QUALITY_STORE: QualityService | None = None
_QUALITY_STORE_LOCK = threading.Lock()


def get_quality_service() -> QualityService | None:
    """Return the configured store, or None when capture is deliberately off."""
    from .. import config

    if not config.QUALITY_CAPTURE_ENABLED or not config.QUALITY_ENCRYPTION_KEY:
        return None
    global _QUALITY_STORE
    with _QUALITY_STORE_LOCK:
        if _QUALITY_STORE is None:
            _QUALITY_STORE = QualityService(
                config.QUALITY_DB_PATH,
                config.QUALITY_ENCRYPTION_KEY,
                retention_days=config.QUALITY_RETENTION_DAYS,
                max_query_chars=config.QUALITY_MAX_QUERY_CHARS,
                max_answer_chars=config.QUALITY_MAX_ANSWER_CHARS,
                feedback_limit_per_hour=config.QUALITY_FEEDBACK_LIMIT_PER_HOUR,
            )
        return _QUALITY_STORE


def reset_quality_service() -> None:
    global _QUALITY_STORE
    with _QUALITY_STORE_LOCK:
        _QUALITY_STORE = None
