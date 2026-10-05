"""캐시 키와 병렬 호출 도구.

캐시 키는 archive 노트북이 만든 캐시를 그대로 쓰기 위해 계산 방식이 같아야 한다.
"""

import hashlib
import json

from pydantic import BaseModel

from script_coverage.shared.llm_step import (
    cache_get,
    cache_put,
    call_in_parallel,
    config_hash,
    llm_step,
    message_hash,
)


class Out(BaseModel):
    text: str


class DictCache:
    def __init__(self):
        self.rows: dict[tuple[str, str, str], str] = {}

    def get(self, kind, key, config_hash):
        return self.rows.get((kind, key, config_hash))

    def put(self, kind, key, config_hash, output_json):
        self.rows[(kind, key, config_hash)] = output_json


def test_config_hash_matches_notebook_formula():
    payload = {"model": "m", "prompt": "프롬프트", "schema": {"b": 1, "a": 2}}
    expected = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    assert config_hash(payload) == expected
    assert len(config_hash(payload)) == 12


def test_message_hash_is_full_sha256():
    assert message_hash("안녕") == hashlib.sha256("안녕".encode()).hexdigest()


def test_cache_none_is_noop():
    assert cache_get(None, "k", "key", "h", Out) is None
    cache_put(None, "k", "key", "h", Out(text="x"))  # 에러 없이 지나간다


def test_cache_round_trip():
    cache = DictCache()
    cache_put(cache, "kind", "key", "h", Out(text="값"))
    assert cache_get(cache, "kind", "key", "h", Out) == Out(text="값")
    assert cache_get(cache, "kind", "key", "other", Out) is None


def test_call_in_parallel_keeps_successes_and_errors():
    def fn(x):
        if x < 0:
            raise ValueError("음수")
        return x * 2

    done, errors = call_in_parallel({"a": (1,), "b": (-1,), "c": (3,)}, fn, 2)
    assert done == {"a": 2, "c": 6}
    assert errors == {"b": "ValueError: 음수"}


def test_llm_step_calls_only_uncached_and_stores_results():
    cache = DictCache()
    cache_put(cache, "kind", message_hash("hit"), "h", Out(text="cached"))
    calls = []

    def fn(message, llm):
        calls.append(message)
        return Out(text=f"{llm}:{message}")

    results, errors, n_calls = llm_step(
        "kind", {1: "hit", 2: "miss"}, fn, "llm", "h", Out, cache=cache
    )
    assert calls == ["miss"]
    assert n_calls == 1
    assert errors == {}
    assert results == {1: Out(text="cached"), 2: Out(text="llm:miss")}
    assert cache_get(cache, "kind", message_hash("miss"), "h", Out) == Out(text="llm:miss")
