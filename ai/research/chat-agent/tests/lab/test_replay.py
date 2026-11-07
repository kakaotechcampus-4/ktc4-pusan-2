"""고정 시험: 기록한 모델 응답을 넣으면 코어 · 서비스 HTTP 경로가 기대한 NDJSON 줄을 바이트 그대로 낸다 (기능 문서 10-2).

- 기록 · 기대 줄: `tests/lab/data/replay/<시나리오>.json` (`chat_agent_lab.replay` 로 만든다, 모델 응답은 실제 모델 기록)
- 본다: (1) 연구 앱 대역(코어 `handle_turn`)의 턴마다 상태 · 오류 본문 · 줄이 기대와 같다
  (2) 모델에 간 요청의 지문이 기록과 같다(같은 실행이다) (3) 서비스 `/agent/turn`(앱 전체)도 같은 줄을 낸다
- 실패하면: 줄이 바뀐 것이 의도한 변경이면 `.venv/bin/python -m chat_agent_lab.replay update <시나리오>`로 기대 줄을 다시
  쓰고 바뀐 줄을 PR 에서 설명한다. 지문이 바뀌었으면(프롬프트 · 도구 설명 변경) `record`로 다시 기록한다(요금이 듦).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chat_agent_lab import replay as rp

#: 배포 기본값(조사 흐름 켬)으로 돈다. 기록 · 기대 줄도 이 값으로 만들었다(tests/conftest.py)
pytestmark = pytest.mark.prefetch

GOLDEN = sorted(rp.DATA.glob("*.json"))


def _load(p: Path) -> dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))


def test_golden_files_exist_and_cover_a_tool_call_and_a_final_answer() -> None:
    assert [p.stem for p in GOLDEN] == ["ana-compare-4-5", "guide-nervous"]
    for p in GOLDEN:
        turns = _load(p)["expected"]["turns"]
        events = [json.loads(x)["event"] for t in turns for x in t["lines"]]
        assert "tool_done" in events or "plan" in events
        assert events.count("final") == len(turns)  # 턴마다 끝 줄 final 하나


@pytest.mark.parametrize("path", GOLDEN, ids=[p.stem for p in GOLDEN])
def test_core_replay_gives_the_same_lines_and_model_requests(path: Path) -> None:
    g = _load(path)
    got = rp.replay(g["calls"], g["scenario"])
    # 기록은 실제 시계로, 재생은 고정 시계로 돌아 시각이 든 요청은 기록 지문과 다를 수 있다. 재생끼리 비교한다
    assert got["fps"] == g["expected"]["fps"], (
        "모델 요청이 바뀌었다: 의도한 변경이면 record 로 다시 기록"
    )
    assert got["left"] == 0 and len(got["fps"]) == len(g["calls"])
    assert got["turns"] == g["expected"]["turns"]


@pytest.mark.parametrize("path", GOLDEN, ids=[p.stem for p in GOLDEN])
def test_service_http_replay_gives_the_same_lines(path: Path) -> None:
    """서비스 `/agent/turn`(요청 로그 미들웨어 · 스트림 응답 포함)이 코어와 같은 바이트를 낸다."""
    g = _load(path)
    got = rp.replay(g["calls"], g["scenario"], http=True)
    assert got["fps"] == g["expected"]["fps"]
    assert got["turns"] == g["expected"]["turns"]
