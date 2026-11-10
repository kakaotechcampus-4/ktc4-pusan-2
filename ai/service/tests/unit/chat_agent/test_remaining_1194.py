"""남은 AI 수정 시험 (Issue #1194 합본 4-H): 소리 크기 비교의 기준 음량 출처(R1) · 기준 다시 평가 막기(R5(a)) ·
재개 때 목표 기준 삭제 안내(CA-006) · 재개 때 자료 판 소실 409(CA-016) · 일반 조언의 화면 사용법 낱말 변형(CA-016).
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from pitch_coach_ai.features.chat_agent import compose, templates
from pitch_coach_ai.features.chat_agent.checks import CheckId, check_claims
from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG as CFG
from pitch_coach_ai.features.chat_agent.evidence import EvidenceRegistry
from pitch_coach_ai.features.chat_agent.goals import compare_takes
from pitch_coach_ai.features.chat_agent.loop import LoopState
from pitch_coach_ai.features.chat_agent.schemas import Claim, TakeSnapshot
from pitch_coach_ai.features.chat_agent.turn import _emitted_guides_gone

from . import fakes as F
from .fixtures import sample_pitch, take_snapshot
from .test_actions import RA_MSG, ctx_for, do
from .test_turn import fetched_take, resume_request

T = templates.SystemText


# ── R1 소리 크기 비교의 기준 음량 출처 ─────────────────────────────────────────────


def _snap(take_id: str, source: str | None) -> TakeSnapshot:
    d = take_snapshot(sample_pitch().take(take_id))  # type: ignore[arg-type]
    d = copy.deepcopy(d)
    vol = d["results"]["take_result"]["areas"]["VOLUME"]["take"]
    if source is None:
        vol.pop("base_level_source", None)
    else:
        vol["base_level_source"] = source
    return TakeSnapshot.model_validate(d)


def _cmp(a: str | None, b: str | None) -> Any:
    reg = EvidenceRegistry("run")
    return compare_takes(
        _snap("t-4", a),
        _snap("t-5", b),
        "voice_diff_db",
        None,
        pitch_id="p-1",
        registry=reg,
        version_of=lambda s: None,  # type: ignore[arg-type,return-value]
        cfg=CFG,
    )


@pytest.mark.parametrize(("a", "b"), [("TAKE", "TAKE"), ("CALIBRATION", "TAKE"), ("TAKE", "CALIBRATION")])
def test_volume_take_base_or_different_bases_are_not_comparable(a: str, b: str) -> None:
    c = _cmp(a, b)
    assert c.comparable is False and c.reason == "DIFFERENT_VOLUME_BASE"
    rec = {"previous": 4, "current": 5, "metric": "voice_diff_db", "slide_number": None, "reason": c.reason}
    assert compose.not_comparable_sentence(rec) == (
        "Take 4 · Take 5 기록은 소리 크기를 견준 기준이 달라 소리 크기를 비교할 수 없어요."
    )


def test_volume_same_calibration_base_is_compared() -> None:
    c = _cmp("CALIBRATION", "CALIBRATION")
    assert c.reason != "DIFFERENT_VOLUME_BASE"


# ── R5(a) 기준(targets)을 바꾸는 다시 평가는 이번 판에서 막는다 ─────────────────────────


TARGETS = {"take_id": "t-5", "change": {"targets": [{"metric": "audience_ratio", "operator": "GTE", "value": 0.3}]}}


def test_targets_reassess_is_not_proposed_by_default() -> None:
    p = do("reassess", {**TARGETS, "reason": "내 기준으로"}, message="Take 5 청중 시선 30% 이상 기준으로 다시 평가해 줘")
    assert p.action is None and p.reason_code == "TARGETS_NOT_SUPPORTED"


def test_script_condition_reassess_is_still_proposed() -> None:
    p = do("reassess", {"take_id": "t-5", "change": {"script_used": True}, "reason": "대본 사용"}, message=RA_MSG)
    assert p.action is not None and p.action.type == "REASSESS"


def test_turn_says_targets_reassess_is_not_possible() -> None:
    msg = "Take 5를 청중 시선 30% 이상 기준으로 다시 평가해 줘"
    ask = F.draft(msg, type_="REQUEST", claim_op=None, take_numbers=[5], polarity="NEUTRAL")
    llm = F.FakeLlm(
        [
            F.plan(assessments=[ask]),
            F.tools(("reassess", {**TARGETS, "reason": "내 기준"}), prefix="r"),
            F.done(),
        ],
        answers=[F.answer([])],
    )
    _, _, data = F.run_turn(F.request(user_message=msg), llm)
    texts = [c["text"] for c in data["message"]["claims"]]
    assert T.REASSESS_TARGETS_BLOCKED.value in texts
    assert data["actions"] == []


# ── CA-006 재개 때 목표의 기준 Take 가 지워짐(명시적 삭제 신호) ───────────────────────


def _goal_request() -> dict[str, Any]:
    ex = json.loads((F.EXAMPLES / "request_take_reviewed.json").read_text(encoding="utf-8"))
    goal = ex["context"]["conversation_goal"]
    goal["base"].update({"take_id": "t-4", "take_number": 4, "value": 0.10})
    req = F.request(user_message="대화 목표 뭐였지? 그리고 Take 3도 봐 줘")
    req["context"]["conversation_goal"] = goal
    req["context"]["goal_version"] = goal["version"]
    req["context"]["loaded_takes"] = []
    return req


def test_goal_base_deleted_while_waiting_is_told() -> None:
    req = _goal_request()
    llm = F.FakeLlm(
        [
            F.plan(),
            F.tools(("get_goals", {}), prefix="g"),
            F.tools(("fetch_take", {"take_id": "t-3", "reason": "Take 3 확인"}), prefix="a"),
        ]
    )
    _, _, data = F.run_turn(req, llm)
    assert data["status"] == "NEEDS_ACTION"
    res = [
        {
            "action_id": data["actions"][0]["action_id"],
            "type": "FETCH_TAKE",
            "status": "SUCCEEDED",
            "data": {"take": fetched_take("t-3")},
            "error": None,
        }
    ]
    req2 = resume_request(req, data, res)
    req2["context"]["removed_sources"] = ["t-4"]
    req2["context"]["conversation_goal"] = None  # BE 가 목표를 비활성화했다
    req2["context"]["pitch"]["takes"] = [t for t in req2["context"]["pitch"]["takes"] if t["take_id"] != "t-4"]
    _, _, data2 = F.run_turn(req2, F.FakeLlm([F.done(), F.answer([])]))
    texts = [c["text"] for c in (data2.get("message") or {}).get("claims") or []]
    assert T.GOAL_BASE_DELETED.value in texts


def test_removed_take_that_is_not_the_goal_base_says_nothing_about_the_goal() -> None:
    req = _goal_request()
    llm = F.FakeLlm(
        [
            F.plan(),
            F.tools(("get_goals", {}), prefix="g"),
            F.tools(("fetch_take", {"take_id": "t-3", "reason": "Take 3 확인"}), prefix="a"),
        ]
    )
    _, _, data = F.run_turn(req, llm)
    res = [
        {
            "action_id": data["actions"][0]["action_id"],
            "type": "FETCH_TAKE",
            "status": "SUCCEEDED",
            "data": {"take": fetched_take("t-3")},
            "error": None,
        }
    ]
    req2 = resume_request(req, data, res)
    req2["context"]["removed_sources"] = ["t-5"]
    _, _, data2 = F.run_turn(req2, F.FakeLlm([F.done(), F.answer([])]))
    texts = [c["text"] for c in (data2.get("message") or {}).get("claims") or []]
    assert T.GOAL_BASE_DELETED.value not in texts


# ── CA-016 재개 때 이미 낸 자료 인용의 카드가 지금 묶음에 없으면 409 ─────────────────────


class _Corpus:
    def __init__(self, ids: set[str]) -> None:
        self.ids = ids

    def get(self, sid: str) -> object | None:
        return object() if sid in self.ids else None


class _Deps:
    def __init__(self, corpus: Any) -> None:
        self.corpus = corpus


def test_emitted_guide_with_a_vanished_card_stops_the_resume() -> None:
    st = LoopState()
    st.emitted = [{"kind": "GUIDE", "text": "…", "source_ids": ["g-07"]}]
    assert _emitted_guides_gone(st, _Deps(_Corpus({"g-01"})))  # type: ignore[arg-type]
    assert _emitted_guides_gone(st, _Deps(None))  # type: ignore[arg-type]
    assert not _emitted_guides_gone(st, _Deps(_Corpus({"g-07"})))  # type: ignore[arg-type]
    st.emitted = [{"kind": "RECORD", "text": "…", "source_ids": []}]
    assert not _emitted_guides_gone(st, _Deps(None))  # type: ignore[arg-type]


# ── CA-016 일반 조언의 화면 사용법 낱말 변형 ─────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "오른쪽 위 버튼을 누르면 바로 시작돼요.",
        "메뉴에서 고르면 돼요.",
        "아이콘을 클릭하면 열려요.",
        "위쪽 탭을 옮겨 보세요.",
        "발표 자료를 업로드하면 돼요.",
        "입력란에 적어 두면 돼요.",
    ],
)
def test_general_usage_word_variants_are_blocked(text: str) -> None:
    fails = check_claims(
        [Claim(kind="GENERAL", text=text)], registry=EvidenceRegistry("run"), guide_searched=True, cfg=CFG
    )
    assert any(f.check_id == CheckId.GENERAL_RULE and f.unfixable for f in fails)
