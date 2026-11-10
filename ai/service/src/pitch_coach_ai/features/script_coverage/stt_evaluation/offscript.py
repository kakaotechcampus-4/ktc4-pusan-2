"""대본 밖 구간의 LLM 분류 (기본 꺼짐, 설정 `OFFSCRIPT_LLM_ENABLED`).

규칙이 `off_script` 로 남긴 구간을 관련 곁말 · 무관한 말 · 진행 말 · 판단 보류로 나눈다. 점수는 바꾸지 않는다.
`core.py` 에 엮을 순서: `offscript_message` (None 이면 건너뜀) → `llm_step(kind, …, classify_offscript,
llm, offscript_config_hash(model), OffscriptJudgment, check=…)` 에서 `check_offscript` 로 검사 →
`apply_offscript_judgments` 로 반영 → 반영한 구간 목록으로 `build_speech_mix`.

- `OffscriptJudgment`: 출력 스키마. 클래스 이름 · docstring · 필드 순서 · description 이 설정 해시에 들어간다.
- `offscript_config_hash` · `offscript_message` · `classify_offscript`: 해시 · 입력 · 호출.
- `check_offscript`: 대상 구간을 빠짐 · 겹침 없이 답했는지 검사 (아니면 `LLM_INVALID_OUTPUT`).
- `apply_offscript_judgments`: 결과를 구간 목록에 반영한다.
"""

from typing import Literal

from pydantic import BaseModel, Field

from ..shared.llm_step import config_hash
from ..shared.rubric import EvaluationRubric
from .config import SPEECH_MIX_VALIDATED
from .prompts.offscript import OFFSCRIPT_PROMPT
from .schemas import Offscript


class OffscriptItem(BaseModel):
    span_id: str = Field(description="구간 번호. 입력에 적힌 그대로 (예: O1)")
    category: Literal["related_aside", "unrelated", "procedural", "undetermined"]
    reason: str = Field(description="분류 이유 한 문장")


class OffscriptJudgment(BaseModel):
    """대본 밖 구간 분류 결과."""

    items: list[OffscriptItem] = Field(description="입력한 구간마다 하나씩")


class IncompleteOffscript(ValueError):
    """분류 출력이 대상 구간을 다 덮지 못했다. 그 장은 LLM_INVALID_OUTPUT 으로 다룬다."""

    code = "LLM_INVALID_OUTPUT"


def offscript_config_hash(model: str) -> str:
    return config_hash(
        {
            "model": model,
            "prompt": OFFSCRIPT_PROMPT,
            "schema": OffscriptJudgment.model_json_schema(),
        }
    )


def _targets(offscript: Offscript):
    """분류 대상: 규칙이 `off_script` 로 정한 구간."""
    return [s for s in offscript.spans if s.category == "off_script" and s.source == "rule"]


def offscript_message(
    rubric: EvaluationRubric, offscript: Offscript, stt_sentences: list[str]
) -> str | None:
    """분류 입력: 핵심 주장 · 대본 문장 · 대상 구간(`[O1] …`). 대상이 없으면 None."""
    targets = _targets(offscript)
    if not targets:
        return None
    lines = ["# 이 장의 핵심 주장", rubric.core_claim, "", "# 이 장의 대본 문장"]
    lines += [f"[S{s.index}] {s.text}" for s in rubric.sentences]
    lines += ["", "# 분류할 구간"]
    for s in targets:
        sentence = stt_sentences[s.stt_sentence_index]
        lines.append(f"[{s.id}] {sentence[s.start : s.end]}")
    return "\n".join(lines)


def classify_offscript(message: str, llm) -> OffscriptJudgment:
    """LLM API: 대상 구간을 한 번에 분류한다."""
    return llm.invoke([("system", OFFSCRIPT_PROMPT), ("user", message)])


def check_offscript(offscript: Offscript, result: OffscriptJudgment) -> None:
    """대상 구간마다 답이 정확히 하나여야 한다. 빠지거나 겹치면 `IncompleteOffscript`.

    대상이 아닌 구간 번호의 답은 쓰지 않으므로 있어도 된다.
    """
    given = [i.span_id.strip().upper() for i in result.items]
    for span in _targets(offscript):
        if given.count(span.id) != 1:
            raise IncompleteOffscript(f"{span.id} 답 {given.count(span.id)}개")


def apply_offscript_judgments(offscript: Offscript, result: OffscriptJudgment) -> Offscript:
    """대상 구간의 범주 · 출처(`llm`) · 화면 범주를 바꾼 새 목록. `OFFSCRIPT_DISABLED` 는 뺀다."""
    answers = {}
    for item in result.items:
        answers.setdefault(item.span_id.strip().upper(), item.category)
    spans = []
    for span in offscript.spans:
        category = answers.get(span.id) if span in _targets(offscript) else None
        if category is None:
            spans.append(span)
            continue
        display = category if category in SPEECH_MIX_VALIDATED else "off_script"
        spans.append(
            span.model_copy(
                update={"category": category, "display_category": display, "source": "llm"}
            )
        )
    reasons = [
        r for r in offscript.reason_codes if r not in ("OFFSCRIPT_DISABLED", "NOT_VALIDATED")
    ]
    if offscript.state == "ok" and any(s.category != s.display_category for s in spans):
        reasons.append("NOT_VALIDATED")
    return offscript.model_copy(update={"spans": spans, "reason_codes": reasons})
