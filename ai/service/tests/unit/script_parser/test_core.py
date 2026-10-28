import pytest

from pitch_coach_ai.features.script_parser import EmptyScriptError, parse_script
from pitch_coach_ai.features.script_parser.config import BASE_CPM
from pitch_coach_ai.features.script_parser.schemas import LLMOutput, ParseRequest


class FakeLLM:
    """정해진 LLMOutput을 돌려주고, 받은 메시지를 남긴다."""

    def __init__(self, output: dict | Exception):
        self.output = output
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        if isinstance(self.output, Exception):
            raise self.output
        return LLMOutput.model_validate(self.output)


def llm_out(slides, preface_title_lines=(), terms=()):
    return {
        "preface_title_lines": list(preface_title_lines),
        "slides": [{"slide_number": n, "title_lines": t, "keywords": k} for n, t, k in slides],
        "terms": list(terms),
    }


DOC_SCRIPT = (
    "# 신규 서비스 성과 보고\n"
    "[1] 안녕하세요, 오늘은 신규 서비스 성과를 말씀드리겠습니다.\n"
    "[2] 시장 현황\n"
    "지난 분기 MAU는 12만 명으로 37% 늘었습니다."
)
DOC_LLM = llm_out(
    [(1, [], ["오늘", "신규 서비스", "성과"]), (2, ["시장 현황"], ["분기", "MAU", "37%"])],
    preface_title_lines=["# 신규 서비스 성과 보고"],
    terms=["MAU"],
)


def test_doc_example():
    """문서 5-5 예시와 같은 응답."""
    response = parse_script(ParseRequest(script_text=DOC_SCRIPT, target_ms=300000), FakeLLM(DOC_LLM))
    assert response.model_dump() == {
        "status": "success",
        "slides": [
            {"slide_number": 1, "script": "안녕하세요, 오늘은 신규 서비스 성과를 말씀드리겠습니다.", "script_chars": 24,
             "keywords": ["오늘", "신규 서비스", "성과"],
             "highlights": [{"start": 7, "end": 9}, {"start": 11, "end": 17}, {"start": 18, "end": 20}],
             "expected_ms": 160000},
            {"slide_number": 2, "script": "지난 분기 MAU는 12만 명으로 37% 늘었습니다.", "script_chars": 21,
             "keywords": ["분기", "MAU", "37%"],
             "highlights": [{"start": 3, "end": 5}, {"start": 6, "end": 9}, {"start": 19, "end": 22}],
             "expected_ms": 140000},
        ],
        "terms": ["MAU"],
        "meta": {"schema_version": "1.0", "feature_version": "script_parser-1.1", "model": None},
    }


def test_llm_sees_blocks():
    llm = FakeLLM(DOC_LLM)
    parse_script(ParseRequest(script_text=DOC_SCRIPT), llm)
    user = llm.calls[0][1][1]
    assert "<머리글>\n# 신규 서비스 성과 보고\n</머리글>" in user
    assert "<장 2>\n시장 현황\n지난 분기" in user
    assert "장을 나누지 못한" not in user


def test_preface_body_turns_fail():
    text = "안녕하세요, 시작합니다.\n[1] 시장 현황\n매출이 늘었습니다.\n[2] 감사합니다."
    llm = FakeLLM(llm_out([(1, ["시장 현황"], ["매출"]), (2, [], ["감사"])]))
    response = parse_script(ParseRequest(script_text=text, target_ms=1000), llm)
    assert response.status == "fail"
    assert len(response.slides) == 1
    slide = response.slides[0]
    assert slide.slide_number == 1
    assert slide.script == "안녕하세요, 시작합니다.\n매출이 늘었습니다.\n감사합니다."  # 제목 줄 · 구분자 없음
    assert slide.keywords == ["매출", "감사"]  # 장별 키워드를 합침
    assert slide.expected_ms == 1000


def test_preface_fail_keeps_other_slide_body():
    # 1장 제목 줄 "성과"가 2장의 본문 줄 "성과"를 지우지 않는다
    text = "도입 설명입니다.\n[1] 성과\n매출이 늘었습니다.\n[2] 다음 계획\n성과\n를 말씀드리면 이렇습니다."
    llm = FakeLLM(llm_out([(1, ["성과"], []), (2, ["다음 계획"], [])]))
    response = parse_script(ParseRequest(script_text=text), llm)
    assert response.status == "fail"
    assert response.slides[0].script == "도입 설명입니다.\n매출이 늘었습니다.\n성과\n를 말씀드리면 이렇습니다."


def test_preface_fail_ignores_unknown_slide_titles():
    # 대본에 없는 99장의 제목 줄은 버린다
    text = "소개합니다.\n[1] 제품 개요입니다.\n자세히 보겠습니다."
    llm = FakeLLM(llm_out([(1, [], []), (99, ["제품 개요입니다."], [])]))
    response = parse_script(ParseRequest(script_text=text), llm)
    assert response.status == "fail"
    assert response.slides[0].script == "소개합니다.\n제품 개요입니다.\n자세히 보겠습니다."


def test_preface_fail_keywords_spread_over_slides():
    # 장마다 키워드 10개, 상한 15개 → 앞 장 10개 + 다음 장 5개가 아니라 장마다 번갈아 고른다
    words = {n: [f"가{n}{i}" for i in range(10)] for n in (1, 2, 3)}
    text = "머리글 본문입니다.\n" + "\n".join(f"[{n}] " + " ".join(ws) for n, ws in words.items())
    llm = FakeLLM(llm_out([(n, [], ws) for n, ws in words.items()]))
    response = parse_script(ParseRequest(script_text=text), llm)
    kept = response.slides[0].keywords
    assert response.status == "fail"
    assert len(kept) == 15
    assert kept == [f"가{n}{i}" for n in (1, 2, 3) for i in range(5)]  # 장마다 5개, 대본 순서


def test_no_delimiter_fail():
    text = "피치코치는 발표 연습을 돕는 AI 코치입니다."
    llm = FakeLLM(llm_out([(1, [], ["피치코치", "발표 연습", "AI 코치"])], terms=["피치코치"]))
    response = parse_script(ParseRequest(script_text=text, target_ms=300000), llm)
    assert response.status == "fail"
    assert [s.slide_number for s in response.slides] == [1]
    assert response.slides[0].script_chars == 19
    assert response.slides[0].expected_ms == 300000
    assert "장을 나누지 못한 대본" in llm.calls[0][1][1]


def test_drops_words_not_in_script():
    llm = FakeLLM(llm_out([(1, ["없는 제목"], ["발표", "없는말", "발표", ""])], terms=["피치코치", "없는용어", "피치코치"]))
    response = parse_script(ParseRequest(script_text="[1] 피치코치 발표"), llm)
    assert response.status == "success"
    assert response.slides[0].script == "피치코치 발표"
    assert response.slides[0].keywords == ["발표"]
    assert response.terms == ["피치코치"]


def test_keywords_in_script_order():
    llm = FakeLLM(llm_out([(1, [], ["다", "가", "나"])]))
    response = parse_script(ParseRequest(script_text="[1] 가 나 다"), llm)
    assert response.slides[0].keywords == ["가", "나", "다"]
    assert [h.start for h in response.slides[0].highlights] == [0, 2, 4]


def test_keyword_limit():
    words = [f"단어{i}" for i in range(10)]
    llm = FakeLLM(llm_out([(1, [], words)]))
    response = parse_script(ParseRequest(script_text="[1] " + " ".join(words)), llm)
    assert response.slides[0].keywords == words[:7]


def test_expected_ms_cumulative_rounding():
    # 글자 수 1 · 1 · 1, 목표 1000 → 333 · 334 · 333 (나머지를 마지막 장에 몰지 않음)
    llm = FakeLLM(llm_out([(1, [], []), (2, [], []), (3, [], [])]))
    response = parse_script(ParseRequest(script_text="[1] 가\n[2] 나\n[3] 다", target_ms=1000), llm)
    assert [s.expected_ms for s in response.slides] == [333, 334, 333]


def test_expected_ms_last_slide_empty():
    llm = FakeLLM(llm_out([(1, [], []), (2, ["끝"], [])]))
    response = parse_script(ParseRequest(script_text="[1] 가나다\n[2] 끝", target_ms=1000), llm)
    assert [(s.script_chars, s.expected_ms) for s in response.slides] == [(3, 1000), (0, 0)]


def test_expected_ms_without_target():
    llm = FakeLLM(llm_out([(1, [], [])]))
    text = "가" * BASE_CPM
    response = parse_script(ParseRequest(script_text=f"[1] {text}"), llm)
    assert response.slides[0].expected_ms == 60000


@pytest.mark.parametrize("text", ["", "  \n\t", "[1]\n[2]  \n"])
def test_empty_script_skips_llm(text):
    llm = FakeLLM(llm_out([]))
    with pytest.raises(EmptyScriptError):
        parse_script(ParseRequest(script_text=text), llm)
    assert llm.calls == []


def test_all_title_lines_is_empty():
    llm = FakeLLM(llm_out([(1, ["제목"], [])]))
    with pytest.raises(EmptyScriptError):
        parse_script(ParseRequest(script_text="[1] 제목"), llm)


def test_llm_error_propagates():
    with pytest.raises(TimeoutError):
        parse_script(ParseRequest(script_text="[1] 본문"), FakeLLM(TimeoutError()))
