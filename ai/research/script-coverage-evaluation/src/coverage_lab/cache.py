"""LLM 응답 캐시: 코어의 `LLMCache` 를 노트북이 쓰던 SQLite 표에 대 준다.

코어가 쓰는 호출 종류(kind)가 노트북의 어느 표에 해당하는지:

| kind | 표 | 키 · 설정 해시 열 |
|---|---|---|
| `script.semantic` | `semantic_cache` | `content_hash` · `llm_config_hash` |
| `script.semantic#k` (반복 k번째, k ≥ 1) | `semantic_samples` (`sample_index` = k) | 같음 |
| `script.final` | `final_cache` | `input_hash` · `final_config_hash` |
| `script.units` | `unit_cache` | `input_hash` · `unit_config_hash` |
| `stt.semantic` / `stt.verifier` (+ `#k`) | `stt_llm_cache` (`kind` = `semantic` / `verifier` + `#k`) | `input_hash` · `config_hash` |

그래서 이 노트북들이 남긴 DB 의 캐시를 그대로 읽고 이어 쓸 수 있다.
"""

import sqlite3
from datetime import UTC, datetime

from script_coverage.shared.llm_step import LLMCache

# 표가 하나인 종류: kind -> (표, 키 열, 설정 해시 열)
_SIMPLE_TABLES = {
    "script.final": ("final_cache", "input_hash", "final_config_hash"),
    "script.units": ("unit_cache", "input_hash", "unit_config_hash"),
}


class SqliteLLMCache:
    """SQLite 연결 하나에 대는 `LLMCache`. 연결은 만든 스레드에서만 쓴다 (코어도 캐시를 메인 스레드에서만 건드린다)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, kind: str, key: str, config_hash: str) -> str | None:
        base, _, sample = kind.partition("#")
        if base == "script.semantic":
            if sample:
                row = self.conn.execute(
                    "SELECT output_json FROM semantic_samples WHERE content_hash = ? AND llm_config_hash = ? AND sample_index = ?",
                    (key, config_hash, int(sample)),
                ).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT output_json FROM semantic_cache WHERE content_hash = ? AND llm_config_hash = ?",
                    (key, config_hash),
                ).fetchone()
        elif base in _SIMPLE_TABLES:
            table, key_col, config_col = _SIMPLE_TABLES[base]
            row = self.conn.execute(
                f"SELECT output_json FROM {table} WHERE {key_col} = ? AND {config_col} = ?",
                (key, config_hash),
            ).fetchone()
        elif base in ("stt.semantic", "stt.verifier"):
            row = self.conn.execute(
                "SELECT output_json FROM stt_llm_cache WHERE kind = ? AND input_hash = ? AND config_hash = ?",
                (_stt_kind(base, sample), key, config_hash),
            ).fetchone()
        else:
            raise KeyError(f"알 수 없는 캐시 종류: {kind}")
        return row[0] if row else None

    def put(self, kind: str, key: str, config_hash: str, output_json: str) -> None:
        now = datetime.now(UTC).isoformat()
        base, _, sample = kind.partition("#")
        if base == "script.semantic":
            if sample:
                self.conn.execute(
                    "INSERT OR REPLACE INTO semantic_samples VALUES (?, ?, ?, ?, ?)",
                    (key, config_hash, int(sample), output_json, now),
                )
            else:
                self.conn.execute(
                    "INSERT OR REPLACE INTO semantic_cache VALUES (?, ?, ?, ?)",
                    (key, config_hash, output_json, now),
                )
        elif base in _SIMPLE_TABLES:
            self.conn.execute(
                f"INSERT OR REPLACE INTO {_SIMPLE_TABLES[base][0]} VALUES (?, ?, ?, ?)",
                (key, config_hash, output_json, now),
            )
        elif base in ("stt.semantic", "stt.verifier"):
            self.conn.execute(
                "INSERT OR REPLACE INTO stt_llm_cache VALUES (?, ?, ?, ?, ?)",
                (_stt_kind(base, sample), key, config_hash, output_json, now),
            )
        else:
            raise KeyError(f"알 수 없는 캐시 종류: {kind}")
        self.conn.commit()


def _stt_kind(base: str, sample: str) -> str:
    """`stt.semantic` + 반복 번호 -> 노트북의 `stt_llm_cache.kind` (`semantic`, `semantic#1` …)."""
    return base.split(".", 1)[1] + (f"#{sample}" if sample else "")


class SampleCache:
    """반복 실행용: sample > 0 이면 모든 kind 에 `#{sample}` 을 붙여, 0번(기본) 응답과 따로 캐시한다.

    sample = 0 이면 아무것도 바꾸지 않는다. 같은 입력을 여러 번 불러 LLM 응답이 얼마나 흔들리는지 잴 때 쓴다.
    """

    def __init__(self, base: LLMCache, sample: int = 0):
        self.base = base
        self.suffix = f"#{sample}" if sample else ""

    def get(self, kind: str, key: str, config_hash: str) -> str | None:
        return self.base.get(kind + self.suffix, key, config_hash)

    def put(self, kind: str, key: str, config_hash: str, output_json: str) -> None:
        self.base.put(kind + self.suffix, key, config_hash, output_json)
