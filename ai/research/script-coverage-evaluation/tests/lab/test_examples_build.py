"""예시 생성기가 커밋된 예시 파일과 같은 내용을 만드는지 (연구 캐시가 없으면 건너뛴다)."""

import pytest

from coverage_lab.examples import EXAMPLES_DIR, build_examples
from coverage_lab.paths import DB_PATH

pytestmark = pytest.mark.skipif(
    not DB_PATH.is_file(), reason="연구 캐시(outputs/rubrics.sqlite)가 없다"
)


def _text(path):
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")  # 체크아웃이 CRLF 일 수 있다


def test_build_reproduces_committed_examples(tmp_path):
    written = build_examples(tmp_path)
    assert written
    for path in written:
        committed = EXAMPLES_DIR / path.relative_to(tmp_path)
        assert _text(path) == _text(committed), path.name
    assert not (tmp_path / "rubric" / "llm_cache.json").read_bytes().count(b"\r")  # 생성기는 LF


def test_build_does_not_change_the_research_cache(tmp_path):
    before = DB_PATH.stat().st_mtime_ns
    build_examples(tmp_path)
    assert DB_PATH.stat().st_mtime_ns == before
