"""말 — BE 가 보낸 최근 STT 단어로 속도(CPM) · 군더더기 · 장별 진행도 · 키워드를 본다.

CPM 공식은 stt-live v1 과 같습니다: 글자 수(공백 제외) ÷ 실제로 말한 시간(단어별
end - start 의 합, 침묵 제외) × 60초. 군더더기는 글자 수와 말한 시간에서 모두 뺍니다.

확정 단어(final)만 누적에 씁니다. 중간 결과는 바뀔 수 있어서 CPM 에만 씁니다.
"""

from __future__ import annotations

from ..schemas import Word
from ..vocab import Issue
from .base import Detection, Tick, nonspace_len, ramp


def ingest(tick: Tick) -> None:
    """새로 확정된 단어를 coach_state 에 누적한다 — 장별 글자 수, 군더더기 수, 키워드."""
    speech = tick.req.current.speech
    if speech is None:
        return
    st = tick.state
    finals = sorted(
        (w for w in speech.words if w.final and w.end_ms > st.last_final_end_ms),
        key=lambda w: (w.end_ms, w.start_ms),
    )
    for word in finals:
        st.last_final_end_ms = max(st.last_final_end_ms, word.end_ms)
        if not tick.stt_ok or _before_recovery(tick, word):
            continue  # STT 를 믿을 수 없던 때의 단어는 진행도 · 군더더기 · 키워드에 넣지 않는다
        if word.filler:
            tick.filler_new += 1
            continue
        text = "".join(word.w.split())
        slide = _slide_of(tick, word)
        if not text or slide is None:
            continue
        key = str(slide)
        st.slide_chars[key] = st.slide_chars.get(key, 0) + len(text)
        _track_keywords(tick, slide, text)


def _before_recovery(tick: Tick, word: Word) -> bool:
    since = tick.state.stt_ok_since_ms
    return since is not None and word.start_ms < since


def _slide_of(tick: Tick, word: Word) -> int | None:
    # 확정 결과는 1~3초 늦게 온다. 장이 바뀐 뒤에 도착한 단어도 '말한 시각'의 장에 붙인다
    for slide, start in reversed(tick.state.slide_log):
        if word.start_ms >= start:
            return slide
    return tick.slide_number


def _track_keywords(tick: Tick, slide: int, text: str) -> None:
    st = tick.state
    key = str(slide)
    tail = (st.keyword_tails.get(key, "") + text)[-tick.cfg.speech.keyword_tail_chars :]
    st.keyword_tails[key] = tail
    plan = tick.slide_plan(slide)
    if plan is not None and plan.required_keywords:
        found = st.keywords_found.setdefault(key, [])
        for keyword in plan.required_keywords:
            if keyword not in found and "".join(keyword.split()) in tail:
                found.append(keyword)
    # '이 장 핵심 키워드를 말해 보세요' 뒤에 다음 장으로 넘어가서 말해도 말한 것으로 친다
    for p in st.pending:
        if p.keyword and "".join(p.keyword.split()) in tail:
            done = st.keywords_found.setdefault(str(p.slide_number), [])
            if p.keyword not in done:
                done.append(p.keyword)


def compute_cpm(words: list[Word], t: int, window_ms: int) -> tuple[float | None, int, int]:
    """(cpm, 말한 ms, 단어 수). 창 안의 군더더기 아닌 단어만 센다."""
    lo = t - window_ms
    chosen = [w for w in words if not w.filler and w.start_ms >= lo and w.end_ms <= t]
    chars = sum(nonspace_len(w.w) for w in chosen)
    speak_ms = sum(max(0, w.end_ms - w.start_ms) for w in chosen)
    if speak_ms <= 0:
        return None, 0, len(chosen)
    return chars / speak_ms * 60_000, speak_ms, len(chosen)


def evaluate(tick: Tick) -> None:
    speech = tick.req.current.speech
    cfg = tick.cfg.speech

    filler_60 = _filler_count(tick, cfg.filler_window_ms)
    tick.metrics["filler_count_60s"] = filler_60 if tick.stt_ok else None
    tick.metrics["filler_count_30s"] = _filler_count(tick, 30_000) if tick.stt_ok else None
    observed = min(cfg.filler_window_ms, tick.t)
    tick.metrics["filler_per_min"] = (
        round(filler_60 * 60_000 / observed, 2) if tick.stt_ok and observed >= 10_000 else None
    )

    if speech is None:
        tick.metrics["cpm"] = None
        return

    # STT 가 불량이었다가 돌아온 직후에는 창을 돌아온 뒤로 줄인다 — 불량 구간의 단어로 속도를 재지
    # 않게
    window = cfg.window_ms
    since = tick.state.stt_ok_since_ms
    if since is not None and tick.t - since < window:
        window = tick.t - since
    raw_cpm, speak_ms, n_words = compute_cpm(speech.words, tick.t, window)
    recent, recent_ms, recent_n = compute_cpm(
        speech.words, tick.t, min(window, cfg.recent_window_ms)
    )
    tick.metrics["cpm_recent"] = (
        round(recent, 1)
        if tick.stt_ok
        and recent is not None
        and recent_ms >= cfg.recent_min_speak_ms
        and recent_n >= 3
        else None
    )
    enough = raw_cpm is not None and speak_ms >= cfg.min_speak_ms and n_words >= cfg.min_words
    cpm = round(raw_cpm, 1) if enough and raw_cpm is not None else None
    tick.metrics["cpm"] = cpm if tick.stt_ok else None
    confidence = 0.6 + 0.4 * min(1.0, speak_ms / (2 * cfg.min_speak_ms))

    if cpm is not None and cpm > cfg.fast_cpm:
        tick.detections.append(
            Detection(
                issue=Issue.PACE_FAST,
                severity=ramp(cpm, cfg.fast_cpm, cfg.fast_cpm_bad),
                confidence=confidence,
                sensor_ok=tick.stt_ok,
                slide_number=tick.slide_number,
                metric="cpm",
                evidence={"cpm": cpm, "window_ms": cfg.window_ms, "speak_ms": speak_ms},
            )
        )

    if filler_60 >= cfg.filler_threshold:
        tick.detections.append(
            Detection(
                issue=Issue.FILLER_FREQUENT,
                severity=ramp(filler_60, cfg.filler_threshold, cfg.filler_bad),
                confidence=1.0,
                sensor_ok=tick.stt_ok,
                slide_number=tick.slide_number,
                metric="filler_count_60s",
                evidence={
                    "filler_count_60s": filler_60,
                    "filler_per_min": tick.metrics["filler_per_min"],
                },
            )
        )

    if tick.cfg.features.keyword_missing:
        _keyword_missing(tick)


def _filler_count(tick: Tick, window_ms: int) -> int:
    since = tick.t - window_ms
    return sum(s.filler_new for s in tick.history_since(since)) + tick.filler_new


def _keyword_missing(tick: Tick) -> None:
    plan = tick.slide_plan(tick.slide_number)
    if plan is None or not plan.required_keywords or plan.script_chars <= 0:
        return
    said = tick.state.slide_chars.get(str(tick.slide_number), 0)
    progress = min(1.0, said / plan.script_chars)
    if progress < tick.cfg.speech.keyword_progress:
        return
    found = tick.state.keywords_found.get(str(tick.slide_number), [])
    missing = [k for k in plan.required_keywords if k not in found]
    if not missing:
        return
    keyword = missing[0]
    tick.detections.append(
        Detection(
            issue=Issue.KEYWORD_MISSING,
            severity=0.6,
            confidence=tick.cfg.speech.keyword_confidence,
            sensor_ok=tick.stt_ok,
            slide_number=tick.slide_number,
            keyword=keyword,
            params={"keyword": keyword},
            evidence={"keyword": keyword, "slide_progress": round(progress, 3)},
        )
    )
