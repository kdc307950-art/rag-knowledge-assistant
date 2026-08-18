"""知识库逻辑版本清单：原子记录每个来源当前可见的完整版本。"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import RUNTIME_DATA_DIR


DEFAULT_MANIFEST_PATH = RUNTIME_DATA_DIR / "kb_manifest.sqlite3"
_MANIFEST_LOCK = threading.RLock()


@dataclass(frozen=True)
class ManifestSnapshot:
    """一次检索使用的不可变逻辑版本快照。"""

    generation: int
    active_revisions: dict[str, str]
    chunk_counts: dict[str, int]
    initialized: bool


class KnowledgeBaseManifest:
    """使用 SQLite 事务切换来源的激活版本，Chroma 仅保存物理分块。"""

    def __init__(self, db_path: Path | str | None = None) -> None:
        # 延迟解析默认路径，便于测试隔离，也避免导入时固化旧环境变量。
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_MANIFEST_PATH

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS source_manifest (
                source TEXT PRIMARY KEY,
                active_revision TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                chunk_count INTEGER NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS manifest_metadata (
                name TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS source_metadata (
                source TEXT PRIMARY KEY,
                metadata_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        return connection

    @staticmethod
    def _generation(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT value FROM manifest_metadata WHERE name = 'generation'"
        ).fetchone()
        return int(row[0]) if row else 0

    def snapshot(self) -> ManifestSnapshot:
        """在一个 SQLite 读事务中捕获来源映射和清单代际。"""
        with _MANIFEST_LOCK:
            connection = self._connect()
            try:
                with connection:
                    generation = self._generation(connection)
                    rows = connection.execute(
                        "SELECT source, active_revision, chunk_count FROM source_manifest"
                    ).fetchall()
                    initialized = connection.execute(
                        "SELECT value FROM manifest_metadata WHERE name = 'schema_initialized'"
                    ).fetchone()
                return ManifestSnapshot(
                    generation=generation,
                    active_revisions={
                        str(source): str(revision) for source, revision, _count in rows
                    },
                    chunk_counts={
                        str(source): int(count) for source, _revision, count in rows
                    },
                    initialized=bool(initialized and initialized[0] == "1"),
                )
            finally:
                connection.close()

    def get_source(self, source: str) -> dict | None:
        """读取单个来源的激活版本、内容摘要和已提交分块数。"""
        with _MANIFEST_LOCK:
            connection = self._connect()
            try:
                row = connection.execute(
                    """
                    SELECT active_revision, content_hash, chunk_count
                    FROM source_manifest WHERE source = ?
                    """,
                    (source,),
                ).fetchone()
                if row is None:
                    return None
                return {
                    "active_revision": str(row[0]),
                    "content_hash": str(row[1]),
                    "chunk_count": int(row[2]),
                }
            finally:
                connection.close()

    def get_source_metadata(self, source: str) -> dict:
        """Read governance metadata stored alongside the active source pointer."""
        with _MANIFEST_LOCK:
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT metadata_json FROM source_metadata WHERE source = ?",
                    (source,),
                ).fetchone()
                if row is None:
                    return {}
                payload = json.loads(str(row[0]))
                return payload if isinstance(payload, dict) else {}
            finally:
                connection.close()

    def set_source_metadata(self, source: str, metadata: dict) -> None:
        """Persist validated source governance metadata without changing generation."""
        payload = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        with _MANIFEST_LOCK:
            connection = self._connect()
            try:
                with connection:
                    connection.execute(
                        """
                        INSERT INTO source_metadata(source, metadata_json, updated_at)
                        VALUES(?, ?, ?)
                        ON CONFLICT(source) DO UPDATE SET
                            metadata_json = excluded.metadata_json,
                            updated_at = excluded.updated_at
                        """,
                        (source, payload, time.time()),
                    )
            finally:
                connection.close()

    def commit_source(
        self,
        source: str,
        revision_id: str,
        content_hash: str,
        chunk_count: int,
    ) -> int:
        """原子切换来源版本并递增逻辑清单代际。"""
        with _MANIFEST_LOCK:
            connection = self._connect()
            try:
                with connection:
                    connection.execute(
                        """
                        INSERT INTO source_manifest(
                            source, active_revision, content_hash, chunk_count, updated_at
                        ) VALUES(?, ?, ?, ?, ?)
                        ON CONFLICT(source) DO UPDATE SET
                            active_revision = excluded.active_revision,
                            content_hash = excluded.content_hash,
                            chunk_count = excluded.chunk_count,
                            updated_at = excluded.updated_at
                        """,
                        (source, revision_id, content_hash, int(chunk_count), time.time()),
                    )
                    connection.execute(
                        """
                        INSERT INTO manifest_metadata(name, value) VALUES('schema_initialized', '1')
                        ON CONFLICT(name) DO UPDATE SET value = excluded.value
                        """
                    )
                    generation = self._generation(connection) + 1
                    connection.execute(
                        """
                        INSERT INTO manifest_metadata(name, value) VALUES('generation', ?)
                        ON CONFLICT(name) DO UPDATE SET value = excluded.value
                        """,
                        (str(generation),),
                    )
                return generation
            finally:
                connection.close()

    def delete_source(self, source: str) -> bool:
        """原子删除来源指针；物理分块可随后尽力清理。"""
        with _MANIFEST_LOCK:
            connection = self._connect()
            try:
                with connection:
                    cursor = connection.execute(
                        "DELETE FROM source_manifest WHERE source = ?", (source,)
                    )
                    connection.execute(
                        "DELETE FROM source_metadata WHERE source = ?", (source,)
                    )
                    changed = cursor.rowcount > 0
                    if changed:
                        generation = self._generation(connection) + 1
                        connection.execute(
                            """
                            INSERT INTO manifest_metadata(name, value) VALUES('generation', ?)
                            ON CONFLICT(name) DO UPDATE SET value = excluded.value
                            """,
                            (str(generation),),
                        )
                    connection.execute(
                        """
                        INSERT INTO manifest_metadata(name, value) VALUES('schema_initialized', '1')
                        ON CONFLICT(name) DO UPDATE SET value = excluded.value
                        """
                    )
                return changed
            finally:
                connection.close()

    def clear(self) -> bool:
        """原子清空全部来源指针并递增清单代际。"""
        with _MANIFEST_LOCK:
            connection = self._connect()
            try:
                with connection:
                    count = connection.execute(
                        "SELECT COUNT(*) FROM source_manifest"
                    ).fetchone()[0]
                    connection.execute("DELETE FROM source_manifest")
                    connection.execute("DELETE FROM source_metadata")
                    connection.execute(
                        """
                        INSERT INTO manifest_metadata(name, value) VALUES('schema_initialized', '1')
                        ON CONFLICT(name) DO UPDATE SET value = excluded.value
                        """
                    )
                    if count:
                        generation = self._generation(connection) + 1
                        connection.execute(
                            """
                            INSERT INTO manifest_metadata(name, value) VALUES('generation', ?)
                            ON CONFLICT(name) DO UPDATE SET value = excluded.value
                            """,
                            (str(generation),),
                        )
                return bool(count)
            finally:
                connection.close()

    def mark_initialized(self) -> None:
        """记录迁移已完成；即使当前为空，也不能在重启后复活物理垃圾。"""
        with _MANIFEST_LOCK:
            connection = self._connect()
            try:
                with connection:
                    connection.execute(
                        """
                        INSERT INTO manifest_metadata(name, value) VALUES('schema_initialized', '1')
                        ON CONFLICT(name) DO UPDATE SET value = excluded.value
                        """
                    )
            finally:
                connection.close()


def get_manifest() -> KnowledgeBaseManifest:
    """创建轻量清单访问器；连接只在单次操作内持有。"""
    return KnowledgeBaseManifest()
