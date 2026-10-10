"""LLM 응답 캐시 — 코어의 PlanCache 를 로컬 SQLite 에 대 준다 (outputs/llm_cache.sqlite, git 제외).

같은 질문(메시지 해시 + 계획 해시)을 다시 하면 저장된 답을 쓰고 LLM 을 부르지 않는다 —
두 번째 실행부터는 과금되지 않는다. 같은 질문을 여러 번 물어 흔들림을 잴 때는
반복 번호(sample)를 다르게 준다.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .paths import OUTPUTS_DIR

DEFAULT_PATH = OUTPUTS_DIR / "llm_cache.sqlite"


class SqlitePlanCache:
    """코칭 계획 응답 캐시. sample 은 같은 질문의 반복 번호(0 = 첫 답)."""

    def __init__(self, path: str | Path = DEFAULT_PATH, sample: int = 0) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.sample = sample
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS plan_cache ("
            " key TEXT, planner_hash TEXT, sample INTEGER, output_json TEXT, created_at TEXT,"
            " PRIMARY KEY (key, planner_hash, sample))"
        )

    def with_sample(self, sample: int) -> SqlitePlanCache:
        other = SqlitePlanCache.__new__(SqlitePlanCache)
        other.conn, other.sample = self.conn, sample
        return other

    def get(self, key: str, planner_hash: str) -> str | None:
        row = self.conn.execute(
            "SELECT output_json FROM plan_cache WHERE key = ? AND planner_hash = ? AND sample = ?",
            (key, planner_hash, self.sample),
        ).fetchone()
        return row[0] if row else None

    def put(self, key: str, planner_hash: str, output_json: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO plan_cache VALUES (?, ?, ?, ?, ?)",
                (key, planner_hash, self.sample, output_json, datetime.now(UTC).isoformat()),
            )

    def close(self) -> None:
        self.conn.close()
