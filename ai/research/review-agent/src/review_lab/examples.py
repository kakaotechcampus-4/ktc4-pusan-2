"""예시 파일의 LLM 성공 예시 만들기 (실제 LLM, ai/.env, 과금).

    python -m review_lab.examples

examples/review/request.json 으로 실제 LLM 을 한 번 불러 두 파일을 쓴다.
- llm_output.json: LLM 출력(ReviewDraft) 그대로.
  가짜 LLM 이 이 답을 돌려주면 response_llm.json 이 된다
- response_llm.json: LLM 성공 응답 (fallback_reason null, meta.model 은 응답한 모델)

response.json 은 LLM 이 없을 때의 응답(기본 결과, LLM_UNAVAILABLE)이다.
LLM 응답은 outputs/llm_cache.json 에 캐시해, 같은 프롬프트 · 모델 · 입력이면 다시 부르지 않는다.
"""

from __future__ import annotations

import json
from typing import Any

from review import review_take

from .llm import CachedLLM, load_settings, review_llm
from .paths import EXAMPLES_DIR, OUTPUTS_DIR


def write_json(name: str, data: Any) -> None:
    path = EXAMPLES_DIR / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"→ {path}")


def main() -> None:
    settings = load_settings()
    llm = CachedLLM(review_llm(settings), settings.model, OUTPUTS_DIR / "llm_cache.json")
    request = json.loads((EXAMPLES_DIR / "request.json").read_text(encoding="utf-8"))
    response = review_take(request, llm, model=settings.model)
    if response.fallback_reason is not None or llm.last_draft is None:
        raise RuntimeError(f"LLM 이 답하지 않았다 (fallback_reason={response.fallback_reason})")
    write_json("llm_output.json", llm.last_draft.model_dump(mode="json"))
    write_json("response_llm.json", response.model_dump(mode="json"))


if __name__ == "__main__":
    main()
