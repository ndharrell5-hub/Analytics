"""Tiny SQLite response cache so we never hit a source twice for the same data."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parent.parent / ".cache" / "responses.sqlite"


class ResponseCache:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS responses ("
            " key TEXT PRIMARY KEY, fetched_at REAL NOT NULL, body TEXT NOT NULL)"
        )
        self._db.commit()

    @staticmethod
    def make_key(source: str, endpoint: str, params: dict[str, Any]) -> str:
        return f"{source}:{endpoint}?" + json.dumps(params, sort_keys=True)

    def get(self, key: str, ttl: float | None) -> Any | None:
        """Return the cached body, or None if missing/expired. ttl=None means never expires."""
        row = self._db.execute(
            "SELECT fetched_at, body FROM responses WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        fetched_at, body = row
        if ttl is not None and time.time() - fetched_at > ttl:
            return None
        return json.loads(body)

    def set(self, key: str, body: Any) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO responses (key, fetched_at, body) VALUES (?, ?, ?)",
            (key, time.time(), json.dumps(body)),
        )
        self._db.commit()
