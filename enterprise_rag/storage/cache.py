"""基于 SQLite、支持 TTL 过期、LRU 淘汰机制与容量代际统计的问答缓存."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Callable

from ..config import RUNTIME_DATA_DIR

DEFAULT_CACHE_PATH = RUNTIME_DATA_DIR / "answer_cache.sqlite3"


class PersistentAnswerCache:
    """适用于单例Streamlit部署的小型进程安全缓存."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        max_entries: int = 100,
        ttl_seconds: int = 86_400,
        clock: Callable[[], float] = time.time,
    ) -> None:
        # 延迟解析默认路径，测试和部署可在实例化前安全替换运行目录。
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_CACHE_PATH
        self.max_entries = max(1, int(max_entries))
        self.ttl_seconds = max(1, int(ttl_seconds))
        self._clock = clock

    def _connect(self) -> sqlite3.Connection:
        # WAL 模式允许读取与短事务写入并存，适合单进程 Streamlit 部署。
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS answer_cache (
                cache_key TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                expires_at REAL NOT NULL,
                last_accessed REAL NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        return connection

    def _connect_existing(self) -> sqlite3.Connection | None:
        if not self.db_path.exists():
            return None
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def get(self, cache_key: str) -> dict | None:
        # 命中过期项时删除；命中有效项时更新访问时间以实现 LRU。
        now = self._clock()
        connection = self._connect_existing()
        if connection is None:
            return None
        try:
            with connection:
                row = connection.execute(
                    "SELECT payload, expires_at FROM answer_cache WHERE cache_key = ?",
                    (cache_key,),
                ).fetchone()
                if row is None:
                    return None
                payload, expires_at = row
                if float(expires_at) <= now:
                    connection.execute("DELETE FROM answer_cache WHERE cache_key = ?", (cache_key,))
                    return None
                connection.execute(
                    "UPDATE answer_cache SET last_accessed = ? WHERE cache_key = ?",
                    (now, cache_key),
                )
            value = json.loads(payload)
            return value if isinstance(value, dict) else None
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
            return None
        finally:
            connection.close()

    def set(self, cache_key: str, value: dict) -> None:
        # 先清除 TTL 过期项，再按最近最少使用原则淘汰超出容量的条目。
        now = self._clock()
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        connection = self._connect()
        try:
            with connection:
                connection.execute("DELETE FROM answer_cache WHERE expires_at <= ?", (now,))
                connection.execute(
                    """
                    INSERT INTO answer_cache(cache_key, payload, expires_at, last_accessed, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        payload = excluded.payload,
                        expires_at = excluded.expires_at,
                        last_accessed = excluded.last_accessed
                    """,
                    (cache_key, payload, now + self.ttl_seconds, now, now),
                )
                overflow = connection.execute("SELECT COUNT(*) FROM answer_cache").fetchone()[0] - self.max_entries
                if overflow > 0:
                    connection.execute(
                        """
                        DELETE FROM answer_cache
                        WHERE cache_key IN (
                            SELECT cache_key FROM answer_cache
                            ORDER BY last_accessed ASC, created_at ASC
                            LIMIT ?
                        )
                        """,
                        (overflow,),
                    )
        finally:
            connection.close()

    def delete(self, cache_key: str) -> None:
        """删除指定缓存项，用于代际竞态下回滚刚写入的旧答案。"""
        connection = self._connect_existing()
        if connection is None:
            return
        try:
            with connection:
                connection.execute("DELETE FROM answer_cache WHERE cache_key = ?", (cache_key,))
        finally:
            connection.close()
