"""LLM 호출 공통 도구: 캐시 키(해시), 캐시 프로토콜, 병렬 호출.

코어는 LLM 클라이언트를 만들지 않는다. 출력 스키마가 고정된 LLM(`.invoke(messages)` 가 있는 객체)을
인자로 받는다. 캐시도 인자로 받고, None 이면 캐시 없이 매번 호출한다. 캐시를 어디에 둘지는 실행하는
쪽이 정한다 (research 는 로컬 SQLite, 배포 서버는 상태를 두지 않으므로 None).

대본 분석과 STT 평가는 같은 방식으로 캐시를 쓴다:
설정 해시(프롬프트 · 메시지 형식 · 출력 스키마 · 모델)와 입력 키가 같으면 LLM 을 다시 부르지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from pydantic import BaseModel


class LLMCache(Protocol):
    """LLM 응답 캐시.

    `kind` 는 호출 종류(예: "script.semantic"), `key` 는 입력을 가리키는 해시다.
    """

    def get(self, kind: str, key: str, config_hash: str) -> str | None: ...

    def put(self, kind: str, key: str, config_hash: str, output_json: str) -> None: ...


def config_hash(payload: dict) -> str:
    """프롬프트 · 메시지 형식 · 스키마 · 모델이 바뀌면 달라지는 해시.

    이 값이 다르면 캐시를 쓰지 않는다.
    """
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def message_hash(message: str) -> str:
    """LLM 에 보내는 메시지의 해시. 입력이 같은지를 이 값으로 판단한다."""
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


def cache_get[M: BaseModel](
    cache: LLMCache | None, kind: str, key: str, config_hash: str, model_cls: type[M]
) -> M | None:
    if cache is None:
        return None
    raw = cache.get(kind, key, config_hash)
    return model_cls.model_validate_json(raw) if raw is not None else None


def cache_put(
    cache: LLMCache | None, kind: str, key: str, config_hash: str, output: BaseModel
) -> None:
    if cache is not None:
        cache.put(kind, key, config_hash, output.model_dump_json())


def call_in_parallel(jobs: dict, fn, max_workers: int) -> tuple[dict, dict]:
    """jobs = {i: 인자 튜플} 을 스레드 풀에서 부른다. 성공 결과와 실패 메시지를 나눠 돌려준다."""
    done, errors = {}, {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {i: pool.submit(fn, *args) for i, args in jobs.items()}
        for i, future in futures.items():
            try:
                done[i] = future.result()
            except Exception as e:  # 한 장이 실패해도 나머지 (이미 비용을 낸) 결과는 살린다
                errors[i] = f"{type(e).__name__}: {e}"
    return done, errors


def llm_step[M: BaseModel](
    kind: str,
    messages: dict,
    fn,
    llm,
    config_hash: str,
    model_cls: type[M],
    cache: LLMCache | None = None,
    max_workers: int = 4,
) -> tuple[dict, dict, int]:
    """캐시에 없는 메시지만 병렬로 호출하고 캐시에 남긴다. (결과, 실패, 호출 수)

    `messages` = {키: 메시지}, `fn(message, llm)` 이 LLM 을 한 번 부른다.
    캐시 읽기 · 쓰기는 호출한 스레드에서만 한다 (SQLite 같은 캐시가 스레드 간 공유를 못 해도 되게).
    """
    results = {
        k: cache_get(cache, kind, message_hash(m), config_hash, model_cls)
        for k, m in messages.items()
    }
    jobs = {k: (messages[k], llm) for k, r in results.items() if r is None}
    done, errors = call_in_parallel(jobs, fn, max_workers)
    for k, output in done.items():
        results[k] = output
        cache_put(cache, kind, message_hash(messages[k]), config_hash, output)
    return results, errors, len(jobs)
