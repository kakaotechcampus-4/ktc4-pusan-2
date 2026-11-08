"""임베딩 호출 — 자료 검색의 질문 임베딩과 카드 벡터 만들기 (기능 문서 6-10 · 11-1).

- 부르는 곳: `api/chat_agent.py`(질문 임베딩 함수를 코어에 넘김),
  `tools/build_guide_embeddings.py`(카드 벡터 한 번에 만들기). 기능 코어(`features/chat_agent/`)는
  이 파일을 가져오지 않는다. 서비스 계층이 만든 함수만 `TurnDeps.embed_query` 로 받는다.
- 하는 일: `openai` SDK 로 `embeddings.create` 를 부른다(모델 · 주소 · 키는 `OPENAI_EMBEDDING_*`
  묶음, `settings.py`). 재시도하지 않는다(`max_retries=0`): 질문 임베딩은 실행당 한 번
  (계획한 검색어는 묶어서)이고 실패하면 키워드 검색으로
  이어가므로 다시 부르는 시간이 아깝다. 호출마다 시간 제한(초)을 받는다.
  SDK 의 시간 제한은 연결 · 읽기 단계마다라 전체 마감이 아니므로, 질문 임베딩은 별도 스레드에서
  돌려 전체 호출에 마감(`future.result(timeout)`)을 강제한다. 넘기면 `openai.APITimeoutError` 를
  올려(코어가 TIMEOUT 으로 기록하고 키워드 검색으로 이어간다) 기다리지 않는다
  (남은 스레드는 SDK 시간 제한으로 끝난다).
- 실패는 예외로 올린다(시간 초과는 `openai.APITimeoutError`). 로그 · 예외에 입력 글 · 키를
  넣지 않는다.
- 고칠 때: 차원은 `ChatAgentConfig.embed_dimensions` 와 `guides/embeddings.json` 이 같아야 한다.
"""

from __future__ import annotations

import concurrent.futures
import threading
from collections.abc import Callable, Sequence

import httpx
import openai

from pitch_coach_ai.settings import Settings

#: 질문 임베딩 호출을 돌리는 스레드(전체 마감을 강제하려고 호출을 호출 스레드 밖에서 돌린다)
_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="embed")
#: 풀 자리. 마감을 넘긴 호출은 스레드에서 끝까지 돌 수 있으므로(SDK 시간 제한으로 끝난다)
#: 자리가 다 차면 기다리지 않고 바로 시간 초과로 돌려 키워드 검색으로 넘어간다
_SLOTS = threading.BoundedSemaphore(8)


def _client(base_url: str, api_key: str) -> openai.OpenAI:
    return openai.OpenAI(base_url=base_url, api_key=api_key, max_retries=0)


def embed_batch(
    *,
    base_url: str,
    api_key: str,
    model: str,
    dims: int,
    texts: Sequence[str],
    timeout: float = 60.0,
) -> list[list[float]]:
    """글 여러 개를 요청 한 번으로 임베딩한다(입력 순서대로 벡터 목록). 카드 벡터를 만들 때 쓴다."""
    resp = _client(base_url, api_key).embeddings.create(
        model=model, input=list(texts), dimensions=dims, timeout=timeout
    )
    data = sorted(resp.data, key=lambda d: d.index)
    if len(data) != len(texts):
        raise ValueError("embedding count differs from input count")
    return [list(d.embedding) for d in data]


def with_deadline(
    call: Callable[[Sequence[str], float], list[list[float]]],
    texts: Sequence[str],
    timeout: float,
    base_url: str = "",
) -> list[list[float]]:
    """`call` 의 전체 시간에 마감을 강제한다. 넘기면 `openai.APITimeoutError`(내용 없음)."""
    if not _SLOTS.acquire(blocking=False):
        raise openai.APITimeoutError(request=httpx.Request("POST", base_url or "http://embed"))

    try:
        future = _POOL.submit(call, texts, timeout)
    except BaseException:
        _SLOTS.release()
        raise
    # 끝나거나 시작 전에 취소돼도 자리를 돌려준다(완료 콜백은 취소된 Future 에도 불린다)
    future.add_done_callback(lambda _f: _SLOTS.release())
    try:
        return future.result(timeout=max(timeout, 0.0))
    except concurrent.futures.TimeoutError:
        future.cancel()
        raise openai.APITimeoutError(
            request=httpx.Request("POST", base_url or "http://embed")
        ) from None


def embedder(
    *, base_url: str, api_key: str, model: str, dims: int
) -> Callable[[Sequence[str], float], list[list[float]]]:
    """질문 임베딩 함수 `(글 목록, 시간 제한 초) → 벡터 목록(입력 순서)`.

    요청 한 번으로 묶어 보낸다. 실패하면 예외(재시도 없음).
    """
    client = _client(base_url, api_key)

    def call(texts: Sequence[str], timeout: float) -> list[list[float]]:
        resp = client.embeddings.create(
            model=model, input=list(texts), dimensions=dims, timeout=timeout
        )
        data = sorted(resp.data, key=lambda d: d.index)
        if len(data) != len(texts):
            raise ValueError("embedding count differs from input count")
        return [list(d.embedding) for d in data]

    def embed(texts: Sequence[str], timeout: float) -> list[list[float]]:
        return with_deadline(call, texts, timeout, base_url)

    return embed


def make_embedder(
    settings: Settings, dims: int
) -> Callable[[Sequence[str], float], list[list[float]]] | None:
    """임베딩 묶음 설정이 있으면 질문 임베딩 함수, 없으면 None(키워드 검색만)."""
    bundle = settings.bundle("embedding")
    if bundle is None:
        return None
    return embedder(
        base_url=bundle.base_url,
        api_key=bundle.api_key.get_secret_value(),
        model=bundle.model,
        dims=dims,
    )
