"""evaluate 예시 만들기 — 다시 만들면 커밋한 파일과 같다."""

from __future__ import annotations

from pathlib import Path

from coach_lab.examples import OUT_DIR, write


def _text(path: Path) -> bytes:
    # 체크아웃할 때 줄바꿈이 CRLF 로 바뀔 수 있어 줄바꿈만 맞춰 비교한다
    return path.read_bytes().replace(b"\r\n", b"\n")


def test_examples_are_rebuilt_unchanged(tmp_path: Path) -> None:
    for path in write(tmp_path):
        assert _text(path) == _text(OUT_DIR / path.name), path.name
