"""한 턴의 출력 전체를 보는 마지막 검사 — 가드레일 (기능 문서 6-9 '한 턴 전체 검사', Issue #1194 화면 검토).

- 무엇을 막나: 문장 하나씩 보는 답 검사(숫자 · 근거 · 종류, `checks.py`)로는 잡히지 않는 **같은 사실의 반복**과 **앞뒤가 어긋나 보이는
  '확인 불가'**. 원인은 앞 단계(사용자 말 판정 `claims.py` · 할 일 단계 `progress.py`)에서 먼저 고치고, 이 검사는 거기서 놓친 것을 막는다.
- 언제: 답 문장은 검사를 통과하는 대로 흘려보내고 나간 문장은 지우지 않는다(5-4 · `architecture.md` 9절). 그래서 다 만든 뒤 고치지 않고,
  **문장을 내보내기 직전에** 그 턴(실행)에서 이미 나간 문장 · 그 턴의 도구 결과(근거 등록부)와 견준다. 숫자 검사를 통과한 문장에만
  그 뒤에서 돈다(숫자 검사 · 한 번 고치기는 그대로). 모델을 다시 부르지 않는다(코드 규칙).
- 규칙(규칙 코드 · 효과):
  - `DUP_FACT` · 뺀다: 기록 문장(`RECORD`)이 단 근거 id 가 모두 이미 나간 문장의 것이고, 문장 속 값(숫자 + 단위)이 하나 이상이며 모두
    이미 나간 문장의 값일 때. 새 근거나 새 값이 하나라도 있으면 빼지 않는다(값이 없는 문장도 빼지 않는다).
  - `UNVERIFIABLE_NO_CRITERION` · 바꾼다: '이 기록으로는 확인할 수 없어요' 판정 문장인데, 그 대상의 값은 그 턴 도구 결과에 있고 판단 기준
    (그 차원의 리뷰 판정 · 그 영역의 문제 구간 · 그 지표의 대화 목표)이 없다고 **확인됐을 때만** 고정 문장
    "기록된 값은 있지만 높고 낮음을 판단할 기준은 이 기록에 없어요."로 바꾼다.
  - `UNVERIFIABLE_HAS_CRITERION` · `UNVERIFIABLE_CRITERION_UNKNOWN` · 남기기만: 기준이 있거나 있는지 확실히 모르는 '확인 불가'는
    바꾸지 않는다(앞 단계 — 사용자 말 판정에서 고칠 대상).
- 남기는 것: 결정 로그(`agent_decision`, 신호 `OUTPUT_CHECK`, 까닭 = 규칙 코드, 효과 `DROPPED` · `REPLACED` · `NOTED`, 한 건에 한 줄).
  문장 원문은 남기지 않는다.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from . import checks as chk
from . import compose, templates
from .schemas import Claim
from .values import METRIC_AREA

#: 판단 첫 문장 중 '확인 불가'(templates.UNVERIFIABLE)의 꼬리. 판정이 UNVERIFIABLE 인 판단 문장에서만 본다
_UNVERIFIABLE_TAIL = "이 기록으로는 확인할 수 없어요"


def values_of(text: str) -> set[tuple[float, str]]:
    """문장 속 값(숫자 + 단위). 비율 표기는 퍼센트로 맞춘다(0-2 ①: 같은 값의 다른 표기를 같게)."""
    out: set[tuple[float, str]] = set()
    for t in chk.extract_numbers(text).values:
        unit = "%" if t.unit in ("%", "퍼센트") else t.unit
        out.add((round(t.value, 4), unit))
    return out


class OutputGuard:
    """한 실행(턴)의 나간 문장을 기억하고, 다음 문장을 내보내기 직전에 규칙을 적용한다."""

    def __init__(self, lp: Any) -> None:
        self.lp = lp
        self.ids: set[str] = set()
        self.values: set[tuple[float, str]] = set()
        self.counts: Counter[tuple[str, str]] = Counter()
        for d in lp.st.emitted:  # 이어 부른 실행이면 앞 응답에서 낸 문장도 같은 턴의 문장이다
            self.note(Claim.model_validate(d), [])

    def _evidence_ids(self, claim: Claim, assessments: list[dict[str, Any]]) -> set[str]:
        ids = {*claim.evidence_ids}
        if claim.kind == "ASSESSMENT":
            ids |= {e for a in assessments if a.get("assessment_id") == claim.assessment_id for e in a.get("evidence_ids") or []}
        return {e["evidence_id"] for e in compose.referenced(self.lp.ctx, sorted(ids))} | ids

    def note(self, claim: Claim, assessments: list[dict[str, Any]]) -> None:
        """나간 문장의 근거 · 값을 기억한다."""
        self.ids |= self._evidence_ids(claim, assessments)
        self.values |= values_of(claim.text)

    def admit(self, claim: Claim, assessments: list[dict[str, Any]]) -> Claim | None:
        """내보낼 문장(검사를 통과한 것): 그대로 · 바꾼 문장 · None(뺌)."""
        cfg = self.lp.cfg
        if not getattr(cfg, "output_guard", True):
            return claim
        if claim.kind == "RECORD" and claim.evidence_ids:
            ids = self._evidence_ids(claim, assessments)
            vals = values_of(claim.text)
            if ids <= self.ids and vals and vals <= self.values:
                self.counts[("DUP_FACT", "DROPPED")] += 1
                return None
        if claim.kind == "ASSESSMENT" and _UNVERIFIABLE_TAIL in claim.text:
            a = next((x for x in assessments if x.get("assessment_id") == claim.assessment_id), None)
            if a is not None and a.get("verdict") == "UNVERIFIABLE":
                state = self._criterion(a)
                if state == "ABSENT":
                    self.counts[("UNVERIFIABLE_NO_CRITERION", "REPLACED")] += 1
                    return Claim(kind="SYSTEM", text=templates.SystemText.VALUE_NO_CRITERION.value)
                if state == "PRESENT":
                    self.counts[("UNVERIFIABLE_HAS_CRITERION", "NOTED")] += 1
                elif state == "UNKNOWN":
                    self.counts[("UNVERIFIABLE_CRITERION_UNKNOWN", "NOTED")] += 1
        return claim

    def _criterion(self, a: dict[str, Any]) -> str | None:
        """'확인 불가' 판단의 대상에 판단 기준이 있는가(그 턴의 도구 결과 + 대화 목표로).

        - None: 그 대상의 값이 그 턴 도구 결과에 없다(이 규칙 밖).
        - `PRESENT`: 그 지표의 대화 목표 · 그 차원의 리뷰 판정 · 그 영역의 문제 구간(1곳 이상) 중 하나가 있다.
        - `ABSENT`: 리뷰가 없다고 확인했고(리뷰 없음 근거) 그 영역의 문제 구간이 0곳(또는 측정 안 됨)이며 대화 목표도 없다.
        - `UNKNOWN`: 그 밖(읽지 않아 모름). 바꾸지 않는다."""
        ctx = self.lp.ctx
        subject = a.get("subject") or {}
        metric = subject.get("metric")
        dim = subject.get("dimension")
        takes = set(subject.get("take_ids") or [])
        reg = ctx.registry
        cur = [ev for eid, ev in reg.items.items() if reg.is_current(eid) and (not takes or ev.take_id in takes or ev.take_id is None)]
        if not metric or not any(ev.metric == metric and ev.take_id is not None for ev in cur):
            return None
        goal = ctx.goal
        if goal is not None and getattr(goal, "metric", None) == metric:
            return "PRESENT"
        area = METRIC_AREA.get(metric)
        if dim and any(ev.path.endswith(f".dimensions.{dim}") for ev in cur):
            return "PRESENT"
        seg = [ev for ev in cur if area is not None and compose.segment_area(ev) == area]
        if any(not (isinstance(ev.value, int) and ev.value == 0) for ev in seg):
            return "PRESENT"
        review_missing = any(compose.none_kind(ev) == "REVIEW" for ev in cur)
        if review_missing and seg:
            return "ABSENT"
        return "UNKNOWN"

    def log(self) -> None:
        """규칙 코드 · 건수를 결정 로그로(한 건에 한 줄). 문장 원문은 남기지 않는다."""
        for (rule, effect), n in sorted(self.counts.items()):
            for _ in range(n):
                self.lp._decide("OUTPUT_CHECK", [rule], effect, "CODE")
        self.counts.clear()
