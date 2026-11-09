"""자료 카드의 임베딩 파일 `guides/embeddings.json` 을 만든다 (기능 문서 6-10 '하이브리드 검색').

- 사용: `ai/service` 에서
  `uv run python tools/build_guide_embeddings.py [--dimensions 1536] [--out <파일>]`.
  `ai/.env` 의 `OPENAI_EMBEDDING_*`(주소 · 키 · 모델)를 `settings.py` 와 같은 방식으로 읽는다.
  키는 출력하지 않는다.
- 하는 일: 자료 묶음의 **모든 카드**(검수 완료 · 검수 전 모두, 연구의 `--corpus all` 이 쓴다)의
  임베딩 입력(`corpus.card_embed_input`: 제목 · 주제어 · 본문)을 요청 한 번으로 임베딩해
  `{model, dimensions, cards: {source_id: {input_sha256, vector}}}` 로 쓴다(벡터는 소수 여섯 자리,
  줄 끝 LF). 서비스는 이 파일을 읽을 때 모델 · 차원 · 카드별 입력 해시가 맞는지 검사하고
  (`parse_card_vectors`), 어긋나면 키워드 검색만 한다.
- 이 파일은 자료 묶음 판(`manifest.json`)에 들지 않는다. 카드 본문 · 제목 · 주제어를 고쳤으면
  이 도구를 다시 돌려 같은 PR 에 넣는다.
- 요금: 카드 47장을 한 번 = 비용이 아주 작다(임베딩 토큰 단가).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pitch_coach_ai.common.embedding import embed_batch
from pitch_coach_ai.features import chat_agent as core_pkg
from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG
from pitch_coach_ai.features.chat_agent.corpus import (
    build_corpus,
    card_embed_input,
    card_embed_sha256,
)
from pitch_coach_ai.settings import Settings

GUIDES = Path(core_pkg.__file__).parent / "guides"
DEFAULT_OUT = GUIDES / "embeddings.json"


def render(model: str, dims: int, rows: list[tuple[str, str, list[float]]]) -> str:
    """파일 본문(카드마다 한 줄, 키는 source_id 순). 마지막 줄바꿈 포함."""
    lines = []
    for sid, sha, vec in sorted(rows):
        body = json.dumps(
            {"input_sha256": sha, "vector": [round(x, 6) for x in vec]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        lines.append(f"{json.dumps(sid)}:{body}")
    head = f'{{"model":{json.dumps(model)},"dimensions":{dims},"cards":{{\n'
    return head + ",\n".join(lines) + "\n}}\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="자료 카드 임베딩 파일 만들기")
    ap.add_argument("--dimensions", type=int, default=DEFAULT_CONFIG.embed_dimensions)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    bundle = Settings().bundle("embedding")
    if bundle is None:
        print("OPENAI_EMBEDDING_* 설정(ai/.env)이 없습니다", file=sys.stderr)
        return 2
    manifest = json.loads((GUIDES / "manifest.json").read_text(encoding="utf-8"))
    files = {
        name: json.loads((GUIDES / name).read_text(encoding="utf-8")) for name in manifest["files"]
    }
    corpus = build_corpus(manifest, files, cfg=DEFAULT_CONFIG, allow_unreviewed=True)
    cards = list(corpus.cards)
    try:
        vectors = embed_batch(
            base_url=bundle.base_url,
            api_key=bundle.api_key.get_secret_value(),
            model=bundle.model,
            dims=a.dimensions,
            texts=[card_embed_input(c) for c in cards],
        )
    except Exception as exc:  # noqa: BLE001  # SDK 오류 원문 · 응답 본문에 비밀값이 있을 수 있어 클래스 이름만 출력한다
        print(f"임베딩 호출 실패: {type(exc).__name__}", file=sys.stderr)
        return 1
    rows = [(c.source_id, card_embed_sha256(c), v) for c, v in zip(cards, vectors, strict=True)]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_bytes(render(bundle.model, a.dimensions, rows).encode("utf-8"))
    print(f"{a.out} (카드 {len(rows)}장, 모델 {bundle.model}, 차원 {a.dimensions})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
