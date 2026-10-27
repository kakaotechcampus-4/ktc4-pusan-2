"""BE 테스트가 읽는 코치 예시 사본(`src/coach/examples/`)이 서비스 예시와 같은지 본다.

BE 테스트(`backend/tests/coach/conftest.py` 등)가 이 연구 폴더 경로를 직접 읽어서, BE 가 서비스 경로
(`ai/service/src/pitch_coach_ai/features/coach/examples/`)로 바꿀 때까지 사본을 둔다
(코치 기능 문서 11절 확인 5). 다르면 `python -m coach_lab.examples` 로 다시 만든다
(서비스 예시와 사본을 함께 쓴다).
"""

from pathlib import Path

from pitch_coach_ai.features import coach

SERVICE = Path(coach.__file__).resolve().parent / "examples"
COPY = Path(__file__).resolve().parents[2] / "src" / "coach" / "examples"


def _files(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes().replace(b"\r\n", b"\n")
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_be_copy_of_examples_matches_the_service_examples() -> None:
    service, copy = _files(SERVICE), _files(COPY)
    assert service, f"서비스 예시가 없다: {SERVICE}"
    assert copy.keys() == service.keys()
    stale = [name for name in service if copy[name] != service[name]]
    assert not stale, f"사본이 낡았다: {stale} — python -m coach_lab.examples 로 다시 만든다"
