"""연구 폴더 준비 확인 (LLM 호출 없음). 이 폴더의 기본 실행(`uv run python -m pytest`)이 고르는 시험이다."""

from pathlib import Path

import pytest

import run
from pitch_coach_ai.features import script_parser
from pitch_coach_ai.features.script_parser.schemas import LLMOutput, ParseRequest


class FakeLLM:
    def __init__(self, output: dict):
        self.output = LLMOutput.model_validate(output)

    def invoke(self, messages):
        return self.output


def parsed(text: str, slides: list[tuple[int, list[str]]]):
    """가짜 LLM으로 parse_script를 돌려 (응답, LLM 출력)을 만든다."""
    picked = {
        "preface_title_lines": [],
        "slides": [{"slide_number": n, "title_lines": [], "keywords": k} for n, k in slides],
        "terms": [],
    }
    llm = FakeLLM(picked)
    return run.parse_script(ParseRequest(script_text=text), llm), llm.output


TWO_SLIDES = "[1] 첫 문장입니다.\n둘째 문장입니다.\n[2] 셋째 문장입니다."


def test_run_uses_the_service_core_in_this_repo():
    # 서비스를 설치하지 않았으면 `import run` 에서 이미 실패한다. 다른 사본이 아니라 저장소 ai/service 의
    # 코어를 쓰는지(editable 설치)는 모듈 파일 위치로 본다
    assert run.parse_script is script_parser.parse_script
    service_dir = run.ROOT.parents[1] / "service"
    assert Path(script_parser.__file__).resolve().is_relative_to(service_dir)


def test_every_script_has_a_label_and_every_label_a_script():
    scripts = {p.stem for p in run.SCRIPTS_DIR.glob("*.txt")}
    labels = {p.stem for p in run.LABELS_DIR.glob("*.json")}
    assert scripts
    assert scripts == labels


def label(first_last: list[tuple[str, str]], status: str = "success") -> dict:
    return {
        "status": status,
        "slides": [
            {"slide_number": i, "first": first, "last": last}
            for i, (first, last) in enumerate(first_last, 1)
        ],
        "title_lines": [],
    }


def test_split_ok_compares_whole_first_and_last_lines():
    response, picked = parsed(TWO_SLIDES, [(1, []), (2, [])])
    third = ("셋째 문장입니다.", "셋째 문장입니다.")
    exact = label([("첫 문장입니다.", "둘째 문장입니다."), third])
    assert run.score(TWO_SLIDES, exact, response, picked)["split_ok"] is True
    # 앞부분 · 뒷부분만 같은 줄은 장 경계가 같다고 보지 않는다(14절 '첫 줄 · 마지막 줄이 같음')
    prefix = label([("첫 문장", "문장입니다."), third])
    assert run.score(TWO_SLIDES, prefix, response, picked)["split_ok"] is False


def test_keyword_on_missing_slide_is_not_in_script():
    # 3번 장은 없다. '첫 문장'은 1번 장에 있지만 3번 장 키워드로 왔으니 대본에 없는 것으로 센다
    response, picked = parsed(TWO_SLIDES, [(1, ["둘째"]), (2, []), (3, ["첫 문장"])])
    result = run.score(TWO_SLIDES, label([("", ""), ("", "")]), response, picked)
    assert (result["keywords"], result["keywords_in_script"]) == (2, 1)


def test_fail_script_looks_for_keywords_in_the_whole_script():
    text = "구분자 없는 대본입니다.\n둘째 줄입니다."
    response, picked = parsed(text, [(1, ["둘째"])])
    result = run.score(text, label([("", "")], status="fail"), response, picked)
    assert (response.status, result["keywords_in_script"]) == ("fail", 1)
