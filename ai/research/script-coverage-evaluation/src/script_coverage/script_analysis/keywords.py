"""TF-IDF 키워드: 슬라이드마다 두드러지는 명사를 뽑는다."""

import re
from collections import Counter

from sklearn.feature_extraction.text import TfidfVectorizer

from ..shared.facts import PREDICATE_SUFFIX
from ..shared.kiwi import get_kiwi
from ..shared.rubric import Keyword
from ..shared.text import NormalizedScript

NOUN_TAGS = {"NNG", "NNP", "SL", "SH"}
KEYWORD_STOPWORDS = {
    "저희",
    "여기",
    "이번",
    "이후",
    "다음",
    "먼저",
    "경우",
    "부분",
    "정도",
    "마지막",
    "이상",
    "감사",
    "안녕",
    "발표",
    "말씀",
    "자체",
    "하나",
    "우리",
    "자신",
}


def tokenize_nouns(text: str) -> list[str]:
    """TF-IDF 용 토큰: 명사 단위 + 붙어 있는 명사 2-gram ("좌석 예약", "예측 오차").

    - 명사+접미사(교수+자, 사용+자)는 한 단어로 합친다
    - 이어 붙은 영문·숫자(A+100, 5+G)는 한 단어로 합친다
    - 뒤에 '-하다'가 붙어 동사로 쓰인 명사(고려해, 활용하여)는 버린다
    - 한 글자 한글 명사(방, 곳, 양)는 뜻이 약해서 버린다
    """
    words: list[tuple[str, int, int]] = []  # (form, start, end)
    for tok in get_kiwi().tokenize(text):
        prev = words[-1] if words else None
        joinable = prev is not None and prev[2] == tok.start
        if tok.tag in PREDICATE_SUFFIX and joinable:
            words[-1] = ("", prev[1], tok.start + tok.len)
        elif (
            tok.tag == "XSN"
            and joinable
            and prev[0]
            or tok.tag in ("SL", "SN")
            and joinable
            and re.fullmatch(r"[A-Za-z0-9]+", prev[0])
        ):
            words[-1] = (prev[0] + tok.form, prev[1], tok.start + tok.len)
        elif tok.tag in NOUN_TAGS or tok.tag == "SN":  # 숫자는 5G, 3D 처럼 뒤 영문과 합치려고 둔다
            words.append((tok.form, tok.start, tok.start + tok.len))
        else:
            words.append(("", tok.start, tok.start + tok.len))  # 명사 연쇄를 끊는 표지

    def keep(w: str) -> bool:
        return len(w) >= 2 and w not in KEYWORD_STOPWORDS and not re.fullmatch(r"[\d.,]+", w)

    unigrams = [w for w, _, _ in words if keep(w)]
    bigrams = [
        f"{a[0]} {b[0]}"
        for a, b in zip(words, words[1:], strict=False)
        if keep(a[0]) and keep(b[0]) and b[1] - a[2] <= 1  # 사이에 조사 없이 붙거나 한 칸 띄운 명사
    ]
    return unigrams + bigrams


def extract_keywords_tfidf(norms: list[NormalizedScript], top_k: int = 8) -> list[list[Keyword]]:
    """TF-IDF / Keyword: 한 발표의 슬라이드들을 문서 집합으로 보고 슬라이드별 상위 키워드를 뽑는다.

    IDF 가 발표 안에서 계산되므로 "여러 슬라이드에 두루 나오는 말"은 낮고
    "이 슬라이드에서만 두드러지는 말"은 높게 나온다.
    """
    docs = [n.text for n in norms]
    vectorizer = TfidfVectorizer(analyzer=tokenize_nouns, sublinear_tf=True, norm="l2")
    try:
        matrix = vectorizer.fit_transform(docs)
    except ValueError:  # 발표 전체에 명사가 하나도 없음
        return [[] for _ in docs]
    vocab = vectorizer.get_feature_names_out()

    results = []
    for i, doc in enumerate(docs):
        counts = Counter(tokenize_nouns(doc))
        row = matrix.getrow(i).toarray().ravel()
        top = row.argsort()[::-1][:top_k]
        results.append(
            [
                Keyword(term=vocab[j], score=round(float(row[j]), 4), tf=counts[vocab[j]])
                for j in top
                if row[j] > 0
            ]
        )
    return results
