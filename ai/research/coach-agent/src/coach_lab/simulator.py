"""가상 발표 시뮬레이터 — 시나리오 JSON 을 1초 단위 CoachRequest 로 바꿔 코치에 흘려보낸다.

BE 가 할 일을 그대로 흉내 냅니다: 최근 15초 STT 단어를 모으고, coach_state 를 받아 두었다가
다음 요청에 붙이고, 응답의 이벤트를 쌓고, 끝나면 finalize.

발표자는 코치의 말에 반응할 수 있습니다(scenario.reactions). 반응이 있으면 개입 효과가
EFFECTIVE 로, 없으면 INEFFECTIVE 로 나와 되돌아보기(사다리 · 포기)를 재생할 수 있습니다.

실험(coach_lab/evaluate.py)을 위해 두 가지를 더 합니다.

- **측정 잡음** (Noise) — 시선 라벨 흔들림, UNCERTAIN 섞임, 말 속도 흔들림, 음량 흔들림,
  STT 단어 누락, 확정 지연 흔들림. seed 로 재현됩니다
- **정답 기록** — 매 초 발표자가 '실제로' 어땠는지(잡음 전의 상태), 장 방문, 코치 말에 반응했는지

코치에는 FE · BE 가 가공하지 않은 원자료를 보냅니다: 시선 1초 기록, 음량 1초 기록(dBFS 레벨),
STT 확정 단어, 문장 끝 시각. 잡음이 없을 때도 시선 라벨은 비율대로 고르게 섞이므로(저불일치 수열)
창 안 비율이 실제 비율 근처에서 조금 흔들립니다.

실제 데이터가 아닙니다. 기준값이 '맞는지'가 아니라 로직이 '의도대로 도는지'를 봅니다.
"""

from __future__ import annotations

import json
import random
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from coach import decide, finalize
from coach.config import CoachConfig, load_config
from coach.judges import Judges
from coach.schemas import CoachResponse, TakeResult

from .judges import lab_judges

_SYLLABLE_WORD = "가나다"  # 3글자 단어. 내용은 상관없고 글자 수만 쓴다
_FILLER = "음"
_WORD_GAP_MS = 120
_SENTENCE_WORDS = 6
_SENTENCE_GAP_MS = 600
_FINAL_LAG_MS = 1_200  # Deepgram 확정 결과가 늦게 오는 만큼
_FILLER_MS = 350
_INPUT_WINDOW_MS = 30_000  # 시선 · 음량 1초 기록을 싣는 창 (코치의 입력 창과 같다)
_WORDS_WINDOW_MS = 60_000  # 확정 단어 · 문장 끝 신호를 싣는 창
#: 가상 발표자의 평소 목소리 레벨 (A 가중 dBFS).
#: 코치는 이 값을 모르고 첫 발화로 잡는다
_VOICE_LEVEL_DBFS = -24.0
_PHI = 0.6180339887498949  # 저불일치 수열 — 잡음 없이도 라벨이 비율대로 고르게 섞인다
_PSI = 0.7548776662466927


class Params(BaseModel):
    """한 순간의 발표자 상태 (잡음 전, 즉 '정답')."""

    model_config = ConfigDict(extra="forbid")

    cpm: float = 300.0
    #: 보인 시간 중 대본을 보는 비율
    script_ratio: float = 0.2
    uncertain_ratio: float = 0.03
    relative_db: float = 0.0
    filler_per_min: float = 2.0
    speaking: bool = True
    stt_status: str = "ok"
    audio_live: bool = True
    #: 대본의 몇 %를 말하면 다음 장으로 넘어가나. CONDENSE 에 반응하면 줄어든다
    completion: float = 1.0


class Noise(BaseModel):
    """측정 잡음. 기본은 없음."""

    model_config = ConfigDict(extra="forbid")

    name: str = "clean"
    #: 매초 시선 라벨이 CAMERA ↔ BOTTOM 으로 뒤집힐 확률
    gaze_flip: float = 0.0
    #: 매초 UNCERTAIN 이 끼어들 추가 확률 (얼굴 놓침)
    uncertain_burst: float = 0.0
    #: 단어마다 말 속도가 ± 이 비율 안에서 흔들림
    cpm_jitter: float = 0.0
    #: 매초 음량 측정에 더해지는 가우시안 잡음 σ (dB)
    db_sigma: float = 0.0
    #: STT 가 단어를 놓칠 확률
    word_drop: float = 0.0
    #: 확정 결과가 늦게 오는 시간에 더해지는 0 ~ 이 값 (ms)
    final_lag_jitter_ms: int = 0
    #: True 면 잡음이 없어도 라벨을 난수로 고른다 (False 면 저불일치 수열)
    random_labels: bool = False


NOISE_PRESETS: dict[str, Noise] = {
    "clean": Noise(),
    "noisy": Noise(
        name="noisy",
        gaze_flip=0.08,
        uncertain_burst=0.04,
        cpm_jitter=0.12,
        db_sigma=1.5,
        word_drop=0.03,
        final_lag_jitter_ms=800,
        random_labels=True,
    ),
    "harsh": Noise(
        name="harsh",
        gaze_flip=0.15,
        uncertain_burst=0.10,
        cpm_jitter=0.20,
        db_sigma=3.0,
        word_drop=0.08,
        final_lag_jitter_ms=1_500,
        random_labels=True,
    ),
}


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_ms: int
    to_ms: int
    set: dict[str, Any]


class Reaction(BaseModel):
    """코치가 이 instruction 을 말하면 발표자가 이렇게 바뀐다."""

    model_config = ConfigDict(extra="forbid")

    delay_ms: int = 2_000
    #: None 이면 Take 끝까지
    duration_ms: int | None = 30_000
    set: dict[str, Any] = Field(default_factory=dict)
    advance_slide: bool = False


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    take_id: str = "sim-take"
    mode: str = "COACHING"
    duration_ms: int
    tick_ms: int = 1_000
    #: 마지막 장을 다 말하면 끝낸다
    end_on_finish: bool = True
    plan: dict[str, Any]
    missions: list[dict[str, Any]] = Field(default_factory=list)
    memory: dict[str, Any] = Field(default_factory=dict)
    baseline: dict[str, Any] = Field(default_factory=dict)
    segments: list[Segment] = Field(default_factory=list)
    reactions: dict[str, Reaction] = Field(default_factory=dict)
    #: 코치 설정 덮어쓰기 (load_config 와 같은 모양)
    config: dict[str, Any] = Field(default_factory=dict)
    noise: Noise = Field(default_factory=Noise)
    seed: int = 0
    expect: dict[str, Any] = Field(default_factory=dict)
    #: 코칭 계획 실험용 (scenarios/plan/): 장별 대본, 직전 리뷰 근거, 계획에 대한 기대
    scripts: list[dict[str, Any]] = Field(default_factory=list)
    previous_review: dict[str, Any] | None = None
    plan_expect: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> Scenario:
        return cls.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


@dataclass
class _Word:
    w: str
    start_ms: int
    end_ms: int
    filler: bool
    #: 오디오가 살아 있었고 STT 가 놓치지 않아 전달됐나. 발표자는 상관없이 말한다
    heard: bool
    slide: int | None
    final_at_ms: int
    #: 마이크(오디오)가 살아 있어 FE 가 소리를 들었나
    mic: bool = True


@dataclass
class _ActiveReaction:
    start_ms: int
    end_ms: float
    values: dict[str, Any]


class Presenter:
    """시나리오대로 말하고, 코치의 말에 반응하는 가상 발표자."""

    def __init__(
        self,
        sc: Scenario,
        noise: Noise | None = None,
        seed: int | None = None,
        coaching_plan: dict[str, Any] | None = None,
    ) -> None:
        self.sc = sc
        self.coaching_plan = coaching_plan
        self.noise = noise or sc.noise
        self.rng = random.Random(sc.seed if seed is None else seed)
        self.base = Params.model_validate(sc.baseline)
        self.slides = sorted(sc.plan.get("slides", []), key=lambda s: s["slide_number"])
        self.slide_idx = 0
        self.slide_start_ms = 0
        self.slide_chars = 0
        self.finished_at: int | None = None
        self.words: list[_Word] = []
        self.heard_words: list[_Word] = []
        self.cursor = 0
        self.sentence_words = 0
        self.filler_acc = 0.0
        #: 지금까지 나온 문장 끝 시각
        self.utterance_ends: list[int] = []
        self.reactions: list[_ActiveReaction] = []
        self.pending_advance: list[int] = []
        #: 시선 · 음량 1초 기록 (최근 창만)
        window = max(1, _INPUT_WINDOW_MS // sc.tick_ms)
        self.gaze_records: deque[dict[str, Any]] = deque(maxlen=window)
        self.voice_records: deque[dict[str, Any]] = deque(maxlen=window)
        self.label_k = 0
        #: 정답 기록
        self.truth: list[dict[str, Any]] = []
        self.visits: list[dict[str, Any]] = [{"slide": self.slide_number, "start_ms": 0}]
        #: Take 전체 원자료 (BE 가 보관해 replay 로 싣는 것): 1초 기록 전부와 STT 상태가 바뀐 시각
        self.all_gaze_records: list[dict[str, Any]] = []
        self.all_voice_records: list[dict[str, Any]] = []
        self.stt_changes: list[dict[str, Any]] = []
        self.reaction_log: list[dict[str, Any]] = []

    # ── 상태 ────────────────────────────────────────────────────────────

    def params_at(self, t: int) -> Params:
        values = self.base.model_dump()
        for seg in self.sc.segments:
            if seg.from_ms <= t < seg.to_ms:
                values.update(seg.set)
        for r in self.reactions:
            if r.start_ms <= t < r.end_ms:
                values.update(r.values)
        return Params.model_validate(values)

    @property
    def slide_number(self) -> int | None:
        return self.slides[self.slide_idx]["slide_number"] if self.slides else None

    def react(self, instruction: str, t: int) -> None:
        reaction = self.sc.reactions.get(instruction)
        self.reaction_log.append(
            {"t_ms": t, "instruction": instruction, "reacted": reaction is not None}
        )
        if reaction is None:
            return
        start = t + reaction.delay_ms
        end = float("inf") if reaction.duration_ms is None else start + reaction.duration_ms
        if reaction.set:
            self.reactions.append(_ActiveReaction(start, end, dict(reaction.set)))
        if reaction.advance_slide:
            self.pending_advance.append(start)

    # ── 말하기 ──────────────────────────────────────────────────────────

    def _advance(self, at_ms: int) -> None:
        self.visits[-1]["end_ms"] = at_ms
        if self.slide_idx + 1 < len(self.slides):
            self.slide_idx += 1
            self.slide_start_ms = at_ms
            self.slide_chars = 0
            self.visits.append({"slide": self.slide_number, "start_ms": at_ms})
        elif self.finished_at is None:
            self.finished_at = at_ms

    def speak_until(self, t: int) -> None:
        nz = self.noise
        while self.cursor < t:
            if self.pending_advance and self.pending_advance[0] <= self.cursor:
                self.pending_advance.pop(0)
                self._advance(self.cursor)
            if self.finished_at is not None:
                self.cursor = t
                break
            p = self.params_at(self.cursor)
            if not p.speaking:
                self.cursor = min(t, self.cursor + 100)
                continue

            text, filler = _SYLLABLE_WORD, False
            if self.filler_acc >= 1.0:
                text, filler = _FILLER, True
            cpm = p.cpm
            if nz.cpm_jitter:
                cpm *= 1 + self.rng.uniform(-nz.cpm_jitter, nz.cpm_jitter)
            dur = _FILLER_MS if filler else round(len(text) * 60_000 / max(1.0, cpm))
            if self.cursor + dur > t:
                break  # 이번 1초 안에 다 말하지 못하는 단어는 다음 틱에 말한다

            dropped = nz.word_drop > 0 and self.rng.random() < nz.word_drop
            lag = _FINAL_LAG_MS + (
                self.rng.randint(0, nz.final_lag_jitter_ms) if nz.final_lag_jitter_ms else 0
            )
            end = self.cursor + dur
            word = _Word(
                text,
                self.cursor,
                end,
                filler,
                p.audio_live and not dropped,
                self.slide_number,
                end + lag,
                p.audio_live,
            )
            self.words.append(word)
            if word.heard:
                self.heard_words.append(word)
            self.cursor = end
            if filler:
                self.filler_acc -= 1.0
                self.cursor += _WORD_GAP_MS
                continue

            self.filler_acc += p.filler_per_min * (dur + _WORD_GAP_MS) / 60_000
            self.sentence_words += 1
            if self.sentence_words % _SENTENCE_WORDS == 0:
                self.utterance_ends.append(self.cursor)
                gap = _SENTENCE_GAP_MS
            else:
                gap = _WORD_GAP_MS

            self.slide_chars += len(text)
            if self.slides:
                need = self.slides[self.slide_idx].get("script_chars", 0) * p.completion
                if self.slide_chars >= need:
                    self._advance(self.cursor)
            self.cursor += gap

    # ── 시선 (FE 흉내) ──────────────────────────────────────────────────

    def _gaze_label(self, p: Params) -> str:
        nz = self.noise
        self.label_k += 1
        if nz.random_labels:
            u, v = self.rng.random(), self.rng.random()
        else:
            u, v = (self.label_k * _PHI) % 1.0, (self.label_k * _PSI + 0.5) % 1.0
        if v < p.uncertain_ratio or (nz.uncertain_burst and self.rng.random() < nz.uncertain_burst):
            return "UNCERTAIN"
        label = "BOTTOM" if u < p.script_ratio else "CAMERA"
        if nz.gaze_flip and self.rng.random() < nz.gaze_flip:
            label = "CAMERA" if label == "BOTTOM" else "BOTTOM"
        return label

    # ── 요청 ────────────────────────────────────────────────────────────

    def request(self, t: int, coach_state: dict[str, Any] | None) -> dict[str, Any]:
        self.speak_until(t)
        p = self.params_at(t)
        # 확정된 단어만 보낸다 (확정은 1~3초 늦게 온다)
        words: list[dict[str, Any]] = []
        for w in reversed(self.heard_words):
            if w.end_ms <= t - _WORDS_WINDOW_MS:
                break
            if w.end_ms <= t and w.final_at_ms <= t:
                words.append({"word": w.w, "start_ms": w.start_ms, "end_ms": w.end_ms})
        words.reverse()
        # FE 의 침묵은 소리 에너지로 잰다 — 단어를 말하는 도중이면 0 이고, STT 가 단어를 놓쳐도
        # 상관없다.
        # 오디오가 멈추면 소리가 안 들어오니 침묵이 계속 늘어난다 (FE 코치 주석의 실제 동작)
        last_end = next((w.end_ms for w in reversed(self.words) if w.mic), 0)
        mid_word = p.audio_live and p.speaking and self.finished_at is None and self.cursor <= t
        silence = 0 if mid_word else max(0, t - last_end)

        label = self._gaze_label(p)
        db = p.relative_db
        if self.noise.db_sigma:
            db += self.rng.gauss(0.0, self.noise.db_sigma)
        # 이 틱의 기록은 지난 1초(t − tick ~ t)의 판정이다. Take 시작 순간(t = 0)에는 없다
        if t >= self.sc.tick_ms:
            tick = self.sc.tick_ms
            self.gaze_records.append({"t_ms": t - tick, "duration_ms": tick, "state": label})
            self.all_gaze_records.append(self.gaze_records[-1])
            # FE 는 그 1초 동안 소리를 낸 시간을 잰다 — 마이크가 들은 단어가 그 1초와 겹친 만큼.
            # 1초 끝에 말하던 단어(다음 틱에 마저 말한다)도 넣는다
            voiced_ms = self._voiced_ms(t - tick, t, self.cursor if mid_word else None)
            self.voice_records.append(
                {
                    "t_ms": t - tick,
                    "duration_ms": tick,
                    "level_db": round(_VOICE_LEVEL_DBFS + db, 2) if voiced_ms else None,
                    "voiced_ms": voiced_ms,
                    "silence_ms": silence,
                    "audio_live": p.audio_live,
                }
            )
            self.all_voice_records.append(self.voice_records[-1])
        if not self.stt_changes or self.stt_changes[-1]["status"] != p.stt_status:
            self.stt_changes.append({"t_ms": t, "status": p.stt_status})

        speaking_now = p.speaking and self.finished_at is None
        self.truth.append(
            {
                "t_ms": t,
                "slide": self.slide_number,
                "cpm": p.cpm,
                "script_ratio": p.script_ratio,
                "uncertain_ratio": p.uncertain_ratio,
                "voice_diff_db": p.relative_db,
                "filler_per_min": p.filler_per_min,
                "speaking": speaking_now,
                "audio_live": p.audio_live,
                "stt_ok": p.stt_status == "ok" and p.audio_live,
                # 센서가 실제로 볼 수 있었나 — 정답 구간 중 코치가 잡을 수 있던 것만 재현율에 넣는다
                "gaze_ok": p.uncertain_ratio + self.noise.uncertain_burst <= 0.5,
            }
        )

        return {
            "take_id": self.sc.take_id,
            "t_ms": t,
            "mode": self.sc.mode,
            "plan": self.sc.plan,
            "missions": self.sc.missions,
            "recurring_issues": self.sc.memory.get("recurring_issues", []),
            "coaching_plan": self.coaching_plan,
            "inputs": {
                "gaze_records": list(self.gaze_records),
                "voice_records": list(self.voice_records),
                "words": words,
                "utterance_ends": [
                    u for u in self.utterance_ends if t - _WORDS_WINDOW_MS <= u <= t
                ],
                "stt_status": p.stt_status,
                "slide": (
                    {"number": self.slide_number, "started_ms": self.slide_start_ms}
                    if self.slide_number is not None
                    else None
                ),
            },
            # 기준 음량은 코치가 첫 발화로 잡는다
            "calibration": {"base_level_db": None},
            "coach_state": coach_state,
        }

    def replay_payload(self) -> dict[str, Any]:
        """Take 전체 원자료 — finalize 의 replay. 단어는 확정된 시각과 함께 싣는다."""
        return {
            "gaze_records": list(self.all_gaze_records),
            "voice_records": list(self.all_voice_records),
            "stt_status_changes": list(self.stt_changes),
            "words": [
                {
                    "word": w.w,
                    "start_ms": w.start_ms,
                    "end_ms": w.end_ms,
                    "final_at_ms": w.final_at_ms,
                }
                for w in self.heard_words
            ],
            "utterance_ends": list(self.utterance_ends),
            "slides": [
                {"number": v["slide"], "started_ms": v["start_ms"]}
                for v in self.visits
                if v["slide"] is not None
            ],
        }

    def _voiced_ms(self, lo: int, hi: int, speaking_from: int | None) -> int:
        """[lo, hi) 동안 마이크가 들은 말의 시간. 단어끼리 겹치지 않는다.

        speaking_from: hi 에 말하던 단어가 시작된 시각(다 말한 단어 목록에는 아직 없다). 이 단어는
        다음 틱에 길이를 다시 정해 말하므로, 1초 경계에 걸친 몫은 근사다(말 속도가 흔들리는 잡음,
        1초 안에서 마이크 · 말하기 상태가 바뀐 경우). 단어 생성을 바꾸면 말과 함께 쓰는 잡음
        난수 순서가 바뀌어 시선 잡음까지 달라지므로 그대로 둔다.
        """
        total = 0
        for w in reversed(self.words):
            if w.end_ms <= lo:
                break
            if w.mic:
                total += max(0, min(w.end_ms, hi) - max(w.start_ms, lo))
        if speaking_from is not None:
            total += max(0, hi - max(speaking_from, lo))
        return min(total, hi - lo)

    def close(self, t: int) -> None:
        self.visits[-1].setdefault("end_ms", t)
        if self.finished_at is None:
            self.finished_at = t


@dataclass
class RunResult:
    scenario: Scenario
    noise: Noise
    seed: int
    config: CoachConfig
    presenter: Presenter
    timeline: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    latencies_ms: list[float] = field(default_factory=list)
    ticks: int = 0
    end_ms: int = 0
    final_state_bytes: int = 0
    #: finalize 가 합계로 만든 Take 결과
    take_result: TakeResult | None = None
    #: finalize 에 넣은 요청. 설정만 바꿔 Take 결과를 다시 만들 때 쓴다(실험)
    final_request: dict[str, Any] | None = None

    @property
    def interventions(self) -> list[dict[str, Any]]:
        return [e for e in self.events if e["kind"] == "INTERVENTION"]

    def stats(self) -> dict[str, Any]:
        lat = sorted(self.latencies_ms)
        p95 = lat[min(len(lat) - 1, round(0.95 * (len(lat) - 1)))] if lat else None
        return {
            "ticks": self.ticks,
            "end_ms": self.end_ms,
            "interventions": len(self.interventions),
            "decide_p50_ms": round(median(lat), 3) if lat else None,
            "decide_p95_ms": round(p95, 3) if p95 is not None else None,
            "coach_state_bytes": self.final_state_bytes,
        }


#: /coach/evaluate 요청에서 Take 동안 바뀌지 않는 값 (finalize 요청에도 같이 싣는다)
_TAKE_CONSTANTS = (
    "take_id",
    "mode",
    "plan",
    "missions",
    "recurring_issues",
    "coaching_plan",
    "script_used",
    "calibration",
)


def scenario_config(sc: Scenario, override: dict[str, Any] | None = None) -> CoachConfig:
    """시나리오 설정 위에 실험 변형을 덮는다."""
    from coach.config import _deep_merge  # noqa: PLC0415 — 도구 전용

    return load_config(**_deep_merge(sc.config, override or {}))


def run(
    sc: Scenario,
    config: CoachConfig | None = None,
    *,
    noise: Noise | None = None,
    seed: int | None = None,
    judges: Judges | None = None,
    coaching_plan: dict[str, Any] | None = None,
) -> RunResult:
    """시나리오 하나를 재생한다.

    coaching_plan 을 주면 매 요청에 그 계획을 싣는다(BE 가 저장해 둔 코칭 계획). Take 첫 요청의
    coach_state 는 늘 null 이다.
    """
    cfg = config if config is not None else scenario_config(sc)
    presenter = Presenter(sc, noise, seed, coaching_plan)
    judges = judges or lab_judges()
    result = RunResult(
        scenario=sc,
        noise=presenter.noise,
        seed=sc.seed if seed is None else seed,
        config=cfg,
        presenter=presenter,
    )
    state: dict[str, Any] | None = None
    last_req: dict[str, Any] = {}

    t = 0
    while t <= sc.duration_ms:
        req = presenter.request(t, state)
        last_req = req
        started = time.perf_counter()
        resp: CoachResponse = decide(req, judges, cfg)
        result.latencies_ms.append((time.perf_counter() - started) * 1000)
        state = resp.coach_state
        result.events.extend(e.model_dump(mode="json") for e in resp.events)
        if resp.feedback is not None:
            presenter.react(resp.feedback.instruction.value, t)
        if resp.action.value != "WAIT" or resp.feedback is not None:
            result.timeline.append(
                {
                    "t_ms": t,
                    "slide": presenter.slide_number,
                    "action": resp.action.value,
                    "area": resp.feedback.area.value if resp.feedback else None,
                    "instruction": resp.feedback.instruction.value if resp.feedback else None,
                    "message": resp.feedback.message if resp.feedback else None,
                    "reason_codes": resp.reason_codes,
                }
            )
        result.ticks += 1
        result.end_ms = t
        if (
            sc.end_on_finish
            and presenter.finished_at is not None
            and t >= presenter.finished_at + 1_000
        ):
            break
        t += sc.tick_ms

    presenter.close(result.end_ms)
    # 마지막 틱이 Take 끝이라 마지막 창은 이미 처리했다(finalize 가 건너뛴다)
    result.final_request = {
        **{k: last_req[k] for k in _TAKE_CONSTANTS if k in last_req},
        "t_ms": result.end_ms,
        "events": list(result.events),
        "inputs": last_req["inputs"],
        "coach_state": state,
    }
    fin = finalize(result.final_request, judges, cfg)
    result.take_result = fin.take_result
    result.events.extend(e.model_dump(mode="json") for e in fin.events)
    result.final_state_bytes = (
        len(json.dumps(state, ensure_ascii=False).encode("utf-8")) if state else 0
    )
    return result


def check_expect(result: RunResult) -> list[str]:
    """시나리오의 expect 와 실제 결과를 비교해 어긋난 것을 문장으로 돌려준다."""
    exp = result.scenario.expect
    failures: list[str] = []
    ivs = result.interventions
    instructions = {e["instruction"] for e in ivs}
    take = result.take_result
    assert take is not None

    if "interventions" in exp:
        lo, hi = exp["interventions"].get("min"), exp["interventions"].get("max")
        if lo is not None and len(ivs) < lo:
            failures.append(f"개입 {len(ivs)}회 < 최소 {lo}")
        if hi is not None and len(ivs) > hi:
            failures.append(f"개입 {len(ivs)}회 > 최대 {hi}")
    for ftype, bound in exp.get("areas", {}).items():
        n = sum(e["area"] == ftype for e in ivs)
        if "min" in bound and n < bound["min"]:
            failures.append(f"{ftype} 개입 {n}회 < 최소 {bound['min']}")
        if "max" in bound and n > bound["max"]:
            failures.append(f"{ftype} 개입 {n}회 > 최대 {bound['max']}")
    for name in exp.get("instructions_include", []):
        if name not in instructions:
            failures.append(f"{name} 개입이 없음")
    for name in exp.get("instructions_exclude", []):
        if name in instructions:
            failures.append(f"{name} 개입이 있으면 안 됨")
    changes = {e["change"] for e in result.events if e["kind"] == "STRATEGY"}
    for name in exp.get("strategy_changes_include", []):
        if name not in changes:
            failures.append(f"전략 변경 {name} 이 없음")
    outcomes = {e["outcome"] for e in result.events if e["kind"] == "OUTCOME"}
    for name in exp.get("outcomes_include", []):
        if name not in outcomes:
            failures.append(f"효과 판정 {name} 이 없음")
    episodes = {e["issue_type"] for e in result.events if e["kind"] == "EPISODE"}
    for name in exp.get("episodes_include", []):
        if name not in episodes:
            failures.append(f"문제 구간 {name} 이 없음")
    for name in exp.get("episodes_exclude", []):
        if name in episodes:
            failures.append(f"문제 구간 {name} 이 있으면 안 됨")
    reasons = {r for e in result.events if e["kind"] == "SUPPRESSED" for r in e["reasons"]}
    for name in exp.get("suppressed_reasons_include", []):
        if name not in reasons:
            failures.append(f"참은 이유 {name} 이 없음")

    # ── Take 결과의 사실 ─────────────────────────────────────────────────
    failures.extend(_check_take_result(exp, take))
    return failures


def _compare(value: float, operator: str, target: float) -> bool:
    match operator:
        case "LT":
            return value < target
        case "LTE":
            return value <= target
        case "GT":
            return value > target
        case "GTE":
            return value >= target
    raise ValueError(f"알 수 없는 비교 연산자 {operator}")


def slide_metric(take: TakeResult, area: str, slide_number: int, metric: str) -> float | None:
    """Take 결과에서 영역 · 장의 지표 하나. 없거나 재지 못했으면 None."""
    result = take.areas.get(area)
    for s in (result.slides or []) if result else []:
        if s.slide_number == slide_number:
            return (s.metrics or {}).get(metric)
    return None


def _check_take_result(exp: dict[str, Any], take: TakeResult) -> list[str]:
    """expect 의 Take 결과 키를 확인한다. 항목의 필드는 적은 것만 맞는지 본다."""
    failures: list[str] = []
    segments = take.problem_segments
    for item in exp.get("segments_include", []):
        if not any(
            s.issue_type.value == item["issue_type"]
            and ("slide_number" not in item or s.slide_number == item["slide_number"])
            and ("coached" not in item or s.coached == item["coached"])
            and ("reliable" not in item or s.reliable == item["reliable"])
            for s in segments
        ):
            failures.append(f"문제 구간 {item} 이 없음")
    for item in exp.get("no_segments", []):
        if any(
            s.reliable
            and s.area.value == item["area"]
            and ("slide_number" not in item or s.slide_number == item["slide_number"])
            for s in segments
        ):
            failures.append(f"{item['area']} 를 문제 구간으로 말하면 안 됨 {item}")
    for item in exp.get("gave_up_include", []):
        if not any(
            g.issue_type.value == item["issue_type"]
            and ("slide_number" not in item or g.slide_number == item["slide_number"])
            for g in take.gave_up
        ):
            failures.append(f"포기한 문제 {item} 이 없음")
    for item in exp.get("slide_metrics", []):
        got = slide_metric(take, item["area"], item["slide_number"], item["metric"])
        if got is None or not _compare(got, item["operator"], item["value"]):
            failures.append(
                f"장 지표 {item['area']}/{item['slide_number']} {item['metric']}: {got}"
                f" ≠ {item['operator']} {item['value']}"
            )
    return failures
