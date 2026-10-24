"""keyterm 파일 — `src/filler/keyterms.json` 이 지금 사전과 같은 최신 상태인지 본다."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from filler.config import DEFAULT
from filler.core import criteria_version, keyterms

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "src" / "filler" / "keyterms.json"
REGENERATE = "uv run python tools/export_keyterms.py"

_spec = importlib.util.spec_from_file_location(
    "export_keyterms", ROOT / "tools" / "export_keyterms.py"
)
assert _spec and _spec.loader
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

# filler.md 5-2 의 순서: T1 짧은 5 → T2 7 → T1 늘인 3
EXPECTED = [
    "음", "어", "아", "에", "으", "그", "저", "이제", "인제", "뭐", "자", "저기",
    "음음", "어어", "으음",
]  # fmt: skip


def _expected() -> dict:
    return {"criteria_version": criteria_version(DEFAULT), "keyterms": keyterms(DEFAULT)}


def test_keyterms_file_matches_current_dictionary():
    data = json.loads(PATH.read_text(encoding="utf-8"))
    assert data == _expected(), f"keyterms.json 이 오래됐다. 다시 만든다: {REGENERATE}"


def test_keyterms_are_the_fifteen_words_in_the_documented_order():
    data = json.loads(PATH.read_text(encoding="utf-8"))
    assert data["keyterms"] == EXPECTED
    assert len(data["keyterms"]) == 15


def test_keyterms_file_text_is_what_the_export_tool_writes():
    # 들여쓰기 · 마지막 줄바꿈까지 같아야 다시 만들어도 git diff 가 빈다. 줄 끝은 비교 전에
    # LF 로 맞춘다: Windows 에서 core.autocrlf 로 받으면 작업 사본이 CRLF 가 되기 때문이다
    raw = PATH.read_bytes().replace(b"\r\n", b"\n")
    assert raw == tool.build_text().encode("utf-8"), f"파일 모양이 다르다: {REGENERATE}"
