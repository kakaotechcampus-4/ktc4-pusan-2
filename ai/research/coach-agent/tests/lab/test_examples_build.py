"""evaluate · finalize 예시 — 다시 만들면 커밋한 파일과 같다.

예시 응답이 코치 응답과 같은지는 서비스 계약 테스트가 본다(`tests/unit/coach/test_examples.py` ·
`test_finalize_examples.py`, 실제 판정 모듈). 여기서는 생성 도구가 같은 파일을 다시 만드는지만 본다.
"""

from __future__ import annotations

from pathlib import Path

from coach_lab.examples import EXAMPLES_DIR, write


def _text(path: Path) -> bytes:
    # 체크아웃할 때 줄바꿈이 CRLF 로 바뀔 수 있어 줄바꿈만 맞춰 비교한다
    return path.read_bytes().replace(b"\r\n", b"\n")


EXPECTED = {
    "evaluate/request.json",
    "evaluate/judge_results.json",
    "evaluate/response.json",
    "finalize/request.json",
    "finalize/response.json",
    "finalize/request_replay.json",
    "finalize/response_replay.json",
}


def test_examples_are_rebuilt_unchanged(tmp_path: Path) -> None:
    written = write(tmp_path)
    assert {p.relative_to(tmp_path).as_posix() for p in written} == EXPECTED
    for path in written:
        rel = path.relative_to(tmp_path)
        assert _text(path) == _text(EXAMPLES_DIR / rel), str(rel)
