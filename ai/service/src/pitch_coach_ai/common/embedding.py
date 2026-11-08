"""임베딩 호출 — 자료 검색의 질문 임베딩과 카드 벡터 만들기 (기능 문서 6-10 · 11-1).

- 부르는 곳: `api/chat_agent.py`(질문 임베딩 함수를 코어에 넘김),
  `tools/build_guide_embeddings.py`(카드 벡터 한 번에 만들기). 기능 코어(`features/chat_agent/`)는
  이 파일을 가져오지 않는다. 서비스 계층이 만든 함수만 `TurnDeps.embed_query` 로 받는다.
- 하는 일: `openai` SDK 로 `embeddings.create` 를 부른다(모델 · 주소 · 키는 `OPENAI_EMBEDDING_*`
  묶음, `settings.py`). 재시도하지 않는다(`max_retries=0`): 질문 임베딩은 실행당 한 번
  (계획한 검색어는 묶어서)이고 실패하면 키워드 검색으로
  이어가므로 다시 부르는 시간이 아깝다. 호출마다 시간 제한(초)을 받는다.
  SDK 의 시간 제한은 연결 · 읽기 단계마다라, 작은 요청의 총 시간은 그 값에 가깝다.
- 실패는 예외로 올린다(시간 초과는 `openai.APITimeoutError`). 로그 · 예외에 입력 글 · 키를
  넣지 않는다.
- 고칠 때: 차원은 `ChatAgentConfig.embed_dimensions` 와 `guides/embeddings.json` 이 같아야 한다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import openai

from pitch_coach_ai.settings import Settings


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


def embedder(
    *, base_url: str, api_key: str, model: str, dims: int
) -> Callable[[Sequence[str], float], list[list[float]]]:
    """질문 임베딩 함수 `(글 목록, 시간 제한 초) → 벡터 목록(입력 순서)`.

    요청 한 번으로 묶어 보낸다. 실패하면 예외(재시도 없음).
    """
    client = _client(base_url, api_key)

    def embed(texts: Sequence[str], timeout: float) -> list[list[float]]:
        resp = client.embeddings.create(
            model=model, input=list(texts), dimensions=dims, timeout=timeout
        )
        data = sorted(resp.data, key=lambda d: d.index)
        if len(data) != len(texts):
            raise ValueError("embedding count differs from input count")
        return [list(d.embedding) for d in data]

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
