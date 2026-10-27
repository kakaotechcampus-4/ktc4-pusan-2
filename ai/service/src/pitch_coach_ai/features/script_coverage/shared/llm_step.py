"""LLM 호출 공통 도구: 캐시 키(해시), 캐시 프로토콜, 병렬 호출, 호출 실패의 에러 코드.

코어는 LLM 클라이언트를 만들지 않는다. 출력 스키마가 고정된 LLM(`.invoke(messages)` 가 있는 객체)을
인자로 받는다. 캐시도 인자로 받고, None 이면 캐시 없이 매번 호출한다. 캐시를 어디에 둘지는 실행하는
쪽이 정한다 (research 는 로컬 SQLite, 배포 서버는 상태를 두지 않으므로 None).

대본 분석과 STT 평가는 같은 방식으로 캐시를 쓴다:
설정 해시(프롬프트 · 메시지 형식 · 출력 스키마 · 모델)와 입력 키가 같으면 LLM 을 다시 부르지 않는다.

호출이 실패하면 예외 객체를 그대로 돌려주고, `llm_error_code` 가 API 에러 코드(`LLM_TIMEOUT` 등)로 바꾼다.
응답에 싣는 문구 `ERROR_MESSAGES` 는 코드마다 정해진 문장이라 예외 내용 · 대본 · STT 원문이 들어가지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from pydantic import BaseModel, ValidationError

# 서비스 `common/errors.py` 의 LLM 에러 코드와 같은 값 · 같은 문장이다 (architecture.md 4절)
LLM_ERROR_CODES = ("LLM_TIMEOUT", "LLM_ERROR", "LLM_INVALID_OUTPUT", "LLM_UNAVAILABLE")
ERROR_MESSAGES = {
    "LLM_TIMEOUT": "LLM 응답 시간이 초과되었습니다.",
    "LLM_ERROR": "LLM 호출에 실패했습니다.",
    "LLM_INVALID_OUTPUT": "LLM 출력이 정해진 형식과 다릅니다.",
    "LLM_UNAVAILABLE": "지금은 LLM 을 쓸 수 없습니다.",
}


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


def llm_error_code(exc: BaseException) -> str:
    """LLM 호출에서 난 예외를 에러 코드로 바꾼다. 예외 내용은 코드에 영향을 주지 않는다.

    ① 예외가 `code` 속성으로 LLM 에러 코드를 갖고 있으면 그 값 (서비스의 `LlmError` 하위 예외)
    ② 시간 초과 (`TimeoutError`) → LLM_TIMEOUT
    ③ 출력이 스키마와 다름 (pydantic 검증 오류 · LangChain 의 `OutputParserException`) → LLM_INVALID_OUTPUT
    ④ 그 밖 → LLM_ERROR. LangChain 은 import 하지 않고 클래스 이름으로 알아본다.
    """
    code = getattr(exc, "code", None)
    if code in LLM_ERROR_CODES:
        return code
    if isinstance(exc, TimeoutError):  # concurrent.futures.TimeoutError 도 같은 클래스다
        return "LLM_TIMEOUT"
    if isinstance(exc, ValidationError) or any(
        cls.__name__ == "OutputParserException" for cls in type(exc).__mro__
    ):
        return "LLM_INVALID_OUTPUT"
    return "LLM_ERROR"


def call_in_parallel(jobs: dict, fn, max_workers: int) -> tuple[dict, dict]:
    """jobs = {i: 인자 튜플} 을 스레드 풀에서 부른다. 성공 결과와 실패한 예외 객체를 나눠 돌려준다."""
    done, errors = {}, {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {i: pool.submit(fn, *args) for i, args in jobs.items()}
        for i, future in futures.items():
            try:
                done[i] = future.result()
            except Exception as e:  # 한 장이 실패해도 나머지 (이미 비용을 낸) 결과는 살린다
                errors[i] = e
    return done, errors


def record_failures(
    failed: dict[int, str], causes: dict[int, str], errors: dict[int, BaseException], stage: str
) -> None:
    """장별 실패를 `stats` 에 남긴다. `failed[장] = 에러 코드`, `causes[장] = "<단계> <예외 클래스 이름>"`.

    causes 는 로그용이다. 예외 메시지에는 대본 · STT 원문이 들어갈 수 있어 클래스 이름만 남긴다.
    """
    for number, exc in errors.items():
        failed[number] = llm_error_code(exc)
        causes[number] = f"{stage} {type(exc).__name__}"


def llm_step[M: BaseModel](
    kind: str,
    messages: dict,
    fn,
    llm,
    config_hash: str,
    model_cls: type[M],
    cache: LLMCache | None = None,
    max_workers: int = 4,
    check=None,
) -> tuple[dict, dict, int]:
    """캐시에 없는 메시지만 병렬로 호출하고 캐시에 남긴다. (결과, 실패한 예외, 호출 수)

    `messages` = {키: 메시지}, `fn(message, llm)` 이 LLM 을 한 번 부른다.
    캐시 읽기 · 쓰기는 호출한 스레드에서만 한다 (SQLite 같은 캐시가 스레드 간 공유를 못 해도 되게).
    `check(키, 출력)` 이 있으면 출력이 쓸 만한지 검사한다. 예외를 내면 그 키는 실패이고 캐시에
    남기지 않는다. 캐시에 있던 출력이 검사에 걸리면 없는 것으로 보고 다시 부른다 — 잘못된 출력이
    캐시에 남아 다시 불러도 회복되지 않는 일을 막는다.
    """

    def usable(k, output) -> bool:
        if output is None or check is None:
            return output is not None
        try:
            check(k, output)
        except Exception:
            return False
        return True

    results = {
        k: cache_get(cache, kind, message_hash(m), config_hash, model_cls)
        for k, m in messages.items()
    }
    jobs = {k: (messages[k], llm) for k, r in results.items() if not usable(k, r)}
    for k in jobs:
        results[k] = None
    done, errors = call_in_parallel(jobs, fn, max_workers)
    for k, output in done.items():
        if check is not None:
            try:
                check(k, output)
            except Exception as e:
                errors[k] = e
                continue
        results[k] = output
        cache_put(cache, kind, message_hash(messages[k]), config_hash, output)
    return results, errors, len(jobs)
