from script_coverage.script_analysis.keywords import extract_keywords_tfidf, tokenize_nouns
from script_coverage.shared.text import normalize_script
from tests.unit.shared.slides import SLIDES


def test_tokenize_nouns():
    text = "예측 모델은 제휴 도서관 3곳의 2년 치 출입 기록으로 5G 서비스를 고려해 활용하여 사용자 방을 만들었습니다."
    assert tokenize_nouns(text) == [
        "예측", "모델", "제휴", "도서관", "출입", "기록", "5G", "서비스", "사용자",
        "예측 모델", "제휴 도서관", "출입 기록", "5G 서비스",
    ]  # fmt: skip


def test_extract_keywords_tfidf_on_small_deck():
    norms = [normalize_script(SLIDES[("가상대본2", n)]) for n in (1, 8, 9)]
    top = [[(k.term, k.score, k.tf) for k in ks] for ks in extract_keywords_tfidf(norms, top_k=3)]
    assert top == [
        [("동네", 0.2973, 2), ("동네 빵집", 0.2973, 2), ("빵집", 0.2802, 3)],
        [("할인", 0.3456, 3), ("에이전트", 0.2788, 2), ("리포트", 0.2788, 2)],
        [("폐기", 0.4778, 3), ("요금", 0.3855, 2), ("비용", 0.3855, 2)],
    ]


def test_extract_keywords_tfidf_without_nouns():
    assert extract_keywords_tfidf([normalize_script("")]) == [[]]
