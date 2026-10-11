"""SqliteLLMCache: 코어의 캐시 종류(kind)가 노트북의 표로 이어지는지, SampleCache 의 kind 접미사."""

import sqlite3

import pytest

from coverage_lab.cache import SampleCache, SqliteLLMCache
from coverage_lab.store import connect


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "t.sqlite")
    yield connection
    connection.close()


def _rows(conn: sqlite3.Connection, table: str) -> list[tuple]:
    return conn.execute(f"SELECT * FROM {table}").fetchall()


@pytest.mark.parametrize(
    ("kind", "table"),
    [
        ("script.semantic", "semantic_cache"),
        ("script.final", "final_cache"),
        ("script.units", "unit_cache"),
    ],
)
def test_script_kinds_use_notebook_tables(conn, kind, table):
    cache = SqliteLLMCache(conn)
    assert cache.get(kind, "k", "cfg") is None
    cache.put(kind, "k", "cfg", '{"a": 1}')
    assert cache.get(kind, "k", "cfg") == '{"a": 1}'
    assert cache.get(kind, "k", "other-config") is None  # 설정 해시가 다르면 다른 항목
    [row] = _rows(conn, table)
    assert row[:3] == ("k", "cfg", '{"a": 1}')


def test_semantic_sample_goes_to_samples_table(conn):
    cache = SqliteLLMCache(conn)
    cache.put("script.semantic#2", "h", "cfg", "{}")
    assert cache.get("script.semantic#2", "h", "cfg") == "{}"
    # 0번 응답은 semantic_cache 에 따로 있다
    assert cache.get("script.semantic", "h", "cfg") is None
    assert cache.get("script.semantic#1", "h", "cfg") is None
    assert _rows(conn, "semantic_cache") == []
    [row] = _rows(conn, "semantic_samples")
    assert row[:4] == ("h", "cfg", 2, "{}")


@pytest.mark.parametrize(
    ("kind", "stored_kind"),
    [
        ("stt.semantic", "semantic"),
        ("stt.verifier", "verifier"),
        ("stt.semantic#1", "semantic#1"),
        ("stt.verifier#2", "verifier#2"),
    ],
)
def test_stt_kinds_use_stt_llm_cache(conn, kind, stored_kind):
    cache = SqliteLLMCache(conn)
    cache.put(kind, "msg-hash", "cfg", "{}")
    assert cache.get(kind, "msg-hash", "cfg") == "{}"
    [row] = _rows(conn, "stt_llm_cache")
    assert row[:4] == (stored_kind, "msg-hash", "cfg", "{}")


def test_put_replaces_existing_entry(conn):
    cache = SqliteLLMCache(conn)
    cache.put("script.final", "k", "cfg", "old")
    cache.put("script.final", "k", "cfg", "new")
    assert cache.get("script.final", "k", "cfg") == "new"
    assert len(_rows(conn, "final_cache")) == 1


def test_unknown_kind_is_rejected(conn):
    cache = SqliteLLMCache(conn)
    with pytest.raises(KeyError):
        cache.get("script.other", "k", "cfg")
    with pytest.raises(KeyError):
        cache.put("other", "k", "cfg", "{}")


class _Recorder:
    def __init__(self):
        self.calls: list[tuple] = []

    def get(self, kind, key, config_hash):
        self.calls.append(("get", kind))
        return None

    def put(self, kind, key, config_hash, output_json):
        self.calls.append(("put", kind))


def test_sample_cache_suffixes_kind_only_when_sample_is_positive():
    base = _Recorder()
    SampleCache(base, 0).get("stt.semantic", "k", "c")
    SampleCache(base, 3).get("stt.semantic", "k", "c")
    SampleCache(base, 3).put("script.semantic", "k", "c", "{}")
    assert base.calls == [
        ("get", "stt.semantic"),
        ("get", "stt.semantic#3"),
        ("put", "script.semantic#3"),
    ]


def test_sample_cache_reads_and_writes_samples_through_sqlite(conn):
    base = SqliteLLMCache(conn)
    SampleCache(base, 1).put("stt.verifier", "m", "c", "{}")
    assert SampleCache(base, 1).get("stt.verifier", "m", "c") == "{}"
    assert SampleCache(base, 0).get("stt.verifier", "m", "c") is None
    assert _rows(conn, "stt_llm_cache")[0][0] == "verifier#1"
