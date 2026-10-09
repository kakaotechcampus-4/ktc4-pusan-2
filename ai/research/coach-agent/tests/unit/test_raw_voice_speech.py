"""음량 레벨 · 표시 없는 단어 — FE · BE 가 가공하지 않은 값을 보내도 코치가 기준을 잡고
군더더기를 센다."""

from __future__ import annotations

import pytest

from coach.evaluators.speech import is_filler
from coach.schemas import Word

SPEAKING = {"silence_ms": 0, "audio_live": True}


def voice(level: float | None = None, **kw) -> dict:
    return {**SPEAKING, "level_db": level, **kw}


def _cands(resp):
    return {c.issue_type.value: c for c in resp.candidates}


# ── 군더더기 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "flag", "want"),
    [
        ("음", None, True),
        ("어어", None, True),
        ("음...", None, True),
        ("으음", None, True),
        ("그", None, False),  # 뜻이 있을 수 있는 말은 세지 않는다
        ("이제", None, False),
        ("음식", None, False),
        ("음", False, False),  # BE 가 표시했으면 그대로
        ("그", True, True),
    ],
)
def test_filler_is_decided_by_the_word_unless_be_marked_it(text, flag, want):
    assert is_filler(Word(w=text, start_ms=0, end_ms=300, filler=flag)) is want
