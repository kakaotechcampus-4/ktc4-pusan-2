"""LLM 교차 검증: 규칙과 어긋난 문장만 다시 판정한다 (충돌이 있는 슬라이드당 1회)."""

from ..shared.llm_step import config_hash
from ..shared.rubric import EvaluationRubric
from ..shared.text import NormalizedScript
from .merge import CONFLICT_TEXT, status_from_units
from .prompts.verifier import VERIFIER_PROMPT
from .schemas import Alignment, FactCheck, SentenceResult, SimilarItem, VerifierResult


def verifier_config_hash(model: str) -> str:
    return config_hash(
        {"model": model, "prompt": VERIFIER_PROMPT, "schema": VerifierResult.model_json_schema()}
    )


def _stt_window(ids, stt_norm: NormalizedScript) -> list[str]:
    """번호들과 앞뒤 한 문장씩."""
    ids = sorted({k for j in ids for k in (j - 1, j, j + 1) if 0 <= k < len(stt_norm.sentences)})
    return [f"  [T{j}] {stt_norm.sentences[j].text}" for j in ids] or ["  (없음)"]


FACT_DETAIL = {
    "matched": "STT 에 있음 ({stt})",
    "mismatched": "STT 에는 {stt} (발음이 전혀 다름 — 발표자가 다르게 말함)",
    "missing": "STT 에 없음",
    "unverified": "확인 못 함",
    "sound_alike": "STT 에는 발음이 비슷한 {stt} (판정에서 뺌)",
    "approximate": "STT 에는 {stt} (어림해 말함)",
}


def verifier_message(
    rubric: EvaluationRubric,
    sentences: list[SentenceResult],
    fact_checks: list[FactCheck],
    alignments: list[Alignment],
    stt_norm: NormalizedScript,
    items: list[SimilarItem],
) -> str:
    """검증 입력: 충돌한 문장만, 필요한 정보만 (대본 문장, 앞뒤 대본 문장, 속한 Key Point, 1차 판정, 관련 STT 문장, 규칙 확인, 비슷한 말, 충돌 이유)."""
    checks = {c.fact_id: c for c in fact_checks}
    aligned = {a.sentence_index: a.stt_ids for a in alignments}
    by_id = {it.item_id: it for it in items}
    lines = ["# 검증 대상"]
    for s in sentences:
        if not s.conflicts:
            continue
        kps = [kp for kp in rubric.key_points if s.sentence_index in kp.sentence_indices]
        # 관련 STT 문장 = LLM 근거 ∪ 규칙 정렬 구간, 앞뒤 한 문장씩 더
        ids = set(s.evidence) | set(aligned.get(s.sentence_index, []))
        lines += [f'## S{s.sentence_index} "{s.text}"']
        # 앞뒤 대본 문장: STT 에 나온 말이 이 문장의 내용인지 이웃 문장의 내용인지 가리게 한다
        neighbors = [
            f"[S{k}] {rubric.sentences[k].text}"
            for k in (s.sentence_index - 1, s.sentence_index + 1)
            if 0 <= k < len(rubric.sentences)
        ]
        if neighbors:
            lines.append("앞뒤 대본 문장 (이 문장과 구별할 것): " + " / ".join(neighbors))
        lines += [f"속한 Key Point: {kp.id} [{kp.importance}] {kp.content}" for kp in kps]
        lines.append(f"1차 판정: {s.first_status} (확신도 {s.confidence}) — {s.reason}")
        lines += ["전달 단위 (1차 판정):"] + [
            f"  {u.unit_id} {u.text} — {u.first_status}" for u in s.units
        ]
        lines.append("관련 STT 문장:")
        lines += _stt_window(ids, stt_norm)
        fact_lines = [
            f"  {checks[fid].value}: "
            + FACT_DETAIL[status].format(stt=checks[fid].stt_value or "다른 말")
            for fid, status in s.fact_statuses.items()
        ]
        lines += ["규칙 기반 확인:"] + (fact_lines or ["  (핵심 수치·이름 없음)"])
        similar = [by_id[i] for i in s.similar_ids if i in by_id]
        if similar:
            lines.append("발음이 비슷한 말 (판정에서 뺌):")
            lines += [
                f"  대본 '{it.script_text}' → STT '{it.stt_text}' [T{it.stt_sentence_index}]"
                for it in similar
            ]
        lines.append(f"이 문장의 단어가 STT 에 나온 비율: {s.lexical_coverage:.0%}")
        if "evidence_unrelated" in s.conflicts:
            lines.append(
                f"1차 근거 {', '.join(f'T{t}' for t in s.evidence)} 에 이 문장의 단어가 나온 비율: {s.evidence_coverage:.0%}"
            )
        lines.append("충돌 이유: " + "; ".join(CONFLICT_TEXT[c] for c in s.conflicts))
        lines.append("")
    return "\n".join(lines)


def verify(message: str, verifier_llm) -> VerifierResult:
    """LLM API (교차 검증): 충돌한 문장만 다시 판정한다. 슬라이드마다 한 번에 묶어 부른다."""
    return verifier_llm.invoke([("system", VERIFIER_PROMPT), ("user", message)])


def apply_verification(sentences: list[SentenceResult], verified: VerifierResult | None) -> None:
    if verified is None:
        return
    by_id = {v.sentence_id: v for v in verified.judgments}
    for s in sentences:
        v = by_id.get(s.sentence_index)
        if s.conflicts and v is not None:
            # 검증자가 다시 판정한 단위만 바꾸고, 문장 판정은 코드가 다시 모은다
            final = {u.unit_id.strip().upper(): u.status for u in v.units}
            for u in s.units:
                u.status = final.get(u.unit_id.upper(), u.status)
                if (
                    u.raised_by_rule and u.status == "missing"
                ):  # 규칙 보정(수치·이름을 말함)은 검증 뒤에도 유지한다
                    u.status = "vague"
            s.status, s.reason, s.verified = (
                status_from_units([u.status for u in s.units]),
                f"[검증] {v.reason}",
                True,
            )
