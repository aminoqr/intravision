"""SQLite store sitting between the fetcher and the renderer.

The renderer only ever reads from here. That decoupling is what keeps page loads
free of API quota and keeps the TV alive when the 42 API is down.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    name       TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

-- Retained so panels that need deltas (level-up milestones) have something to diff.
CREATE TABLE IF NOT EXISTS snapshot_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    payload    TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_name_time
    ON snapshot_history (name, fetched_at DESC);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL lets the renderer read while the fetcher writes.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def put(self, name: str, payload: Any, keep_history: bool = False) -> None:
        blob = json.dumps(payload, ensure_ascii=False)
        now = utcnow()
        with self._conn:
            self._conn.execute(
                "INSERT INTO snapshots (name, payload, fetched_at) VALUES (?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET payload=excluded.payload, "
                "fetched_at=excluded.fetched_at",
                (name, blob, now),
            )
            if keep_history:
                self._conn.execute(
                    "INSERT INTO snapshot_history (name, payload, fetched_at) VALUES (?, ?, ?)",
                    (name, blob, now),
                )

    def get(self, name: str, default: Any = None) -> Any:
        row = self._conn.execute(
            "SELECT payload FROM snapshots WHERE name = ?", (name,)
        ).fetchone()
        return json.loads(row["payload"]) if row else default

    def fetched_at(self, name: str) -> str | None:
        row = self._conn.execute(
            "SELECT fetched_at FROM snapshots WHERE name = ?", (name,)
        ).fetchone()
        return row["fetched_at"] if row else None

    def previous(self, name: str, before: str) -> Any:
        """Most recent historical snapshot older than `before`. Used for level-up diffs."""
        row = self._conn.execute(
            "SELECT payload FROM snapshot_history WHERE name = ? AND fetched_at < ? "
            "ORDER BY fetched_at DESC LIMIT 1",
            (name, before),
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def prune_history(self, name: str, keep: int = 48) -> None:
        with self._conn:
            self._conn.execute(
                "DELETE FROM snapshot_history WHERE name = ? AND id NOT IN "
                "(SELECT id FROM snapshot_history WHERE name = ? "
                " ORDER BY fetched_at DESC LIMIT ?)",
                (name, name, keep),
            )

    def set_meta(self, key: str, value: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def close(self) -> None:
        self._conn.close()
