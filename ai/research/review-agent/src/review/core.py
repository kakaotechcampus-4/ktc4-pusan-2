"""리뷰 진입점.

- apply_rules: 규칙 단계만 (LLM 없음, 문장 없음). 같은 입력이면 같은 결과다.
- check_numbers: 숫자 검사. LLM 문장 속 숫자가 모두 입력에 있는 숫자인지 본다.
- review_take: 최종 진입점 (POST /takes/review). 규칙 단계 결과에
  LLM 단계(문장 · 다음 미션 고르기, 1번)를 붙여 응답을 만든다.
  LLM 이 없거나 실패하면 기본 문장과 후보 상위 다음 미션을 내고 fallback_reason 을 단다.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .burden import Burden, compute_burden, recurring_dimensions
from .comparison import compare_previous
from .config import DEFAULT_CONFIG, ReviewConfig
from .dimensions import decide_dimension_states
from .evaluable import check_evaluable
from .llm_input import Candidates, build_candidates, review_message
from .missions import judge_missions, judge_user_criteria
from .next_missions import build_next_missions, merge_picked_missions, select_next_missions
from .prompts.review import REVIEW_SYSTEM_PROMPT
from .recurring import build_recurring_issues
from .schemas import ReviewDraft, ReviewRequest, ReviewResponse
from .score import compute_score
from .text import TextCandidate, default_text, text_candidates
from .version import FEATURE_VERSION, SCHEMA_VERSION
from .vocab import FallbackReason

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuleResult:
    """규칙 단계 결과 (문장 없음).

    review_take 가 이것으로 응답을 만들고, 테스트 · 연구 평가가 근거를 본다.
    """

    take_result: dict[str, Any]
    script_coverage: dict[str, Any] | None
    evaluable: dict[str, bool]
    mission_results: list[dict[str, Any]]
    criteria_results: list[dict[str, Any]]
    burden: Burden
    dimensions: dict[str, str]
    comparison: list[dict[str, Any]]
    next_mission_candidates: list[dict[str, Any]]
    recurring_issues: list[dict[str, Any]]
    score: int | None


def _validate(request: ReviewRequest | dict[str, Any]) -> ReviewRequest:
    """요청 형식이 틀리면 pydantic ValidationError (라우터가 422 로 바꾼다)"""
    return request if isinstance(request, ReviewRequest) else ReviewRequest.model_validate(request)


def criteria_versions(take_result: dict[str, Any], cfg: ReviewConfig) -> dict[str, str]:
    """meta.criteria_versions (명세 5-3).

    Take 결과 criteria 의 기능별 기준 버전 + 리뷰 자체 규칙 버전.

    키는 기준 버전의 기능 이름(``gaze-1.1+…`` → ``gaze``). 한 모듈의 기준이 여러 영역에 실려도
    (volume → VOLUME · PAUSE) 한 번만 싣는다. 영역 이름순으로 처음 본 버전을 쓴다.
    """
    versions: dict[str, str] = {}
    for area in sorted(take_result.get("criteria") or {}):
        version = (take_result["criteria"][area] or {}).get("criteria_version")
        if isinstance(version, str) and version:
            versions.setdefault(version.split("-", 1)[0], version)
    versions["review"] = cfg.criteria_version()
    return versions


def apply_rules(
    request: ReviewRequest | dict[str, Any], config: ReviewConfig | None = None
) -> RuleResult:
    """규칙 단계 (명세 6-1). LLM · 문장 없이 숫자 · 상태 · 점수 · 미션 판정 ·
    다음 미션 후보 · 반복 문제를 정한다.

    실행 순서: 판단 가능 → 미션 판정 → 부담 → 차원 상태 → 사용자 기준 → 이전 Take 비교
    → 다음 미션 후보 → 반복 문제 → 점수.
    부담 · 차원 상태가 미션 결과를 쓰므로 미션 판정을 먼저 한다.
    """
    cfg = config or DEFAULT_CONFIG
    data = _validate(request).model_dump()

    take_result = data["take_result"]
    script_coverage = data["script_coverage"]
    recurring = recurring_dimensions(data["previous_recurring_issues"])
    user_criteria = data["user_criteria"] or {}
    script_used = user_criteria.get("script_used")

    evaluable = check_evaluable(take_result, script_coverage, script_used, cfg)
    mission_results = judge_missions(data["missions"] or [], take_result, script_coverage, cfg)
    burden = compute_burden(
        take_result, script_coverage, evaluable, script_used, recurring, mission_results, cfg
    )
    states = decide_dimension_states(burden, evaluable, recurring, mission_results, cfg)
    criteria_results = judge_user_criteria(user_criteria, take_result, script_coverage, cfg)
    comparison = compare_previous(take_result, script_coverage, data["previous_takes"], cfg)
    candidates = build_next_missions(
        criteria_results, states, burden, take_result, script_coverage, cfg
    )

    return RuleResult(
        take_result=take_result,
        script_coverage=script_coverage,
        evaluable=evaluable,
        mission_results=mission_results,
        criteria_results=criteria_results,
        burden=burden,
        dimensions=states,
        comparison=comparison,
        next_mission_candidates=candidates,
        recurring_issues=build_recurring_issues(states, burden, cfg),
        score=compute_score(states, cfg),
    )


# ══════════════════════════════════════════════════════════════════════════
# 숫자 검사 (명세 6-2, architecture.md 0-2)
# ══════════════════════════════════════════════════════════════════════════

#: 문장 속 단위 → 입력 값을 그 단위로 바꾸는 배율. 시간 입력은 ms 다 (architecture.md 6-1).
#: 단위가 없거나 개수 · 점수 · 글자 · dB 는 입력 값 그대로
_UNIT_SCALE = {
    "%": 100,
    "퍼센트": 100,
    "ms": 1,
    "초": 1 / 1000,
    "분": 1 / 60000,
    "번": 1,
    "개": 1,
    "점": 1,
    "자": 1,
    "db": 1,
    "": 1,
}
#: 정수 · 소수. 1,240 처럼 세 자리마다 쉼표를 찍은 수도 한 값이다
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
#: 이어 쓴 "N분 M초" 는 한 값 (초로 센다)
_MIN_SEC = re.compile(rf"({_NUM})분\s?({_NUM})초(?!과)")
#: 숫자 바로 뒤에 붙은 단위. "5초과" · "분당" 의 초 · 분은 단위가 아니다
_NUMBER = re.compile(rf"({_NUM})(퍼센트|%|ms|dB|초(?!과)|분(?!당)|번|개|점|자)?", re.IGNORECASE)


def _value(text: str) -> tuple[float, int]:
    """(값, 보여 준 소수 자리)"""
    text = text.replace(",", "")
    return float(text), len(text.partition(".")[2])


def _numbers(sentence: str) -> list[tuple[str, float, int]]:
    """문장 속 숫자 (단위, 값, 소수 자리). 한글로 쓴 수("세 번")는 뽑지 않는다"""
    found: list[tuple[str, float, int]] = []

    def min_sec(m: re.Match[str]) -> str:
        minutes, _ = _value(m.group(1))
        seconds, places = _value(m.group(2))
        found.append(("초", minutes * 60 + seconds, places))
        return " "  # 뽑은 자리는 다시 뽑지 않는다

    rest = _MIN_SEC.sub(min_sec, sentence)
    for m in _NUMBER.finditer(rest):
        value, places = _value(m.group(1))
        found.append(((m.group(2) or "").lower(), value, places))
    return found


def _round_half_up(value: float, places: int) -> float:
    """보여 준 자리까지 반올림 (0.625 → 0.63). 부동소수 오차는 1e-9 로 덮는다"""
    scale = 10**places
    return math.floor(value * scale + 0.5 + 1e-9) / scale


def check_numbers(sentence: str, allowed_numbers: Iterable[float | int | None]) -> bool:
    """문장 속 숫자가 모두 입력에 있는 숫자면 True (명세 6-2 숫자 검사).

    allowed_numbers 는 입력 값 그대로 (비율 0–1 · 시간 ms · 개수 · dB · 장 번호 · Take 번호).
    문장 속 숫자 하나가 아래 중 하나로 입력 값과 같으면 통과다 (architecture.md 0-2).

    1. 비율과 퍼센트: 0.6248 ↔ 62% · 62퍼센트 · 0.62. 단위 없는 62 는 다르다
    2. 시간: 88000 ↔ 88초 · 1분 28초 · 88000ms
    3. 문장에 보인 소수 자리까지 반올림한 입력 값과 같다 (0.6248 ↔ 0.62)
    4. 개수 · 점수는 그대로 (5 ↔ 5번)
    5. 음수는 크기로 본다 (−1.5 ↔ 1.5dB). 방향 말("작게")은 보지 않는다

    숫자가 없는 문장은 True. None · bool 은 입력 값으로 보지 않는다.
    """
    allowed = [abs(float(a)) for a in allowed_numbers if a is not None and not isinstance(a, bool)]
    for unit, value, places in _numbers(sentence):
        scale = _UNIT_SCALE[unit]
        if not any(abs(_round_half_up(a * scale, places) - value) < 1e-9 for a in allowed):
            return False
    return True


def message_numbers(message: str) -> list[float]:
    """LLM 에 보낸 입력(review_message) 속 숫자 = 숫자 검사의 허용 숫자.

    LLM 이 볼 수 있던 숫자는 이것뿐이라 그 밖의 숫자는 지어낸 것이다.
    - JSON 의 숫자 값 (점수 · 근거 · 미션 목표 · 관측값 · 비교 값 · 장 번호 · 핵심 내용 개수 …)
    - 문자열 속 숫자 (기본 설명 "52% 이하" · 사용자 표현 "추임새 5번 이하" …)를 입력 단위로
      되돌린 값 (52% → 0.52, 1분 28초 → 88000)
    - candidate_id("s1")는 번호일 뿐이라 넣지 않는다
    """
    found: list[float] = []

    def walk(value: Any) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, int | float):
            found.append(value)
        elif isinstance(value, str):
            found.extend(v / _UNIT_SCALE[unit] for unit, v, _ in _numbers(value))
        elif isinstance(value, dict):
            for key, item in value.items():
                if key != "candidate_id":
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(json.loads(message))
    return found


#: 예외의 code 로 받아들이는 fallback_reason (서비스 common/llm.py 가 다는 계약)
_CODES = ("LLM_TIMEOUT", "LLM_ERROR", "LLM_INVALID_OUTPUT", "LLM_UNAVAILABLE")


def _fallback_reason(exc: BaseException) -> FallbackReason:
    """LLM 호출 · 초안 읽기가 실패한 원인 (coach-agent planner 와 같은 규칙).

    예외가 네 값 중 하나인 code 를 가지면 그것을 쓴다 — 코어는 LLM 라이브러리의 예외 종류를
    import 하지 않으므로, 서비스가 시간 초과 · 출력 검증 실패 · 설정 없음 예외에 code 를 달아
    던진다. 없으면 표준 시간 초과 → LLM_TIMEOUT, pydantic 검증 실패 → LLM_INVALID_OUTPUT,
    그 밖 → LLM_ERROR.
    """
    code = getattr(exc, "code", None)
    if code in _CODES:
        return code
    if isinstance(exc, TimeoutError):
        return "LLM_TIMEOUT"
    if isinstance(exc, ValidationError):
        return "LLM_INVALID_OUTPUT"
    return "LLM_ERROR"


def _written(items: list[Any], known: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """(후보 id → LLM 항목, 버린 항목 수). 후보에 없는 id · 같은 id 의 두 번째부터는 버린다"""
    picked: dict[str, Any] = {}
    for item in items:
        if item.candidate_id in known and item.candidate_id not in picked:
            picked[item.candidate_id] = item
    return picked, len(items) - len(picked)


def _texts(items: dict[str, TextCandidate], written: dict[str, str | None]) -> list[dict[str, Any]]:
    """후보를 모두 후보 순서로. LLM 이 쓴 문장만 바꾸고, 빠뜨렸거나 빈 문장은 기본 문장"""
    out = []
    for cid, c in items.items():
        text = (written.get(cid) or "").strip()
        out.append({**copy.deepcopy(c.item), "text": text or c.item["text"]})
    return out


def apply_draft(
    draft: ReviewDraft,
    candidates: Candidates,
    default_summary: str,
    allowed_numbers: list[float],
) -> tuple[dict[str, Any], list[dict[str, Any]], int, int]:
    """LLM 초안을 후보에 붙인다 → (문장, 다음 미션, 버린 항목 수, 숫자 검사로 바꾼 문장 수).

    - 숫자 검사: LLM 문장마다 check_numbers. 걸린 문장은 쓰지 않아 그 후보의 기본 문장이 된다
      (문장 단위로 바꾸므로 fallback_reason 은 달지 않는다, 명세 6-2)
    - 강점 · 개선점: 어느 차원을 말할지는 규칙 단계가 정했으므로 후보를 모두 낸다 (_texts)
    - 다음 미션: 사용자 기준 FAILED 를 맨 앞에, 이어서 LLM 이 고른 순서 (merge_picked_missions)
    - 요약이 비었거나 숫자 검사에 걸리면 기본 요약
    """
    replaced = 0

    def checked(sentence: str | None) -> str | None:
        nonlocal replaced
        if sentence and not check_numbers(sentence, allowed_numbers):
            replaced += 1
            return None
        return sentence

    strengths, dropped_s = _written(draft.strengths, candidates.strengths)
    improvements, dropped_i = _written(draft.improvements, candidates.improvements)
    missions, dropped_n = _written(draft.next_missions, candidates.next_missions)

    index = {cid: i for i, cid in enumerate(candidates.next_missions)}
    # 사용자 표현(display)이 있는 FAILED 미션은 LLM 설명을 쓰지 않으므로 검사 · 셈에서 뺀다
    keep = {
        i
        for i, m in enumerate(candidates.next_missions.values())
        if i < candidates.n_fixed and m["description"] is not None
    }
    picks = [
        (
            index[cid],
            None if index[cid] in keep else checked(m.description),
            checked(m.reason),
        )
        for cid, m in missions.items()
    ]
    next_missions = merge_picked_missions(
        list(candidates.next_missions.values()), candidates.n_fixed, picks
    )
    text = {
        "overall_summary": (checked(draft.overall_summary) or "").strip() or default_summary,
        "strengths": _texts(
            candidates.strengths, {cid: checked(m.text) for cid, m in strengths.items()}
        ),
        "improvements": _texts(
            candidates.improvements, {cid: checked(m.text) for cid, m in improvements.items()}
        ),
    }
    return text, next_missions, dropped_s + dropped_i + dropped_n, replaced


def review_take(
    request: ReviewRequest | dict[str, Any],
    llm: Any = None,
    *,
    model: str | None = None,
    config: ReviewConfig | None = None,
) -> ReviewResponse:
    """Take 하나를 리뷰한다 (명세 4-2). 응답을 dict 로: ``.model_dump(mode="json")``

    ``llm`` 은 LLM 출력 스키마(ReviewDraft)가 걸린 객체(``.invoke(messages)`` 만 씀), 없으면 None.
    llm 이 None 이면 부르지 않고 기본 결과 + LLM_UNAVAILABLE. 호출 · 초안 읽기가 실패하면
    기본 결과 + 원인(_fallback_reason). 숫자 · 상태 · 점수 · 미션 판정은 어느 쪽이든 같다.
    ``model`` 은 응답한 모델 이름이고 LLM 결과일 때만 meta.model 에 싣는다.
    요청 형식 오류는 그대로 올린다 (라우터가 422 로 바꾼다).
    """
    cfg = config or DEFAULT_CONFIG
    rules = apply_rules(_validate(request), cfg)
    texts = text_candidates(
        rules.dimensions, rules.burden, rules.take_result, rules.script_coverage, cfg
    )
    text = default_text(texts)
    next_missions = select_next_missions(rules.next_mission_candidates, cfg)
    reason: FallbackReason | None = "LLM_UNAVAILABLE"
    dropped = replaced = 0

    if llm is not None:
        candidates = build_candidates(rules, texts)
        message = review_message(rules, candidates)
        try:
            out = llm.invoke([("system", REVIEW_SYSTEM_PROMPT), ("user", message)])
            draft = out if isinstance(out, ReviewDraft) else ReviewDraft.model_validate(out)
            text, next_missions, dropped, replaced = apply_draft(
                draft, candidates, text["overall_summary"], message_numbers(message)
            )
            reason = None
        except Exception as exc:  # noqa: BLE001 — 규칙 단계 결과가 본체라 LLM 장애로 끊지 않는다
            reason = _fallback_reason(exc)
            # 예외 메시지에 LLM 문장이 들어 있을 수 있어 종류만 남긴다 (명세 11절 로그)
            log.warning(
                "review llm failed take_id=%s reason=%s error=%s",
                rules.take_result["take_id"],
                reason,
                type(exc).__name__,
            )

    log.info(
        "review take_id=%s score=%s dimensions=%s fallback_reason=%s replaced=%d dropped=%d",
        rules.take_result["take_id"],
        rules.score,
        rules.dimensions,
        reason,
        replaced,
        dropped,
    )
    return ReviewResponse.model_validate(
        {
            "take_id": rules.take_result["take_id"],
            "overall_summary": text["overall_summary"],
            "score": rules.score,
            "dimensions": rules.dimensions,
            "strengths": text["strengths"],
            "improvements": text["improvements"],
            "comparison": rules.comparison,
            "mission_results": rules.mission_results,
            "criteria_results": rules.criteria_results,
            "next_missions": next_missions,
            "recurring_issues": rules.recurring_issues,
            "fallback_reason": reason,
            "meta": {
                "schema_version": SCHEMA_VERSION,
                "feature_version": FEATURE_VERSION,
                "criteria_versions": criteria_versions(rules.take_result, cfg),
                "model": (model or None) if reason is None else None,
            },
        }
    )
