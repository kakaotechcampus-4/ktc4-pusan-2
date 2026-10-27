"""실제 LLM API 로 예시 요청을 리뷰한다 (과금, 1회 호출, 명세 10절).

가짜 LLM 테스트(tests/unit/test_llm.py)는 LLM 결과를 붙이는 규칙 · 실패 처리를 본다.
이 테스트는 실제 LLM 출력이 스키마와 숫자 검사를 통과하는지 확인한다(숫자 검사 전 초안 기준).
기본 실행에서 빠진다 — `uv run python -m pytest -m live` 로 따로 돌린다.

캐시를 쓰지 않아 부를 때마다 과금된다. 호출 한 번 12초 제한에 걸리는지는 평가
(review_lab.evaluate)가 재므로, 여기서는 시간 제한을 넉넉히 둔다.
"""

import json

import pytest

from review import review_take
from review.core import check_numbers, message_numbers
from review_lab.evaluate import draft_sentences
from review_lab.llm import CachedLLM, load_settings, review_llm
from review_lab.paths import EXAMPLES_DIR

pytestmark = pytest.mark.live

#: 스키마 · 숫자 검사를 보려는 테스트라 시간 제한으로 떨어지지 않게 넉넉히
TIMEOUT_S = 60.0


@pytest.fixture(scope="module")
def reviewed(tmp_path_factory):
    settings = load_settings()
    llm = CachedLLM(
        review_llm(settings, timeout_s=TIMEOUT_S),
        settings.model,
        tmp_path_factory.mktemp("llm") / "cache.json",  # 빈 캐시 → 늘 실제로 부른다
    )
    request = json.loads((EXAMPLES_DIR / "request.json").read_text(encoding="utf-8"))
    return settings, llm, review_take(request, llm, model=settings.model)


def test_real_llm_output_passes_schema(reviewed):
    settings, llm, resp = reviewed
    assert resp.fallback_reason is None, f"LLM 호출 또는 출력 형식이 실패했다 ({llm.last_error})"
    assert resp.meta.model == settings.model
    assert llm.last_draft is not None


def test_real_llm_uses_only_candidate_ids(reviewed):
    _, llm, _ = reviewed
    payload = json.loads(llm.last_message)
    for key, items in (
        ("strength_candidates", llm.last_draft.strengths),
        ("improvement_candidates", llm.last_draft.improvements),
        ("next_mission_candidates", llm.last_draft.next_missions),
    ):
        known = {c["candidate_id"] for c in payload[key]}
        assert {i.candidate_id for i in items} <= known, key


def test_real_llm_sentences_pass_number_check(reviewed):
    _, llm, _ = reviewed
    allowed = message_numbers(llm.last_message)
    failed = [s for s in draft_sentences(llm.last_draft) if not check_numbers(s, allowed)]
    assert failed == [], "입력에 없는 숫자가 든 LLM 문장"
