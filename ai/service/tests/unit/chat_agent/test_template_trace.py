"""고정 문장(`templates.py`) ↔ 시험 대조 — 새 고정 문장이 시험 없이 들어오지 못하게 막는다(Issue #1194).

- 왜: 독립 평가에서 늘 실패한 문항 중 여럿이 "설계에 정한 고정 문장을 그 상황에서 코드가 내지 않는" 빈틈이었다(연습한 발표가
  없을 때 · 기준이 바뀐 자동 목표 확인 등). 문장마다 그 상황을 만드는 시험이 있으면 미리 막힌다.
- 규칙: `templates.py`의 고정 문장(`SystemText` 항목 · 대문자 문자열 상수)마다, 서비스 · 연구 시험 어딘가가 그 이름이나 문장의
  고정 부분을 확인해야 한다. 아직 시험이 없는 문장은 아래 두 목록에 까닭과 함께 둔다(지금 상태를 고정하는 래칫).
  - 목록에 없는 새 문장은 시험이 있어야 한다.
  - 목록의 문장에 시험이 생기면 목록에서 빼야 한다(목록이 낡지 않게).
- 고칠 때: 문장에 시험을 더하고 목록에서 뺀다. 문장을 지우면 목록에서도 지운다.
"""

from __future__ import annotations

import re
from pathlib import Path

from pitch_coach_ai.features.chat_agent import templates as T

HERE = Path(__file__).resolve().parent
SERVICE = HERE.parents[2] / "src" / "pitch_coach_ai" / "features" / "chat_agent"
LAB_TESTS = HERE.parents[3] / "research" / "chat-agent" / "tests"

#: 설계(기능 문서 14절 실패 모드 표 · 6절)에 정한 화면 문장인데 지금 코드가 내지 않는 것. 기능 문서 15-4 '설계 문장인데 코드가
#: 내지 않는 고정 문장'에 할 때를 적었다
NOT_EMITTED = {
    "SystemText.ACTION_LIMIT": "L1-23 행동 한도 초과 — 지금은 행동을 버리고 문장은 내지 않음",
    "SystemText.CORPUS_UPDATING": "L4-30 자료 판 변경 — 지금은 409 로 멈춤",
    "SystemText.GOAL_CANNOT_BUILD": "L4-11 — 지금은 GOAL_METRIC_NOT_ALLOWED · 제안 카드로 대신함",
    "SystemText.GOAL_CONFIRM": "L1-24 확인 카드 제목 — 카드 제목은 ASK_CONFIRM_TITLE 이 낸다",
    "SystemText.INJECTION_IGNORED": "L4-31 — 지금은 지시를 따르지 않을 뿐 안내 문장은 내지 않음",
    "SystemText.LLM_DOWN_REPORT_OK": "L1-11 — 지금은 PARTIAL 안내 문장(TARGET v2 3절)이 대신함",
    "SystemText.NO_CAUSE": "L1-36 — 지금은 원인 단정 문장을 검사로 뺄 뿐 안내 문장은 내지 않음",
    "SystemText.NO_NUMERIC_TARGET": "L4-18 — 기준 다시 평가의 숫자 없는 기준",
    "SystemText.RANGE_DIFFERS": "L4-09 — 범위 목표끼리의 비교",
    "SystemText.REASSESS_BOTH_FAILED": "L4-19 — 지금은 작업 완료를 확인하지 못했어요로 대신함",
    "SystemText.RECORD_RECHECKED": "L1-20 — 재개 때 자료 판 변경",
    "SystemText.SCREEN_VALUE": "6절 화면 입력 중인 값",
}

#: 코드가 내지만 아직 그 문장을 확인하는 시험이 없는 것
UNTESTED = {
    "SystemText.ANSWER_INCOMPLETE": "L1-12 형식 실패",
    "SystemText.CROSS_PITCH_NO_COMPARE": "다른 발표의 Take 비교",
    "SystemText.GOAL_AFTER_SAVE": "목표 저장 전에 시작한 Take 의 자동 목표 확인",
    "SystemText.NEXT_PICK_TAKE": "'안 된다' 답의 Take 고르기 다음 걸음",
    "SystemText.RECORD_NOT_FOUND": "기록을 찾을 수 없음",
    "SystemText.SCRIPT_POINTS_REMOVED": "대본 허용으로 오른 점수",
    "SystemText.SOME_ACTIONS_FAILED": "행동 일부 실패",
    "REASSESS_SUMMARY": "다시 평가 확인 카드 요약",
    "REASSESS_TARGETS_NOW": "기준 다시 평가 카드 안내 글",
    "REASSESS_TARGETS_NOW_NONE": "기준 다시 평가 카드 안내 글(기준 없음)",
}


def _key_part(text: str) -> str:
    """자리(`{…}`)를 뺀 고정 부분 중 가장 긴 조각(6자 이상). 시험이 문장을 글자로 확인했는지 찾는 데 쓴다."""
    parts = [p.strip() for p in re.split(r"\{[^}]*\}", text) if len(p.strip()) >= 6]
    return max(parts, key=len) if parts else text


def _templates() -> dict[str, str]:
    out = {f"SystemText.{m.name}": m.value for m in T.SystemText}
    for name in dir(T):
        value = getattr(T, name)
        if name.isupper() and not name.startswith("_") and isinstance(value, str):
            out[name] = value
    return out


def _tested(name: str, text: str, corpus: str) -> bool:
    return name.split(".")[-1] in corpus or _key_part(text) in corpus


def _corpus() -> str:
    files = [p for p in HERE.rglob("*.py") if p.name != Path(__file__).name]
    files += list(LAB_TESTS.rglob("*.py"))
    return "\n".join(p.read_text(encoding="utf-8") for p in files)


def test_every_fixed_sentence_has_a_test_or_a_listed_reason() -> None:
    corpus = _corpus()
    untested = {n for n, t in _templates().items() if not _tested(n, t, corpus)}
    listed = set(NOT_EMITTED) | set(UNTESTED)
    assert not (untested - listed), f"시험이 없는 새 고정 문장: {sorted(untested - listed)} — 그 상황을 만드는 시험을 더한다"
    stale = listed - untested
    assert not stale, f"이제 시험이 있는 문장: {sorted(stale)} — 목록에서 뺀다"


def test_not_emitted_list_is_really_not_emitted_by_code() -> None:
    """'코드가 내지 않음' 목록의 문장이 코드에 쓰이기 시작하면 목록을 `UNTESTED`(또는 시험)로 옮긴다."""
    code = "\n".join(
        p.read_text(encoding="utf-8") for p in SERVICE.rglob("*.py") if p.name != "templates.py"
    )
    used = [n for n in NOT_EMITTED if n.split(".")[-1] in code]
    assert not used, f"코드가 쓰기 시작한 문장: {used}"
