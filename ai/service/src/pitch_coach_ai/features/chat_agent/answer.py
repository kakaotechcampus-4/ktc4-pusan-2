"""답 문장 만들기 · 검사 · 문장 줄 (기능 문서 5-4 '문장 줄' · 6-1 5번 · 6-3 · 6-9).

- 누가 쓰나: turn.py 의 답 쓰기(`_answer`)와 승인 대기 전 답(`_ask_pause`). 서비스 탑재 뒤에도 그대로 쓴다.
- 하는 일
  1. 앞 문장: 모델을 부르기 전에 정해진 코드 문장(행동 결과 · 행동 안내 · 기록이 바뀐 안내 · 조사 중단 안내)을 검사해
     먼저 낸다.
  2. 모델이 답 초안(JSON)을 흘려 보내는 동안 `StreamTracker`로 `route` · 판단 목록 · 문장 원소가 닫히는 때를 잡는다. 판단
     목록이 닫히면 판단을 확정(코드 판정)하고, 최종 답과 같은 순서로 확정된 문장을 만들어 한 문장씩 검사한다. 통과한 문장부터
     `claim` 줄로 내고, 걸린 문장이 나오면 그 문장과 뒤는 보류한다(앞 문장이 확정되기 전에 뒤 문장을 내지 않는다).
  3. 초안을 다 받으면 같은 순서로 다시 만들고, 이미 낸 문장은 그대로 둔 채 나머지만 검사 → 한 번 고치기 → 다시 검사한다.
     판단 문장의 모델 설명만 걸렸으면 그 자리에 코드가 확정한 첫 문장을 남긴다.
- 판단 설명은 판단의 근거에 설명이 이은 이번 턴 근거를 더해 검사한다(설명 속 관측 값, 6-9).
- 문장 순서: 앞 문장 → 판단 문장(코드 첫 문장 + 모델 설명, 판단 순서) → 다루지 못한 말 안내 → 비교할 수 없는 까닭(코드 문장) → 초안의 결론 · 기록 · 자료
  문장(초안 순서) → 일반 조언 안내(고정 SYSTEM) + 일반 조언(`GENERAL`, 6-10). 판단 설명(`ASSESSMENT` 원소)은 초안 `claims`
  맨 앞에 이어 온 것만 쓰고, 다른 종류가 나온 뒤의 설명은 버린다. 스트림에서 판단 문장을 일찍 확정하려는 것이고, 프롬프트도
  설명을 맨 앞에 쓰게 한다. 같은 까닭으로 일반 조언은 초안 맨 끝에 이어 온 것만 쓴다: 첫 일반 조언 뒤의 다른 종류는 버리고,
  일반 조언은 앞의 `max_general_claims`개만 쓴다(승인 대기 전 답에 낸 수를 뺀다). 판단 · 비교 결론에 이은 일반 조언 원소는 쓰지 않는다.
  안내는 첫 일반 조언과 함께 나가고(실행에 한 번), 일반 조언이 모두 빠지면 뺀다.
- 문장 줄에는 실행 전체에서 이어지는 번호와 그 줄이 처음 쓰는 근거 · 자료 사본, 아직 보내지 않은 확정 판단을 싣는다
  (`ClaimLines`). 그래서 문장 줄을 하나라도 받았으면 그때까지 확정된 판단이 모두 BE에 있다.
- 스레드: 응답 조각은 LLM 실행 풀의 스레드에서 들어온다. 잠금 하나로 묶고, 답 쓰기 호출이 끝나면 `close()`로 늦게 온 조각을
  버린다. 스트림 처리에서 예외가 나거나 같은 최상위 키가 두 번 오면 그 뒤로는 흘려 내지 않고 초안을 다 받은 뒤
  한꺼번에 낸다(답은 막지 않는다).
- 하지 않는 것: 상태 · 행동 정하기와 끝 줄 조립(turn.py), 줄 쓰기 · 크기 한도(turn.py `_Writer`).
- 고칠 때: 검사 규칙은 checks.py, 판단 확정은 claims.py, 템플릿 문장은 compose.py · templates.py.
"""

from __future__ import annotations

import dataclasses
import json
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import checks as chk
from . import compose, templates
from .schemas import Assessment, Claim
from .stream_json import AssessmentsDone, ClaimClosed, StreamTracker, ValueDone

#: 모델 문장을 스트림으로 내도 되는 답 초안 route. 범위 밖 답(OUT_OF_SCOPE)은 고정 거절 문장으로 끝나므로 내지 않는다
STREAM_ROUTES = ("ANSWER", "NO_GUIDE", "CANNOT_DO")
#: 일반 조언 묶음 바로 앞의 고정 안내(6-10)
GENERAL_INTRO = templates.SystemText.GENERAL_INTRO.value


def is_general_intro(c: Claim) -> bool:
    return c.kind == "SYSTEM" and c.text == GENERAL_INTRO


def _drop_lone_intro(claims: list[Claim]) -> list[Claim]:
    """뒤에 일반 조언이 하나도 남지 않은 안내 문장을 뺀다(일반 조언이 검사로 모두 빠진 때)."""
    out = list(claims)
    for i, c in enumerate(out):
        if is_general_intro(c) and not any(x.kind == "GENERAL" for x in out[i + 1 :]):
            del out[i]
            break
    return out


def assessment_head(o: Any) -> str:
    """판단 문장의 코드 부분: 첫 문장(템플릿) + 사실 문장. SUPPORTED 인 요청은 첫 문장이 비어 있어 근거 문구로 그린다."""
    head = " ".join(x for x in [o.sentence, *o.extra_sentences] if x)
    if head:
        return head
    a = o.assessment
    if a.verdict == "SUPPORTED" and o.evidence_phrase and a.type != "REQUEST":
        # 요청 · 정보 질문은 사실 주장이 아니므로 '기록과 맞아요'를 그리지 않는다(6-3 REQUEST)
        tpl = templates.KNOWLEDGE_SUPPORTED if a.type == "KNOWLEDGE" else templates.SUPPORTED
        return tpl.format(quote=a.quote, evidence=o.evidence_phrase)
    return ""


def guide_texts(lp: Any) -> dict[str, str]:
    """이번 실행에서 읽은 자료의 본문만(checks · claims 의 전제)."""
    corpus = lp.ctx.corpus
    out: dict[str, str] = {}
    if corpus is None:
        return out
    for sid in lp.ctx.registry.sources:
        t = corpus.text_of(sid)
        if t:
            out[sid] = t
    return out


def _key(c: Claim) -> str:
    return json.dumps(c.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)


def with_prefix(emitted: list[dict[str, Any]], claims: list[Claim]) -> list[Claim]:
    """낸 문장을 앞에 그대로 두고, 뒤 목록에서 낸 문장과 같은 것을 뺀다(5-4 · 5-5: 낸 문장은 언제나 최종 답의 앞부분)."""
    left = Counter(json.dumps(d, sort_keys=True, ensure_ascii=False) for d in emitted)
    rest: list[Claim] = []
    for c in claims:
        k = _key(c)
        if left[k] > 0:
            left[k] -= 1
            continue
        rest.append(c)
    return [*(Claim.model_validate(d) for d in emitted), *rest]


class ClaimLines:
    """문장 줄(`claim`, 5-4)을 낸다. 실행 전체에서 이어지는 번호와, 그 줄이 처음 쓰는 근거 · 자료 사본과, 아직 보내지 않은
    확정 판단을 싣는다. 앞 응답에서 낸 문장 · 내보낸 판단과 그 근거는 이미 보낸 것으로 센다(서명 상태에서 되살림)."""

    def __init__(self, lp: Any) -> None:
        self.lp = lp
        #: 앞 응답에서 낸 문장 수(이 응답의 첫 문장 줄 번호)
        self.base = len(lp.st.emitted)
        self.next_index = self.base
        self.sent_ev: set[str] = set()
        self.sent_src: set[str] = set()
        #: 이 응답의 문장 줄이 보낸 자료 카드(자료 id → 사본). 끝 줄의 `sources`가 같은 사본을 쓴다
        self.sent_cards: dict[str, dict[str, Any]] = {}
        self.sent_as: set[str] = set()
        #: 이 응답에서 줄로 보낸 판단. 끝 줄에도 싣는다(어느 끝에서도 빠지지 않게, turn.py `_finish`)
        self.sent_assessments: list[dict[str, Any]] = []
        for d in lp.st.emitted:
            self._mark(d.get("evidence_ids") or [], d.get("source_ids") or [])
        for a in lp.st.exported:
            self.sent_as.add(a["assessment_id"])
            self._mark(a.get("evidence_ids") or [], a.get("source_ids") or [])

    def _mark(self, ev_ids: list[str], src_ids: list[str]) -> None:
        self.sent_ev.update(e["evidence_id"] for e in compose.referenced(self.lp.ctx, ev_ids))
        self.sent_src.update(src_ids)

    def send(self, claim: Claim, confirmed: list[dict[str, Any]], *, record: bool = True) -> None:
        """문장 하나를 줄로 낸다. `record`면 서명 상태의 낸 문장(`st.emitted`)에도 더한다(끝 줄 조립 뒤의 남은 문장은 False)."""
        lp = self.lp
        new_as = [a for a in confirmed if a["assessment_id"] not in self.sent_as]
        ev_ids = [*claim.evidence_ids, *(e for a in new_as for e in a.get("evidence_ids") or [])]
        src_ids = [*claim.source_ids, *(s for a in new_as for s in a.get("source_ids") or [])]
        evidence = [
            e for e in compose.referenced(lp.ctx, ev_ids) if e["evidence_id"] not in self.sent_ev
        ]
        sources = compose.guide_cards(
            lp.ctx, [s for s in src_ids if s not in self.sent_src], compose.claim_quotes([claim])
        )
        dump = claim.model_dump(mode="json")
        lp.emit(
            "claim",
            {
                "index": self.next_index,
                "claim": dump,
                "evidence": evidence,
                "sources": [g.model_dump(mode="json") for g in sources],
                "assessments": new_as,
            },
        )
        for g in sources:
            self.sent_cards[g.source_id] = g.model_dump(mode="json")
        self.next_index += 1
        self.sent_ev.update(e["evidence_id"] for e in evidence)
        self.sent_src.update(src_ids)
        self.sent_as.update(a["assessment_id"] for a in new_as)
        self.sent_assessments.extend(new_as)
        if record:
            lp.st.emitted.append(dump)


@dataclass
class Composed:
    """답 문장 만들기 · 검사의 결과."""

    #: 검사 전의 본문 문장(앞 문장 제외, 이미 낸 본문 포함)
    out: list[Claim]
    #: 검사를 통과해 낸 앞 문장
    pre: list[Claim]
    #: 검사를 통과한 본문 문장(스트림에서 낸 것 + 보류했다 통과한 것, 최종 답 순서)
    body: list[Claim]
    #: 이 실행에서 내보낸 모든 판단(앞서 내보낸 것 + 이번에 확정한 것)
    all_assessments: list[Assessment]
    amap: dict[str, Assessment]

    @property
    def final_claims(self) -> list[Claim]:
        return [*self.pre, *self.body]


@dataclass
class _Fin:
    """판단 확정 결과."""

    result: Any
    outcomes: list[Any]
    all_assessments: list[Assessment]
    amap: dict[str, Assessment]
    #: 판단 id → 코드가 그린 첫 문장(검사용) · 판단 문장의 코드 부분(첫 문장 + 사실 문장)
    first: dict[str, str] = field(default_factory=dict)
    heads: dict[str, str] = field(default_factory=dict)


class Composer:
    """한 번의 답 쓰기 호출(`answer_phase`)에 하나. LLM 쪽에는 `TextSink`(start · feed · committed)로 넘긴다."""

    def __init__(
        self,
        lp: Any,
        *,
        pre: list[Claim],
        finalize: Callable[[list[Assessment] | None], Any],
        lines: ClaimLines,
    ) -> None:
        self.lp = lp
        self.lines = lines
        self._finalize_fn = finalize
        self._lock = threading.Lock()
        st, ctx = lp.st, lp.ctx
        #: 이 답을 쓰기 전에 이미 낸 문장(앞 응답) — 같은 글의 모델 문장은 되풀이하지 않는다
        self.prior_texts = {d["text"] for d in st.emitted}
        self.exported = [Assessment.model_validate(a) for a in st.exported]
        # 내보낸 판단은 다시 확정하지 않는다. 다만 이어 부르는 사이 기록 판이 바뀌었으면 새 자료로 다시 판단해
        # 앞 판단을 대체(supersedes)로 잇는다(앞 판단과 그 근거 사본은 그대로 남김, 5-5 · 6-3 · L1-32)
        self.refinal = bool(self.exported) and lp.record_changed
        self.orig_drafts = list(st.drafts)
        self.succeeded = {
            e["action_id"]: e["success_text"]
            for e in st.executed
            if e.get("status") == "SUCCEEDED" and e.get("success_text")
        }
        self.approved = {
            sid
            for sid in ctx.registry.sources
            if ctx.corpus is not None and ctx.corpus.is_approved_cause_source(sid)
        }
        self.guide_texts = guide_texts(lp)
        self.pre_all = list(pre)
        self.pre: list[Claim] = []
        self.pre_texts = {c.text for c in pre}
        #: 코드가 그린 앞 문장(행동 결과)의 글자: 검사의 코드 문장 모음에 넣는다
        self.pre_code = {c.text for c in pre if c.kind == "ACTION_RESULT"}
        #: 앞 응답(승인 대기 전)에 이미 낸 일반 조언 수 — 한 답의 상한은 실행 전체에서 센다
        self.prior_general = sum(1 for d in st.emitted if d.get("kind") == "GENERAL")
        #: 이 실행에서 일반 조언 안내를 이미 냈는가(앞 응답). 냈으면 다시 붙이지 않는다(FE 는 일반 조언마다 라벨을 단다, L3-42)
        self.intro_said = any(
            d.get("kind") == "SYSTEM" and d.get("text") == GENERAL_INTRO for d in st.emitted
        )
        #: 마지막 `_build`에서 쓰지 않은 일반 조언 원소 수(수 초과 · 판단에 이음). finish 가 진단에 남긴다
        self.general_skipped = 0
        self.closed = False
        self.broken = False
        self.attempt = 0
        self._reset_stream()

    # ── 앞 문장 ─────────────────────────────────────────────────────
    def send_pre(self) -> None:
        """모델을 부르기 전에 정해진 코드 문장을 검사해 낸다(걸리면 내지 않는다 — 코드 문장이라 걸리지 않아야 한다)."""
        for c in self.pre_all:
            if self._check([c], set(self.pre_code)):
                continue
            self.lines.send(c, [])
            self.pre.append(c)

    # ── TextSink ────────────────────────────────────────────────────
    def start(self, attempt: int) -> None:
        with self._lock:
            if self.closed or self.sent:
                return  # 낸 문장이 있으면 LLM 쪽이 다시 보내지 않는다(committed)
            self._reset_stream()
            self.attempt = attempt

    def feed(self, attempt: int, text: str) -> None:
        with self._lock:
            if self.closed or self.broken or attempt != self.attempt:
                return
            try:
                for ev in self.tracker.feed(text):
                    self._on(ev)
                if self.tracker.duplicate_key:
                    # 같은 최상위 키가 두 번 오면 다 받은 뒤 파싱은 마지막 값을 쓴다: 그 뒤로는 흘려 내지 않는다
                    self.broken = True
                    return
                self._advance()
            except Exception as exc:  # noqa: BLE001  # 스트림 처리 오류로 답을 막지 않는다(초안을 다 받은 뒤 한꺼번에 낸다)
                self.broken = True
                self.lp.deps.log({"event": "agent_stream_error", "cause": type(exc).__name__})

    def committed(self) -> bool:
        with self._lock:
            return bool(self.sent)

    def close(self) -> None:
        """답 쓰기 호출이 끝났다: 늦게 온 조각(시간 초과 뒤 남은 전송)을 버린다."""
        with self._lock:
            self.closed = True

    # ── 스트림 처리 ──────────────────────────────────────────────────
    def _reset_stream(self) -> None:
        self.route: str | None = None
        self.fin: _Fin | None = None
        self.elems: list[dict[str, Any] | None] = []
        self.tracker = StreamTracker()
        #: 스트림에서 낸 본문 문장
        self.sent: list[Claim] = []
        self.blocked = False
        self.lp.st.drafts = list(self.orig_drafts)

    def _on(self, ev: Any) -> None:
        if isinstance(ev, ValueDone):
            if ev.key == "route" and self.route is None:
                self.route = ev.value
        elif isinstance(ev, AssessmentsDone):
            if self.route in STREAM_ROUTES:
                self._finalize(ev.items)
        elif isinstance(ev, ClaimClosed):
            self.elems.append(ev.item)

    def _advance(self) -> None:
        if self.fin is None or self.route not in STREAM_ROUTES or self.blocked:
            return
        out, _flags, code = self._build(closed=False)
        while len(self.sent) < len(out):
            n = len(self.sent)
            c = out[n]
            # 일반 조언 안내는 바로 뒤 첫 일반 조언이 검사를 통과할 때 함께 낸다(안내만 나가고 일반 조언이 모두 빠지지 않게).
            # `_build`는 안내를 언제나 일반 조언 바로 앞에 둔다
            batch = out[n : n + 2] if is_general_intro(c) else [c]
            if len(batch) < 2 and is_general_intro(c):
                return  # 뒤 일반 조언을 기다린다
            if self._check(batch, code):
                self.blocked = True  # 걸린 문장과 그 뒤는 초안이 끝난 뒤 고쳐 원래 순서로 낸다
                return
            for b in batch:
                self.lines.send(b, self._confirmed())
                self.sent.append(b)

    def _confirmed(self) -> list[dict[str, Any]]:
        if self.fin is None:
            return []
        return [a.model_dump(mode="json") for a in self.fin.all_assessments]

    # ── 판단 확정 · 문장 만들기 ──────────────────────────────────────
    def _finalize(self, items: list[Any] | None) -> None:
        """판단 확정(답 단계의 초안이 있으면 그것, 없으면 계획 단계의 초안). 이미 내보낸 판단은 다시 확정하지 않는다."""
        if self.fin is not None:
            return
        lp = self.lp
        answer_drafts = [d for d in items or [] if isinstance(d, dict)]
        if answer_drafts and not lp.st.exported:
            lp.st.drafts = answer_drafts
        result = (
            self._finalize_fn(self.exported if self.refinal else None)
            if lp.original and (not self.exported or self.refinal)
            else None
        )
        outcomes = result.outcomes if result is not None else []
        all_as = self.exported + [o.assessment for o in outcomes]
        fin = _Fin(result, outcomes, all_as, {a.assessment_id: a for a in all_as})
        for o in outcomes:
            aid = o.assessment.assessment_id
            fin.first[aid] = o.sentence
            head = assessment_head(o)
            if head:
                fin.heads[aid] = head
        self.fin = fin

    def _build(self, *, closed: bool) -> tuple[list[Claim], list[bool], set[str]]:
        """지금까지 닫힌 초안으로 위치 · 글자가 확정된 본문 문장(최종 답 순서), 문장마다 모델 글이 든가(고칠 수 있는가),
        코드 문장 글자 모음. 초안이 더 와도 앞부분은 바뀌지 않는다(`closed`면 초안 전체)."""
        fin = self.fin
        assert fin is not None
        ctx = self.lp.ctx
        out: list[Claim] = []
        flags: list[bool] = []
        code = set(self.pre_code) | set(fin.heads.values())

        def add(c: Claim | None, by_model: bool) -> None:
            if c is not None:
                out.append(c)
                flags.append(by_model)
                if not by_model:
                    code.add(c.text)

        elems = [c for c in self.elems if isinstance(c, dict)]
        lead = 0
        while lead < len(elems) and elems[lead].get("kind") == "ASSESSMENT":
            lead += 1
        # 판단 설명은 맨 앞에 이어 온 ASSESSMENT 원소만. 다른 종류가 나왔거나 초안이 끝났으면 설명이 확정됐다
        lead_closed = closed or lead < len(elems)
        expl: dict[int, str] = {}
        #: 판단 설명이 이은 근거(이번 턴에 읽은 최신 근거만). 판단의 근거에 더해 설명 속 관측 값 검사에 쓴다(6-9)
        expl_ev: dict[int, list[str]] = {}
        reg = self.lp.ctx.registry
        for c in elems[:lead]:
            if isinstance(c.get("assessment_index"), int):
                expl.setdefault(c["assessment_index"], compose.strip_ref_ids(c.get("text") or ""))
                expl_ev.setdefault(
                    c["assessment_index"],
                    [
                        e
                        for e in c.get("evidence_ids") or []
                        if isinstance(e, str) and reg.is_current(e)
                    ],
                )
        for i, o in enumerate(fin.outcomes):
            if i not in expl and not lead_closed:
                return out, flags, code  # 이 판단의 설명을 아직 기다린다
            a = o.assessment
            head = fin.heads.get(a.assessment_id, "")
            body = expl.get(i, "")
            text = f"{head} {body}".strip() if body and len(head) + len(body) < 299 else head
            if text:
                ev_ids = list(a.evidence_ids)
                if text != head:
                    ev_ids += [e for e in expl_ev.get(i, []) if e not in ev_ids]
                c = compose._claim(
                    "ASSESSMENT",
                    text,
                    assessment_id=a.assessment_id,
                    evidence_ids=ev_ids,
                    source_ids=list(a.source_ids),
                )
                add(c, text != head)
        if not lead_closed:
            return out, flags, code
        if fin.result is not None:
            for s in [*fin.result.uncovered_sentences, *fin.result.dropped_notice]:
                add(compose._claim("SYSTEM", s), False)
        # 비교할 수 없다는 도구 결과의 까닭은 코드 문장으로(모델이 근거 없는 까닭 문장을 써서 빠지지 않게, 7-10)
        seen_nc: set[str] = set()
        for rec in self.lp.st.not_comparable:
            s = compose.not_comparable_sentence(rec)
            if s not in seen_nc and s not in self.prior_texts and s not in self.pre_texts:
                seen_nc.add(s)
                add(compose._claim("SYSTEM", s), False)
        in_general = False
        n_general = 0
        #: 이 답에서 더 쓸 수 있는 일반 조언 수: 한 답(실행)의 상한에서 앞 응답(승인 대기 전)에 이미 낸 수를 뺀다
        room = max(0, self.lp.cfg.max_general_claims - self.prior_general)
        self.general_skipped = 0
        for c in elems[lead:]:
            kind = c.get("kind")
            if kind == "GENERAL":
                in_general = True
            elif in_general:
                continue  # 일반 조언 묶음 뒤의 다른 종류는 버린다(안내 문장이 일반 조언 묶음 바로 앞에만 오게)
            if kind == "CONCLUSION":
                r = compose.conclusion_sentence(ctx, c.get("comparison_evidence_id") or "")
                if r is not None:
                    add(compose._claim("RECORD", r[0], evidence_ids=r[1]), False)
            elif kind in ("RECORD", "GUIDE", "GENERAL"):
                # 이미 낸 문장 · 코드 문장과의 대조는 문장 글의 근거 · 자료 id 를 지운 뒤에 한다(id 만 붙여 되풀이하지 않게)
                text = compose.strip_ref_ids(c.get("text") or "")
                if not text:
                    continue  # 빈 원소는 문장이 아니다(일반 조언 수 · 진단에도 세지 않는다)
                if text in code or text in self.prior_texts or text in self.pre_texts:
                    continue  # 코드 문장 · 이미 낸 문장과 글자만 같은 모델 문장은 버린다(검사 면제를 흉내 내지 못하게 · 되풀이 없이)
                if kind == "GENERAL" and (
                    n_general >= room
                    or c.get("assessment_index") is not None
                    or c.get("comparison_evidence_id")
                ):
                    # 수를 넘긴 일반 조언, 판단 · 비교 결론에 이은 일반 조언(근거가 있으면 일반 조언이 아니다)은 쓰지 않는다
                    # (6-10). 버린 수는 finish 가 진단에 남긴다
                    self.general_skipped += 1
                    continue
                q = c.get("quote")
                claim = compose._claim(
                    kind,
                    text,
                    # 인용 문장은 GUIDE 만 싣는다(내부용, 응답 Claim 에는 나가지 않는다). 없으면 검사가 걸러 낸다
                    quote=(q if isinstance(q, str) else "") if kind == "GUIDE" else None,
                    evidence_ids=list(c.get("evidence_ids") or []),
                    source_ids=list(c.get("source_ids") or []),
                )
                if kind == "GENERAL" and claim is not None:
                    if n_general == 0 and not self.intro_said:
                        # 첫 일반 조언 바로 앞에 고정 안내(6-10). 스트림에서는 뒤 일반 조언이 통과할 때 함께 낸다(`_advance`)
                        add(compose._claim("SYSTEM", GENERAL_INTRO), False)
                    n_general += 1
                add(claim, True)
        return out, flags, code

    def _check(self, claims: list[Claim], code: set[str]) -> list[chk.CheckFailure]:
        lp, fin = self.lp, self.fin
        return chk.check_claims(
            claims,
            registry=lp.ctx.registry,
            assessments=fin.amap if fin is not None else {},
            first_sentences=fin.first if fin is not None else {},
            guide_texts=self.guide_texts,
            succeeded_actions=self.succeeded,
            approved_cause_sources=self.approved,
            code_sentences=code,
            guide_searched=lp.st.guide_searched,
            cfg=lp.cfg,
        )

    # ── 초안을 다 받은 뒤 ────────────────────────────────────────────
    def finish(self, draft: dict[str, Any], messages: list[dict[str, Any]]) -> Composed:
        """초안 전체로 문장을 다시 만들고, 이미 낸 문장은 그대로 둔 채 나머지만 검사 → 한 번 고치기 → 다시 검사한다(6-9).
        `draft`가 비면(모델 실패) 코드가 확정한 문장만 만든다."""
        self.close()
        self._finalize(draft.get("assessments"))
        fin = self.fin
        assert fin is not None
        self.elems = [c for c in draft.get("claims") or [] if isinstance(c, dict)]
        out, flags, code = self._build(closed=True)
        n = len(self.sent)
        if [_key(c) for c in out[:n]] == [_key(c) for c in self.sent]:
            rest, rflags = out[n:], flags[n:]
        else:
            # 스트림에서 읽은 초안과 다 받은 초안이 다르다(낸 뒤 실패해 빈 초안 등): 낸 문장은 그대로 두고 나머지만 본다.
            # 이미 낸 판단 문장의 판단은 다시 그리지 않는다(설명 없이 다시 만든 첫 문장이 한 번 더 나가지 않게)
            left = Counter(_key(c) for c in self.sent)
            said_as = {c.assessment_id for c in self.sent if c.kind == "ASSESSMENT"}
            rest, rflags = [], []
            for c, f in zip(out, flags, strict=True):
                k = _key(c)
                if left[k] > 0:
                    left[k] -= 1
                    continue
                if c.kind == "ASSESSMENT" and c.assessment_id in said_as:
                    continue
                rest.append(c)
                rflags.append(f)
        resolved = _drop_lone_intro(self._check_and_fix(rest, rflags, code, messages, offset=n))
        self._note_general_over()
        return Composed(
            out=out,
            pre=list(self.pre),
            body=[*self.sent, *resolved],
            all_assessments=fin.all_assessments,
            amap=fin.amap,
        )

    def _note_general_over(self) -> None:
        """마지막 `_build`(초안 전체)가 쓰지 않은 일반 조언 원소(수 초과 · 판단 · 비교에 이음)를 진단에 남긴다(원소마다
        `GENERAL_RULE` DROPPED, 6-10). 글이 비었거나 이미 낸 문장과 같아 건너뛴 원소는 세지 않는다."""
        for _ in range(self.general_skipped):
            self.lp.diag.step(
                "CHECK",
                chk.CheckId.GENERAL_RULE,
                {"claim_kind": "GENERAL", "round": 0, "result": "DROPPED"},
                parent_seq=self.lp._last_model_seq,
            )

    def _check_and_fix(
        self,
        rest: list[Claim],
        flags: list[bool],
        code: set[str],
        messages: list[dict[str, Any]],
        *,
        offset: int,
    ) -> list[Claim]:
        lp, fin = self.lp, self.fin
        assert fin is not None

        def recheck(cl: list[Claim]) -> list[chk.CheckFailure]:
            return self._check(cl, code)

        def pos(i: int | None) -> int | None:
            # 진단 기록의 문장 번호는 이 답의 본문 전체(이미 낸 문장 포함) 기준
            return None if i is None else offset + i

        checked = len(self.pre_all) + offset + len(rest)
        pre_dropped = len(self.pre_all) - len(self.pre)
        failures = recheck(rest)
        answer_seq = lp._last_model_seq
        # 범위 밖 낱말(SCOPE_RULE)이 걸린 문장은 고치기에 보내지 않고 바로 버린다(6-9). 한 문장에 다른 검사도 걸렸어도 같다.
        # 글만 고쳐서는 풀리지 않는 것(자료를 찾지 않은 일반 조언 등)도 같다
        drop_now = {
            f.claim_index for f in failures if f.check_id == chk.CheckId.SCOPE_RULE or f.unfixable
        }
        check_seqs: list[int] = []
        for f in failures:
            detail = {
                "claim_index": pos(f.claim_index),
                "claim_kind": f.claim_kind,
                "round": 0,
                "result": "DROPPED" if f.claim_index in drop_now else "FIX_REQUESTED",
            }
            check_seqs.append(lp.diag.step("CHECK", f.check_id, detail, parent_seq=answer_seq))
        if not failures:
            lp.diag.step(
                "CHECK", "SUMMARY", chk.summary_counts([], 0, checked=checked, dropped=pre_dropped)
            )
            return list(rest)
        fixable = [
            f
            for f in failures
            if f.claim_index is not None and flags[f.claim_index] and f.claim_index not in drop_now
        ]
        asked = {f.claim_index for f in fixable}
        fixed_texts: dict[int, str | None] | None = None
        if fixable:
            # 고치기 호출의 문장 번호는 이 답의 본문 전체(이미 낸 문장 포함) 기준이다. 낸 문장은 고치라고 하지 않는다
            texts = [
                {
                    "text": c.text,
                    "kind": c.kind,
                    "evidence_ids": list(c.evidence_ids),
                    "source_ids": list(c.source_ids),
                }
                for c in [*self.sent, *rest]
            ]
            shifted = [dataclasses.replace(f, claim_index=pos(f.claim_index)) for f in fixable]
            got = lp.fix_call(messages, texts, shifted, fix_for=check_seqs)
            if got is not None:
                fixed_texts = {k - offset: v for k, v in got.items() if k >= offset}
        fixed: list[Claim] | None = None
        #: 고친 목록의 각 문장이 원래 몇 번째 문장이었는지(남은 문장을 원래 자리에 두고, 진단 번호를 맞추려고)
        origin: list[int] = []
        if fixed_texts is not None:
            fixed = []
            for i, c in enumerate(rest):
                if i not in fixed_texts or i not in asked:
                    # 고치라고 하지 않은 문장(코드 문장 포함)은 고치기 결과로 바꾸거나 지우지 않는다
                    fixed.append(c)
                    origin.append(i)
                    continue
                t = fixed_texts[i]
                if t is None:
                    continue
                head = fin.first.get(c.assessment_id or "")
                if c.kind == "ASSESSMENT" and head:
                    # 판단 첫 문장(템플릿)은 고치는 단계도 바꾸지 못한다. 모델이 쓴 뒷부분만 근거 · 자료 id 를 지운다
                    body = t[len(head) :] if t.startswith(head) else t
                    t = f"{head} {compose.strip_ref_ids(body)}".strip()
                else:
                    t = compose.strip_ref_ids(t)
                nc = compose._claim(
                    c.kind,
                    t,
                    quote=compose.claim_quote(c),
                    assessment_id=c.assessment_id,
                    evidence_ids=list(c.evidence_ids),
                    source_ids=list(c.source_ids),
                    action_id=c.action_id,
                )
                if nc is not None:
                    fixed.append(nc)
                    origin.append(i)
        outcome = chk.apply_fix_round(rest, fixed, failures, recheck=recheck)
        if outcome.used_fixed and fixed is not None:
            bad2 = chk.failing_indices(outcome.remaining_failures)
            kept = {origin[j]: c for j, c in enumerate(fixed) if j not in bad2}
        else:
            bad = chk.failing_indices(failures)
            kept = {i: c for i, c in enumerate(rest) if i not in bad}
        resolved: list[Claim] = []
        for i, c in enumerate(rest):
            if i in kept:
                resolved.append(kept[i])
                continue
            head = fin.heads.get(c.assessment_id or "")
            if c.kind != "ASSESSMENT" or not head:
                continue
            hc = compose._claim(
                "ASSESSMENT",
                head,
                assessment_id=c.assessment_id,
                evidence_ids=list(c.evidence_ids),
                source_ids=list(c.source_ids),
            )
            if hc is not None and not recheck([hc]):
                resolved.append(
                    hc
                )  # 모델 설명만 걸렸으면 코드가 확정한 판단 문장은 그 자리에 남긴다(6-3 · L1-38)
        fix_seq = lp._last_model_seq if fixed is not None else answer_seq
        for f in outcome.remaining_failures:
            # 다시 검사한 번호는 고친 목록 기준이라 원래 번호로 바꿔 처음 검사와 맞춘다
            oi = (
                origin[f.claim_index]
                if f.claim_index is not None and f.claim_index < len(origin)
                else f.claim_index
            )
            if oi in drop_now:
                continue  # 이미 처음 검사에서 DROPPED 로 남겼다
            detail = {
                "claim_index": pos(oi),
                "claim_kind": f.claim_kind,
                "round": 1,
                "result": "DROPPED",
            }
            lp.diag.step("CHECK", f.check_id, detail, parent_seq=fix_seq)
        lp.diag.step(
            "CHECK",
            "SUMMARY",
            chk.summary_counts(
                failures, 1, checked=checked, dropped=pre_dropped + len(rest) - len(resolved)
            ),
        )
        return resolved
