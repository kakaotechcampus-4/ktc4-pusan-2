"""평가 시나리오 모양 · 읽기 · 가상 기록 만들기 (기능 문서 10-5 · 13-2 · 13-4 · 13-5, 연구 전용).

세트: 평가셋 50(`eval`) · 안전 세트 20(`safety`) · 객관성 세트 14(`objectivity`) · 에이전트 서비스 세트 19(`agent_service`,
승인 · 문장 스트림 · 기억 · 일반 조언 · 범위 밖의 새 동작, 13-2). 세트마다 따로 분모로 보고한다(14-1).

- 누가 쓰나: 평가 실행기(evals.py) · 평가 시험(tests/lab/test_evals.py). `evals/scenarios/schema.json`은 여기 모델에서 만든다
  (`python -m chat_agent_lab.evals --write-schema`). 시험이 파일과 모델이 같은지 본다.
- 시나리오 파일: `evals/scenarios/<id>.json` 하나에 시나리오 하나. 필수 `schema_version` · `id` · `set` · `group` · `split` ·
  `act`(해야 할 때 `should` · 하지 말아야 할 때 `should_not`, 13-1) · `fixtures` · `turns` · `expected.checks[]`.
  검사 하나는 `{check_id, observe, expect, compare}`(10-5)에 고를 것(`path` · `grader` · `turn` · `metric` · `derive`)을 더한다.
- 가상 기록(`fixtures`)은 선언형이다: 피치는 `preset: "sample"`(fixtures.sample_pitch 와 같은 '졸업 발표' Take 3 · 4 · 5)에서
  `take_overrides`(Take 별로 깊게 덮어씀) · `add_takes` · `drop_takes`로 바꾸거나 처음부터 적는다. Take 하나는 코치 Take 결과
  (`result` → fixtures.take_result 인자) · 리뷰(`review` → fixtures.review 인자 + 문장 칸) · Take 행 칸(상태 · 버전 · 장 넘김)이다.
  그래서 기대 숫자는 이 값에서 바로 계산해 시나리오에 적는다(13-5 '사람 정답': 에이전트를 돌리지 않고 fixtures 만 보고 쓴다).
- 차례(`turns[]`)의 `approval`(기본 `APPROVE`): 승인 대기(`ASK_CONFIRM` 카드)에서 멈췄을 때 사용자의 결정. `REJECT` · `EXPIRE`(30분 지남) ·
  `NEW_MESSAGE`(대기를 그대로 두고 다음 차례의 새 메시지가 취소하게 함). 저장 · 다시 평가를 기대하는 기존 시나리오는 기본값 그대로다.
  선택 필드라 `SCHEMA_VERSION`은 올리지 않았다.
- 하는 일: `load_scenarios()`가 파일을 읽어 모양(pydantic, schema.json 과 같음)과 뜻(묶음 · 개수 · 보류 · 검사 · 템플릿 이름)을
  검사하고, 하나라도 틀리면 `ScenarioError`로 멈춘다(10-5 '스키마 검사에 실패하면 실행을 멈춘다'). `build_world()`가 가짜 BE 저장소
  (피치 · Take · 대화 목표 · 대화 기록 · 화면 · 자료 묶음)를 만든다.
- 고칠 때: 모양을 바꾸면 `SCHEMA_VERSION`과 schema.json 을 같이 바꾼다. 묶음 · 개수(13-2 · 13-4)는 `GROUPS`. 기대 결과는 사람이
  검수한다(13-5, 아직 팀원 검수 전).
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from chat_agent.config import DEFAULT_CONFIG
from chat_agent.corpus import Corpus, build_corpus, manifest_hash
from chat_agent.goals import build_goal_draft
from chat_agent.schemas import Screen, TakeSnapshot

from . import fixtures as fx
from .app import GUIDES

SCHEMA_VERSION = "1.0"
SCENARIO_DIR = Path(__file__).resolve().parents[2] / "evals" / "scenarios"
SCHEMA_FILE = SCENARIO_DIR / "schema.json"

#: 세트 → 묶음 → 개수(13-2 · 13-4). 개수가 다르면 전체를 읽을 때 멈춘다
GROUPS: dict[str, dict[str, int]] = {
    "eval": {
        "representative": 6,
        "global_scope": 4,
        "pitch_analysis": 6,
        "reassess": 5,
        "screen": 7,
        "autonomy": 4,
        "stale": 4,
        "not_comparable": 3,
        "guide": 3,
        "refusal": 2,
        "injection": 3,
        "failure": 3,
    },
    "safety": {
        "injection_abuse": 6,
        "permission": 4,
        "tampering": 4,
        "multi_turn": 3,
        "cost": 3,
    },
    "objectivity": {
        "wrong_claim": 3,
        "right_claim": 3,
        "unverifiable": 2,
        "pressure": 2,
        "goal_conflict": 1,
        "myth": 1,
        "mixed": 1,
        "other_speaker": 1,
    },
    "agent_service": {
        "approval": 4,
        "stream": 3,
        "memory": 5,
        "general": 4,
        "out_of_scope": 3,
    },
}
#: 묶음 이름(결과 표)
GROUP_LABELS = {
    "representative": "대표 시나리오 경로",
    "global_scope": "전체 범위 질문",
    "pitch_analysis": "피치 분석 질문",
    "reassess": "다시 평가",
    "screen": "피치 만들기 · 설정 중",
    "autonomy": "자율 등급",
    "stale": "최신 아님",
    "not_comparable": "비교 불가",
    "guide": "발표법 · 사용법",
    "refusal": "근거 없음 · 범위 밖",
    "injection": "주입(기본)",
    "failure": "장애 · 한도 · 스스로 고치기",
    "injection_abuse": "주입으로 허용 행동 악용 · 애매한 요청",
    "permission": "권한",
    "tampering": "점수 · 미션 조작",
    "multi_turn": "여러 턴에 걸친 유도",
    "cost": "비용 소모",
    "wrong_claim": "틀린 측정 주장",
    "right_claim": "맞는 측정 주장",
    "unverifiable": "확인 불가",
    "pressure": "압력",
    "goal_conflict": "목표 충돌",
    "myth": "근거 없는 통념",
    "mixed": "여러 주장이 섞인 말",
    "other_speaker": "다른 사람 평가 인용",
    "approval": "승인",
    "stream": "문장 스트림",
    "memory": "기억",
    "general": "일반 조언",
    "out_of_scope": "범위 밖",
}
#: 평가셋 50 의 보류 개수(13-5: 묶음마다 약 30%, 합계 15)
HOLDOUT_COUNTS = {
    "representative": 2,
    "global_scope": 1,
    "pitch_analysis": 2,
    "reassess": 1,
    "screen": 2,
    "autonomy": 1,
    "stale": 1,
    "not_comparable": 1,
    "guide": 1,
    "refusal": 1,
    "injection": 1,
    "failure": 1,
}
#: 고정 순서 기준선의 입력 유형(13-3). 유형마다 도구 순서는 evals.BASELINE_SEQUENCES
BaselineType = Literal["record", "compare", "goal", "reassess", "scope", "screen", "guide", "none"]

Observe = Literal["answer", "actions_proposed", "actions_executed", "db_diff", "trace"]
Compare = Literal["equals", "contains", "set_equals", "absent", "one_of"]
Grader = Literal["value", "number", "target", "regex", "template", "quote"]
#: 검사가 들어가는 지표(14-1). 그 지표의 대상 실행 = 이 표시가 있는 검사를 가진 시나리오의 실행.
#: advice: 조언 질문의 GUIDE 비율(답을 뒷받침하는 자료 id 를 정해 둔 조언 질문) · memory: 기억 누설(기억을 넣거나 바꿔도
#: 판단 · 판정 · 근거 · 행동이 기억 없을 때의 기대와 같은가 — 기대값은 기억 없이 fixtures 만 보고 쓴다)
MetricTag = Literal["autonomy", "screen", "objectivity", "advice", "memory"]
Approval = Literal["APPROVE", "REJECT", "EXPIRE", "NEW_MESSAGE"]


class ScenarioError(ValueError):
    """시나리오 파일 검사 실패. 실행을 멈춘다(10-5)."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── 가상 기록 ─────────────────────────────────────────────────────────────


class ResultSpec(_M):
    """코치 Take 결과(fixtures.take_result 인자). 리스트는 장마다, 수 하나면 모든 장에 같은 값."""

    slides: int = Field(default=4, ge=1)
    slide_ms: int = Field(default=60_000, ge=1)
    audience: float | list[float] = 0.6
    script_ratio: float | list[float] = 0.2
    cpm: float | list[float] = 310.0
    filler_per_min: float = 1.2
    filler_count: int = 5
    voice_diff_db: float = -0.5
    long_silence_count: int = 0
    #: [문제 이름, 장 번호 또는 null, reliable]
    issues: list[tuple[str, int | None, bool]] = Field(default_factory=list)
    #: {영역: 사유} — 그 영역은 측정 안 됨(값 null)
    unmeasured: dict[str, str] = Field(default_factory=dict)
    #: 없으면 Take 행의 script_mode
    script_mode: str | None = None
    plan: dict[str, int] | None = None
    criteria_versions: dict[str, str] = Field(default_factory=dict)


class ReviewSpec(_M):
    """공식 리뷰(fixtures.review 인자). 차원을 주지 않으면 모두 STABLE(내용 전달은 NOT_EVALUABLE)."""

    dimensions: dict[str, str] = Field(default_factory=dict)
    score: int | None = 72
    fallback: bool = False
    #: 리뷰 문장 칸(주입 경로 시험용). 주면 그대로 덮어쓴다
    overall_summary: str | None = None
    strengths: list[dict[str, Any]] | None = None
    improvements: list[dict[str, Any]] | None = None
    mission_results: list[dict[str, Any]] | None = None


class TakeSpec(_M):
    take_id: str | None = None
    take_number: int = Field(ge=0)
    status: str = "COMPLETED"
    mode: str = "COACHING"
    script_mode: str = "OFF"
    end_reason: str | None = None
    page_count: int | None = 4
    visited_slides: list[int] = Field(default_factory=lambda: [1, 2, 3, 4])
    script_version_id: str = "sv-1"
    presentation_version_id: str = "pv-1"
    standard_version: int | None = 1
    completed_at: str | None = "2026-11-01T10:00:00+09:00"
    started_at: str = "2026-11-01T09:50:00+09:00"
    result: ResultSpec | None = Field(default_factory=ResultSpec)
    review: ReviewSpec | None = Field(default_factory=ReviewSpec)
    script_coverage: dict[str, Any] | None = None
    revisions: dict[str, int | None] | None = None


class PitchSpec(_M):
    """피치 하나. `preset`이 있으면 그 피치에서 시작해 나머지 칸으로 덮어쓴다."""

    preset: Literal["sample"] | None = None
    pitch_id: str | None = None
    user_id: str | None = None
    title: str | None = None
    time_limit_sec: int | None = None
    lower_deviation_sec: int | None = None
    upper_deviation_sec: int | None = None
    script_version_id: str | None = None
    script_version_label: str | None = None
    presentation_version_id: str | None = None
    page_count: int | None = None
    standard_version: int | None = None
    criteria: dict[str, Any] | None = None
    missions: list[dict[str, Any]] | None = None
    last_practiced_at: str | None = None
    takes: list[TakeSpec] | None = None
    #: preset 의 Take 를 깊게 덮어쓴다: {take_id: TakeSpec 의 일부}
    take_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    add_takes: list[TakeSpec] = Field(default_factory=list)
    drop_takes: list[str] = Field(default_factory=list)


class GoalSpec(_M):
    """대화에 이미 저장된 대화 목표. 기준값 · 판 · 범위는 기준 Take 기록으로 코드가 만든다(6-7)."""

    metric: str
    slide_number: int | None = None
    base_take_id: str
    version: int = Field(default=1, ge=1)
    status: Literal["OPEN", "ACHIEVED", "NOT_ACHIEVED", "BASELINE_MISMATCH"] = "OPEN"
    #: 목표 저장 때 그 피치의 가장 큰 Take 번호(이보다 큰 번호가 '저장 뒤 시작한 Take')
    after_take: int = Field(ge=0)
    created_at: str = "2026-11-03T11:00:00+09:00"


class HistorySpec(_M):
    """대화에 이미 있는 메시지(옛 대화). `source_ids`의 원천이 지워졌으면 BE가 가린다(12-2)."""

    role: Literal["user", "assistant", "system"]
    text: str = Field(min_length=1, max_length=4000)
    source_ids: list[str] = Field(default_factory=list)
    basis: dict[str, Any] | None = None


class SummaryItemSpec(_M):
    """대화에 이미 있는 대화 요약 항목(5-2 `SummaryItem`, 6-14)."""

    kind: Literal["TOPIC", "INTENT", "DECISION", "OPEN"]
    text: str = Field(min_length=1, max_length=120)


class ConversationSpec(_M):
    scope_pitch_id: str | None = "p-1"
    goal: GoalSpec | None = None
    history: list[HistorySpec] = Field(default_factory=list)
    summary: list[SummaryItemSpec] | None = Field(default=None, max_length=12)
    #: '기억 없이' 대화(6-14): 코칭 프로필을 주지 않고 저장 후보도 저장하지 않는다
    no_memory: bool = False


class ProfileItemSpec(_M):
    """이미 저장된 코칭 프로필 항목(5-3 `memory.profile`, 6-14). key 마다 하나."""

    key: Literal["TONE", "EXPLAIN_STYLE", "FOCUS_AREA", "CONTEXT"]
    value: str = Field(min_length=1, max_length=200)


class MemorySpec(_M):
    """사용자의 기억 설정 · 코칭 프로필(6-14). 기본은 켜짐 · 빈 프로필. 값은 BE 가 저장하는 그대로 넣는다(검사 없이 —
    오염된 프로필이 들어온 경우를 시험하려고)."""

    enabled: bool = True
    profile: list[ProfileItemSpec] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def _one_per_key(self) -> MemorySpec:
        keys = [p.key for p in self.profile]
        if len(keys) != len(set(keys)):
            raise ValueError("코칭 프로필은 key 마다 하나")
        return self


class CorpusSpec(_M):
    """자료 묶음 바꾸기(주입 경로 시험용). 판은 `corpus_version`, 해시는 다시 계산한다."""

    corpus_version: str = "guides-1-eval"
    extra_cards: list[dict[str, Any]] = Field(default_factory=list)


class Fixtures(_M):
    user_id: str = "u-3"
    pitches: list[PitchSpec] = Field(default_factory=lambda: [PitchSpec(preset="sample")])
    conversation: ConversationSpec = Field(default_factory=ConversationSpec)
    #: 화면 상태(12-4 Screen 모양). 차례(`turns[].screen`)에서 이름으로 고른다
    screens: dict[str, dict[str, Any]] = Field(default_factory=dict)
    corpus: CorpusSpec | None = None
    #: stored: 가상 리뷰 그대로 · rule: 공식 리뷰를 실제 리뷰 규칙 결과로 바꿔 시작(다시 평가 비교용)
    official_reviews: Literal["stored", "rule"] = "stored"
    #: 기억 설정 · 코칭 프로필(6-14)
    memory: MemorySpec = Field(default_factory=MemorySpec)


# ── 차례 · 장애 주입 ─────────────────────────────────────────────────────


class Fault(_M):
    """차례 하나에 넣는 장애(10-5 '장애 주입').

    - model_delay: `after_call`번째 모델 호출이 끝난 뒤 시계를 `seconds`만큼 흘린다(모델이 오래 걸림 · 한도 끝)
    - model_error: `purpose`의 `nth`번째 호출을 보내지 않고 시간 초과로 끝낸다(`seconds`만큼 시계를 흘림)
    - corrupt_answer: 첫 답 쓰기 응답의 문장 속 `from_text`를 `to_text`로 바꾼다(답 검사가 숫자를 잡아 고치게 하는지)
    - delete_before_action: BE가 `action` 행동을 실행하기 직전에 `target` Take 를 지운다(행동 실패)
    """

    kind: Literal[
        "model_delay", "model_error", "corrupt_answer", "delete_before_action", "stream_cut"
    ]
    after_call: int | None = Field(default=None, ge=1)
    #: stream_cut: 이 차례의 첫 AI 호출에서 문장 줄을 이만큼 받은 뒤 끝 줄 없이 연결이 끊긴다(L2-45)
    after_claims: int | None = Field(default=None, ge=1)
    seconds: float = Field(default=0.0, ge=0)
    purpose: Literal["RESEARCH", "ANSWER", "FIX"] | None = None
    nth: int = Field(default=1, ge=1)
    from_text: str | None = None
    to_text: str | None = None
    action: str | None = None
    target: str | None = None

    @model_validator(mode="after")
    def _args(self) -> Fault:
        need = {
            "model_delay": self.after_call is not None and self.seconds > 0,
            "model_error": self.purpose is not None,
            "corrupt_answer": bool(self.from_text) and self.to_text is not None,
            "delete_before_action": bool(self.action) and bool(self.target),
            "stream_cut": self.after_claims is not None,
        }[self.kind]
        if not need:
            raise ValueError(f"{self.kind} 에 필요한 칸이 없다")
        return self


class Turn(_M):
    """사용자 말 하나(BE 메시지 → 실행 끝까지) 또는 자동 목표 확인 한 주기."""

    message: str | None = Field(default=None, min_length=1, max_length=2000)
    page_take_id: str | None = None
    #: fixtures.screens 의 이름
    screen: str | None = None
    choice: dict[str, Any] | None = None
    auto_goal_check: bool = False
    #: 승인 대기(`ASK_CONFIRM` 카드)에서 멈췄을 때 사용자의 결정(12-3). 기본 `APPROVE`(저장 · 다시 평가를 기대하는 시나리오).
    #: `REJECT`(거절) · `EXPIRE`(30분 지남) · `NEW_MESSAGE`(대기를 그대로 두고 다음 차례의 새 메시지가 취소하게 함)
    approval: Approval = "APPROVE"
    faults: list[Fault] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_kind(self) -> Turn:
        if (self.message is None) == (not self.auto_goal_check):
            raise ValueError("차례는 message 또는 auto_goal_check 중 정확히 하나")
        return self


class Followup(_M):
    """앞 차례가 실패했을 때만 차례로 주는 후속 사용자 말(13-3, 시나리오마다 최대 2개)."""

    message: str = Field(min_length=1, max_length=2000)
    page_take_id: str | None = None


class Check(_M):
    """기대 결과 하나(10-5). 필수 검사가 하나라도 실패하거나 관찰값이 없으면 그 실행은 실패다.

    - observe: 무엇을 보나. answer(최종 답) · actions_proposed(AI가 낸 행동 · 카드) · actions_executed(BE가 실제로 실행한 행동) ·
      db_diff(실행 전후 저장소) · trace(도구 · 진단 기록). 관찰값 모양은 grading.py 머리 설명
    - path: 관찰값 안의 위치(예 `after.conversation.goal.metric` · `assessments[]`). 없으면 grader 기본값
    - grader: value(값 그대로) · number(숫자 표기 규칙) · target(Take · 장 · 지표 대상) · regex · template(templates.py 문구) ·
      quote(자료 인용이 원문에 그대로 있는가)
    - compare: equals · contains · set_equals · absent(10-5) + one_of(허용 집합, 13-5 '맞는 답이 여럿이면')
    - turn: 몇 번째 차례의 관찰값인가(기본 마지막, `all`이면 모든 차례를 합침)
    - metric: 이 검사가 들어가는 지표(자율 · 화면 이해도 · 객관성)
    - derive: 기대값을 계산한 가상 기록 위치(예 `t-5:GAZE.audience_ratio@3`). 시험이 기대값과 맞는지 다시 계산한다
    """

    check_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    observe: Observe
    expect: Any
    compare: Compare
    path: str | None = None
    grader: Grader = "value"
    turn: int | Literal["all"] = -1
    metric: MetricTag | None = None
    derive: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _grader_compare(self) -> Check:
        allowed = {
            "value": {"equals", "contains", "set_equals", "absent", "one_of"},
            "number": {"contains", "absent"},
            "target": {"contains", "absent"},
            "regex": {"contains", "absent"},
            "template": {"contains", "absent"},
            "quote": {"equals"},
        }[self.grader]
        if self.compare not in allowed:
            raise ValueError(f"{self.grader} 채점기는 {sorted(allowed)} 만 쓴다")
        if self.grader != "value" and self.observe != "answer":
            raise ValueError(f"{self.grader} 채점기는 answer 에만 쓴다")
        return self


class Expected(_M):
    checks: Annotated[list[Check], Field(min_length=1)]


class Scenario(_M):
    schema_version: Literal["1.0"]
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    set: Literal["eval", "safety", "objectivity", "agent_service"]
    group: str
    split: Literal["dev", "holdout"]
    #: 해야 할 때(should: 기록 읽기 · 다시 평가 · 저장 · 이동을 묻지 않고 함) · 하지 말아야 할 때(should_not, 13-1)
    act: Literal["should", "should_not"]
    title: str = Field(min_length=1)
    #: 같은 원본에서 바꾼 시나리오끼리 같은 값(보류 · 개발이 같은 쪽이어야 한다, 13-5)
    source: str | None = None
    #: 거절 정확도(14-1) 대상이면 거절해야 하는가. null 이면 대상이 아니다
    expect_refusal: bool | None = None
    baseline_type: BaselineType = "record"
    fixtures: Fixtures = Field(default_factory=Fixtures)
    turns: Annotated[list[Turn], Field(min_length=1)]
    followups: Annotated[list[Followup], Field(max_length=2)] = Field(default_factory=list)
    expected: Expected
    notes: str | None = None


# ── 프리셋 ────────────────────────────────────────────────────────────────


def _sample_take(n: int, aud3: float, day: int) -> dict[str, Any]:
    low = aud3 < 0.4
    return {
        "take_id": f"t-{n}",
        "take_number": n,
        "completed_at": f"2026-11-0{day}T10:00:00+09:00",
        "result": {
            "audience": [0.62, 0.58, aud3, 0.6],
            "script_ratio": [0.15, 0.2, 0.55, 0.18],
            "issues": [["GAZE_ON_SCRIPT", 3, True]] if low else [],
        },
        "review": {
            "dimensions": {
                "audience_gaze": "PRIORITY" if low else "STABLE",
                "script_independence": "PRIORITY" if low else "STABLE",
                "fluency": "STRENGTH",
            },
            "score": 68 if low else 76,
        },
    }


def sample_preset(user_id: str) -> dict[str, Any]:
    """fixtures.sample_pitch() 와 같은 '졸업 발표'(시험이 같은지 본다): Take 3 · 4 · 5 의 3장 청중 시선 45% · 31% · 28%."""
    return {
        "pitch_id": "p-1",
        "user_id": user_id,
        "title": "졸업 발표",
        "criteria": {
            "standard_version": 1,
            "display_criteria": ["청중을 보며 말하기"],
            "targets": [],
            "script_used": None,
        },
        "missions": [
            {
                "mission_id": "m-7",
                "area": "GAZE",
                "slide_number": 3,
                "target": {"metric": "audience_ratio", "operator": "GTE", "value": 0.4},
                "status": "ACTIVE",
            }
        ],
        "takes": [_sample_take(3, 0.45, 1), _sample_take(4, 0.31, 2), _sample_take(5, 0.28, 3)],
    }


def _deep_merge(base: Any, over: Any) -> Any:
    """dict 는 키마다 깊게 합치고, 그 밖(리스트 포함)은 덮어쓴다."""
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = _deep_merge(base.get(k), v) if k in base else copy.deepcopy(v)
        return out
    return copy.deepcopy(over)


def resolve_pitch(spec: PitchSpec, user_id: str) -> PitchSpec:
    """preset 을 펼쳐 덮어쓰기까지 적용한 피치(preset 없음)."""
    if spec.preset is None:
        if spec.pitch_id is None or spec.title is None:
            raise ValueError("preset 이 없는 피치는 pitch_id · title 이 필수다")
        return spec
    base = sample_preset(spec.user_id or user_id)
    # 시나리오에 적은 칸만 덮어쓴다(명시한 null 도 덮어쓴다: 예 criteria: null)
    own = spec.model_dump(
        include=spec.model_fields_set - {"preset", "take_overrides", "add_takes", "drop_takes"}
    )
    merged = _deep_merge(base, own)
    takes = []
    for t in merged.get("takes", []):
        if t["take_id"] in spec.drop_takes:
            continue
        over = spec.take_overrides.get(t["take_id"])
        takes.append(_deep_merge(t, over) if over else t)
    unknown = set(spec.take_overrides) - {t["take_id"] for t in merged.get("takes", [])}
    if unknown:
        raise ValueError(f"take_overrides 의 Take 가 preset 에 없다: {sorted(unknown)}")
    takes += [t.model_dump(exclude_none=False) for t in spec.add_takes]
    merged["takes"] = takes
    return PitchSpec.model_validate(merged)


# ── 저장소 만들기 ─────────────────────────────────────────────────────────


def build_take(t: TakeSpec, pitch_id: str) -> fx.TakeRecord:
    tid = t.take_id or (
        f"t-{t.take_number}" if pitch_id == "p-1" else f"{pitch_id}-t{t.take_number}"
    )
    result = None
    if t.result is not None:
        r = t.result
        result = fx.take_result(
            take_id=tid,
            slides=r.slides,
            slide_ms=r.slide_ms,
            audience=r.audience,
            script_ratio=r.script_ratio,
            cpm=r.cpm,
            filler_per_min=r.filler_per_min,
            filler_count=r.filler_count,
            voice_diff_db=r.voice_diff_db,
            long_silence_count=r.long_silence_count,
            issues=[tuple(i) for i in r.issues],  # type: ignore[misc]
            unmeasured=dict(r.unmeasured) or None,
            script_mode=r.script_mode or t.script_mode,
            plan=r.plan,
            criteria_versions=dict(r.criteria_versions) or None,
        )
    review = None
    if t.review is not None:
        rv = t.review
        review = fx.review(
            take_id=tid, dimensions=dict(rv.dimensions), score=rv.score, fallback=rv.fallback
        )
        for k in ("overall_summary", "strengths", "improvements", "mission_results"):
            v = getattr(rv, k)
            if v is not None:
                review[k] = copy.deepcopy(v)
    rec = fx.TakeRecord(
        take_id=tid,
        take_number=t.take_number,
        status=t.status,
        mode=t.mode,
        script_mode=t.script_mode,
        end_reason=t.end_reason,
        page_count=t.page_count,
        visited_slides=list(t.visited_slides),
        script_version_id=t.script_version_id,
        presentation_version_id=t.presentation_version_id,
        standard_version=t.standard_version,
        completed_at=t.completed_at,
        started_at=t.started_at,
        take_result=result,
        script_coverage=copy.deepcopy(t.script_coverage),
        review=review,
    )
    if t.revisions is not None:
        rec.revisions = {**rec.revisions, **t.revisions}
    if t.script_coverage is not None and rec.revisions.get("SCRIPT_COVERAGE") is None:
        rec.revisions["SCRIPT_COVERAGE"] = 1
    return rec


def build_pitch(spec: PitchSpec, user_id: str) -> fx.PitchRecord:
    s = resolve_pitch(spec, user_id)
    assert s.pitch_id is not None and s.title is not None
    kw: dict[str, Any] = {
        k: getattr(s, k)
        for k in (
            "time_limit_sec",
            "lower_deviation_sec",
            "upper_deviation_sec",
            "script_version_id",
            "script_version_label",
            "presentation_version_id",
            "page_count",
            "standard_version",
            "last_practiced_at",
        )
        if getattr(s, k) is not None
    }
    p = fx.PitchRecord(
        pitch_id=s.pitch_id,
        user_id=s.user_id or user_id,
        title=s.title,
        criteria=copy.deepcopy(s.criteria),
        missions=copy.deepcopy(s.missions or []),
        **kw,
    )
    p.takes = [build_take(t, s.pitch_id) for t in s.takes or []]
    return p


def build_corpus_for(spec: CorpusSpec | None) -> Corpus:
    """기본 자료 묶음, 또는 카드를 더한 시험용 묶음(판 · 해시를 새로 만든다)."""
    manifest = json.loads((GUIDES / "manifest.json").read_text(encoding="utf-8"))
    files = {
        name: json.loads((GUIDES / name).read_text(encoding="utf-8"))
        for name in manifest.get("files", [])
    }
    if spec is not None and spec.extra_cards:
        first = manifest["files"][0]
        files[first] = [*files[first], *copy.deepcopy(spec.extra_cards)]
        raw = [c for name in manifest["files"] for c in files[name]]
        manifest = {
            **manifest,
            "corpus_version": spec.corpus_version,
            "sha256": manifest_hash(raw, manifest.get("synonyms") or None),
        }
    return build_corpus(manifest, files, cfg=DEFAULT_CONFIG, allow_unreviewed=True)


def corpus_texts(spec: CorpusSpec | None) -> dict[str, str]:
    """채점기용 자료 원문(source_id → 본문). 코어 검색 코드를 거치지 않고 파일에서 바로 읽는다."""
    manifest = json.loads((GUIDES / "manifest.json").read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for name in manifest.get("files", []):
        for c in json.loads((GUIDES / name).read_text(encoding="utf-8")):
            out[c["source_id"]] = c["text"]
    for c in spec.extra_cards if spec is not None else []:
        out[c["source_id"]] = c["text"]
    return out


_SCREEN = TypeAdapter(Screen)


@dataclass
class World:
    """시나리오 하나의 초기 상태: 피치 목록 · 대화 준비값 · 화면 · 자료 묶음 · 기억."""

    pitches: list[fx.PitchRecord]
    user_id: str
    conversation: ConversationSpec
    screens: dict[str, dict[str, Any]]
    corpus: Corpus
    corpus_texts: dict[str, str]
    official_reviews: str
    goal: dict[str, Any] | None = None
    memory: MemorySpec = field(default_factory=MemorySpec)
    extra: dict[str, Any] = field(default_factory=dict)


def build_goal(spec: GoalSpec, pitches: list[fx.PitchRecord]) -> dict[str, Any]:
    """저장된 대화 목표(Goal)를 기준 Take 기록으로 만든다(코드 `build_goal_draft`, 모델 값 없음)."""
    for p in pitches:
        t = p.take(spec.base_take_id)
        if t is None:
            continue
        snap = TakeSnapshot.model_validate(fx.take_snapshot(t))
        draft = build_goal_draft(
            snap, spec.metric, spec.slide_number, pitch_id=p.pitch_id, cfg=DEFAULT_CONFIG
        )
        if isinstance(draft, str):
            raise ValueError(f"목표를 만들 수 없다: {draft}")
        return {
            **draft.model_dump(mode="json"),
            "version": spec.version,
            "status": spec.status,
            "created_at": spec.created_at,
        }
    raise ValueError(f"목표 기준 Take 가 없다: {spec.base_take_id}")


def build_world(sc: Scenario) -> World:
    fxs = sc.fixtures
    pitches = [build_pitch(p, fxs.user_id) for p in fxs.pitches]
    for raw in fxs.screens.values():
        _SCREEN.validate_python(raw)  # 12-4 화면 상태 모양
    goal = build_goal(fxs.conversation.goal, pitches) if fxs.conversation.goal else None
    return World(
        pitches=pitches,
        user_id=fxs.user_id,
        conversation=fxs.conversation,
        screens=copy.deepcopy(fxs.screens),
        corpus=build_corpus_for(fxs.corpus),
        corpus_texts=corpus_texts(fxs.corpus),
        official_reviews=fxs.official_reviews,
        goal=goal,
        memory=fxs.memory,
    )


def derive_value(world: World, spec: str) -> float:
    """검사의 `derive`(기대값을 계산한 가상 기록 위치)를 읽는다. 시험이 적어 둔 기대값과 맞는지 다시 계산할 때 쓴다.

    형식: `<take_id>:<영역>.<지표>`(Take 값) · `<take_id>:<영역>.<지표>@<장>`(장 값) · `<take_id>:review.score`.
    예: `t-5:GAZE.audience_ratio@3` → 0.28
    """
    tid, _, rest = spec.partition(":")
    take = next((t for p in world.pitches for t in p.takes if t.take_id == tid), None)
    if take is None:
        raise ValueError(f"derive 의 Take 가 없다: {spec}")
    if rest == "review.score":
        v = (take.review or {}).get("score")
    else:
        where, _, slide = rest.partition("@")
        area, _, metric = where.partition(".")
        a = ((take.take_result or {}).get("areas") or {}).get(area) or {}
        if slide:
            row = next((s for s in a.get("slides") or [] if s["slide_number"] == int(slide)), {})
            v = (row.get("metrics") or {}).get(metric)
        else:
            v = (a.get("take") or {}).get(metric)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"derive 위치에 숫자가 없다: {spec}")
    return float(v)


# ── 읽기 · 검사 ───────────────────────────────────────────────────────────


def schema_json() -> dict[str, Any]:
    s = Scenario.model_json_schema()
    s["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    s["$id"] = "chat-agent/evals/scenario.schema.json"
    s["title"] = f"chat_agent 평가 시나리오 {SCHEMA_VERSION}"
    return s


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _template_keys(expect: Any) -> Any:
    """기대값 안의 `키@`(고정 문구가 있는가) 값들. 이름이 틀리면 실행 전에 멈추도록 검사에서 쓴다."""
    if isinstance(expect, dict):
        for k, v in expect.items():
            if isinstance(k, str) and k.endswith("@"):
                yield v
            else:
                yield from _template_keys(v)
    elif isinstance(expect, list):
        for v in expect:
            yield from _template_keys(v)


def _semantic(sc: Scenario, path: Path) -> list[str]:
    """모양 밖의 뜻 검사: 이름 · 묶음 · 차례 번호 · 화면 이름 · 템플릿 이름 · 가상 기록."""
    from .grading import TemplateMissing, resolve_template  # 순환 import 를 피한다

    out: list[str] = []
    where = path.name
    if path.stem != sc.id:
        out.append(f"{where}: 파일 이름과 id 가 다르다({sc.id})")
    if sc.group not in GROUPS[sc.set]:
        out.append(f"{where}: {sc.set} 세트에 없는 묶음 {sc.group}")
    if sc.set != "eval" and sc.split != "dev":
        out.append(f"{where}: 보류(holdout)는 평가셋 50 에만 둔다")
    ids = [c.check_id for c in sc.expected.checks]
    if len(ids) != len(set(ids)):
        out.append(f"{where}: check_id 가 겹친다")
    n = len(sc.turns)
    for c in sc.expected.checks:
        if isinstance(c.turn, int) and not -n <= c.turn < n:
            out.append(f"{where}: {c.check_id} 의 turn {c.turn} 이 차례 수({n}) 밖이다")
        specs = [c.expect] if c.grader == "template" else list(_template_keys(c.expect))
        for spec in specs:
            try:
                resolve_template(spec)
            except TemplateMissing as e:
                out.append(f"{where}: {c.check_id} 템플릿 {e}")
    for i, t in enumerate(sc.turns):
        if t.screen is not None and t.screen not in sc.fixtures.screens:
            out.append(f"{where}: turns[{i}].screen {t.screen} 이 fixtures.screens 에 없다")
    try:
        w = build_world(sc)
        ids_p = [p.pitch_id for p in w.pitches]
        ids_t = [t.take_id for p in w.pitches for t in p.takes]
        if len(ids_p) != len(set(ids_p)) or len(ids_t) != len(set(ids_t)):
            out.append(f"{where}: 피치 · Take id 가 겹친다")
        scope = sc.fixtures.conversation.scope_pitch_id
        if scope is not None and not any(
            p.pitch_id == scope and p.user_id == w.user_id for p in w.pitches
        ):
            out.append(f"{where}: 대화 범위 피치 {scope} 가 사용자의 피치가 아니다")
    except (ValueError, ValidationError) as e:
        out.append(f"{where}: 가상 기록을 만들 수 없다: {str(e)[:300]}")
    return out


def load_scenario(path: Path) -> Scenario:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Scenario.model_validate(data)


def load_scenarios(
    directory: Path = SCENARIO_DIR,
    *,
    sets: tuple[str, ...] = ("eval", "safety", "objectivity", "agent_service"),
    splits: tuple[str, ...] = ("dev", "holdout"),
    ids: list[str] | None = None,
    check_counts: bool = True,
) -> list[Scenario]:
    """시나리오 파일을 모두 읽어 검사한 뒤 고른 것만 돌려준다. 하나라도 틀리면 `ScenarioError`(실행을 멈춘다).

    검사는 고르기 전에 **모든** 파일에 한다(묶음 개수 · 보류 개수 · 같은 원본은 같은 쪽).
    """
    problems: list[str] = []
    found: list[tuple[Scenario, Path]] = []
    for path in sorted(directory.glob("*.json")):
        if path.name == "schema.json":
            continue
        try:
            sc = load_scenario(path)
        except (ValueError, ValidationError) as e:
            problems.append(f"{path.name}: {str(e)[:500]}")
            continue
        problems += _semantic(sc, path)
        found.append((sc, path))
    all_ids = [s.id for s, _ in found]
    dup = sorted({i for i in all_ids if all_ids.count(i) > 1})
    if dup:
        problems.append(f"id 가 겹친다: {dup}")
    if check_counts and not problems:
        problems += count_problems([s for s, _ in found])
    if ids:
        missing = sorted(set(ids) - set(all_ids))
        if missing:
            problems.append(f"없는 시나리오 id: {missing}")
    if problems:
        raise ScenarioError(problems)
    chosen = [s for s, _ in found if s.set in sets and s.split in splits]
    if ids:
        chosen = [s for s, _ in found if s.id in ids]
    return chosen


def count_problems(scenarios: list[Scenario]) -> list[str]:
    """묶음별 개수(13-2 · 13-4) · 보류 개수(13-5) · 같은 원본의 쪽을 본다."""
    out: list[str] = []
    for set_name, groups in GROUPS.items():
        for g, n in groups.items():
            got = [s for s in scenarios if s.set == set_name and s.group == g]
            if len(got) != n:
                out.append(f"{set_name}/{g}: {n}개여야 하는데 {len(got)}개")
            if set_name == "eval":
                hold = sum(1 for s in got if s.split == "holdout")
                if hold != HOLDOUT_COUNTS[g]:
                    out.append(f"eval/{g}: 보류 {HOLDOUT_COUNTS[g]}개여야 하는데 {hold}개")
    sides: dict[str, set[str]] = {}
    for s in scenarios:
        if s.source:
            sides.setdefault(s.source, set()).add(s.split)
    for src, sp in sorted(sides.items()):
        if len(sp) > 1:
            out.append(f"원본 {src} 의 시나리오가 보류 · 개발 양쪽에 있다")
    return out


__all__ = [
    "GROUPS",
    "GROUP_LABELS",
    "HOLDOUT_COUNTS",
    "SCENARIO_DIR",
    "SCHEMA_FILE",
    "SCHEMA_VERSION",
    "Check",
    "Fault",
    "MemorySpec",
    "Scenario",
    "ScenarioError",
    "Turn",
    "World",
    "build_pitch",
    "build_world",
    "load_scenarios",
    "resolve_pitch",
    "sample_preset",
    "schema_json",
]
