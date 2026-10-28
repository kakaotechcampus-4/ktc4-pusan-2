"""연구 폴더 준비 확인 (LLM 호출 없음).

이 폴더의 기본 실행(`uv run python -m pytest`)이 고르는 시험이다. 단위 테스트는 서비스
`tests/unit/review/`로 옮겼고 여기에는 live 만 남아, 이 시험이 없으면 고를 테스트가 없다
(종료 코드 5).
"""

import json
from pathlib import Path

from pitch_coach_ai.features.review import core
from pitch_coach_ai.features.review.prompts.review import REVIEW_SYSTEM_PROMPT
from review_lab import evaluate, paths
from review_lab.llm import CachedLLM


def test_lab_uses_the_service_core():
    # 서비스를 editable 로 설치하지 않았으면 import 에서 이미 실패한다
    assert evaluate.review_take is core.review_take


def test_examples_dir_is_the_service_examples():
    # examples.py 가 예시를 다시 쓰는 곳이 서비스 코어 패키지 안이다 (원본은 서비스 한 곳)
    assert Path(core.__file__).resolve().parent / "examples" / "review" == paths.EXAMPLES_DIR
    assert (paths.EXAMPLES_DIR / "request.json").is_file()


def test_every_take_scenario_has_an_answer():
    takes = {p.stem.split("__")[0] for p in paths.TAKES_DIR.glob("*.json")}
    answers = set(json.loads(paths.ANSWERS_PATH.read_text(encoding="utf-8"))["scenarios"])
    assert takes
    assert takes == answers


def test_cache_key_has_the_reasoning_effort(tmp_path):
    # 추론 강도가 다른 답을 캐시에서 꺼내지 않는다. 보내지 않은 답의 키는 전과 같다
    plain = CachedLLM(None, "m", tmp_path / "c.json")
    low = CachedLLM(None, "m", tmp_path / "c.json", reasoning_effort="low")
    assert plain.prefix.startswith("m\n" + REVIEW_SYSTEM_PROMPT)
    assert low.prefix != plain.prefix
