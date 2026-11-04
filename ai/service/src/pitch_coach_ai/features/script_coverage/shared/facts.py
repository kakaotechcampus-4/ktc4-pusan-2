"""규칙 기반 Critical Fact Parser: 숫자·비율·날짜·시각·기간·금액·고유명사를 규칙으로 추출한다."""

import re
from typing import Literal

from pydantic import BaseModel, Field

from .kiwi import Morphemes, get_kiwi
from .numbers import BIG_UNITS, HANGUL_DIGITS, format_number, parse_korean_number
from .text import NormalizedScript

Importance = Literal["critical", "high", "normal"]
FactType = Literal[
    "percentage",
    "money",
    "date",
    "time",
    "duration",
    "quantity",
    "ratio",
    "number",
    "proper_noun",
    "term",
]


class CriticalFact(BaseModel):
    id: str = ""
    type: FactType
    value: str = Field(description="대본에 적힌 표기 그대로 (예: '약 1,240만 건')")
    normalized: str = Field(description="STT 와 비교할 정규형 (예: '12,400,000건')")
    numeric_value: float | None = None
    unit: str | None = None
    qualifier: str | None = Field(default=None, description="약·이상·이내 같은 한정어")
    spans: list[tuple[int, int]] = Field(
        description="정규화 텍스트 기준 등장 위치 (첫 번째가 대표)"
    )
    sentence_indices: list[int]
    source: Literal["rule", "llm"] = "rule"
    key_point_ids: list[str] = Field(default_factory=list, description="Rubric Builder 가 연결")
    importance: Importance = Field(
        default="normal", description="연결된 Key Point 중 가장 높은 중요도"
    )


# ── 정규식 빌딩 블록 ───────────────────────────────────────────
NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_KGROUP = rf"(?:{NUM})(?:[십백천만억조]|{NUM})*"
KNUM = rf"{_KGROUP}(?:(?<=[만억조])\s{_KGROUP}(?<=[십백천만억조]))*"  # 1,240만 / 1천만 / 2억 3천만
PRE = r"(?:(?P<pre>약|대략|최대|최소|평균|총|거의)\s?)?"
NEG = r"(?P<neg>마이너스\s?|(?<![\w)\-−])[-−])?"
POST = r"(?:\s?(?P<post>이상|이하|이내|미만|초과|가량|정도|내외|남짓))?"
NO_ALNUM_BEFORE = r"(?<![A-Za-z0-9.,])"

# 단위 목록. "원칙"의 원, "프로젝트"의 프로처럼 단위 글자로 시작하는 일반 단어는 여기서 막지 않고
# Kiwi 형태소 경계로 걸러낸다 (_unit_is_morpheme)
PERCENT_UNIT = r"퍼센트\s?포인트|%\s?포인트|%[pP]|%|퍼센트|프로"
MONEY_UNIT = r"원|달러|\$"
DURATION_UNIT = r"초|분|시간|일|주|개월|달|년"
COUNTER_UNIT = (
    r"개교|개국|개사|개소|개|명|곳|건|회|번|배|가지|차원|대|층|권|장|편|쪽"
    r"|페이지|종|위|점|세|살|인치|인|km|kg|mg|cm|mm|ml|mAh|m|g|GB|MB|TB|KB|kWh|Wh|W|V|GHz|MHz|Hz|fps|px"
)
# 한글 수사: STT 가 숫자를 한글로 받아 적는 경우와 대본의 "두 배", "세 가지"
NATIVE_ONES = {
    "한": 1,
    "두": 2,
    "세": 3,
    "석": 3,
    "네": 4,
    "넉": 4,
    "다섯": 5,
    "여섯": 6,
    "일곱": 7,
    "여덟": 8,
    "아홉": 9,
}
NATIVE_TENS = {
    "열": 10,
    "스물": 20,
    "스무": 20,
    "서른": 30,
    "마흔": 40,
    "쉰": 50,
    "예순": 60,
    "일흔": 70,
    "여든": 80,
    "아흔": 90,
}
_ONES = "다섯|여섯|일곱|여덟|아홉|한|두|세|석|네|넉"
NATIVE_NUMBER = rf"(?:(?:열|스물|서른|마흔|쉰|예순|일흔|여든|아흔)(?:{_ONES})?|스무|{_ONES})"  # 열두, 스물다섯, 서른, 세


def native_value(text: str) -> int:
    tens = next((t for t in NATIVE_TENS if text.startswith(t)), None)
    return NATIVE_TENS[tens] + NATIVE_ONES.get(text[len(tens) :], 0) if tens else NATIVE_ONES[text]


NATIVE_UNIT = r"배|가지|명|개월|개|곳|달|시간|주|해"
DURATION_BASE = {"달": "개월", "해": "년"}
# 한글로 읽은 수 (STT): "사십이", "만 구천", "십일 점 사", "천이백사십만".
# Kiwi 가 수사(NR)로 분석할 때만 인정한다
HNUM = r"(?<![가-힣])[일이삼사오육칠팔구십백천만억조]+(?:\s[일이삼사오육칠팔구십백천만억조]+)*(?:\s?점\s?[영공일이삼사오육칠팔구]+)?"
NUMBER = rf"(?:{KNUM}|{HNUM})"
HANGUL_YEAR = r"(?<![가-힣])(?:이천|천구백)[일이삼사오육칠팔구십]*"
HOUR_WORDS = {
    "한": 1,
    "두": 2,
    "세": 3,
    "네": 4,
    "다섯": 5,
    "여섯": 6,
    "일곱": 7,
    "여덟": 8,
    "아홉": 9,
    "열": 10,
    "열한": 11,
    "열두": 12,
}
MONTH_WORDS = {
    "일": 1,
    "이": 2,
    "삼": 3,
    "사": 4,
    "오": 5,
    "유": 6,
    "육": 6,
    "칠": 7,
    "팔": 8,
    "구": 9,
    "시": 10,
    "십": 10,
    "십일": 11,
    "십이": 12,
}

# 영문·숫자가 섞인 이름: SeatFlow, XGBoost, GPT-5, K-팝, v1 / 5G, 3D, 4K
LATIN_NAME = re.compile(
    r"(?<![A-Za-z0-9%])[A-Za-z][A-Za-z0-9]*(?:[-.&][A-Za-z0-9]+)*(?:-[가-힣]+)?"
)
DIGIT_NAME = re.compile(r"(?<![A-Za-z0-9.,])\d+[A-Za-z][A-Za-z0-9]*(?![A-Za-z0-9])")
LATIN_STOPWORDS = {"ai"}  # 핵심 사실로 보기엔 너무 일반적인 영문 토큰
UNIT_SYMBOLS = {
    "km",
    "kg",
    "mg",
    "cm",
    "mm",
    "ml",
    "mL",
    "m",
    "g",
    "L",
    "GB",
    "MB",
    "TB",
    "KB",
    "mAh",
    "kWh",
    "Wh",
    "W",
    "V",
    "Hz",
    "GHz",
    "MHz",
    "fps",
    "px",
    "x",
    "X",
    "p",
    "P",
}

# 순서가 중요하다: 앞 패턴이 차지한 구간은 뒤 패턴이 다시 잡지 않는다
NUMERIC_PATTERNS: list[tuple[str, re.Pattern]] = [
    # "3단계", "2번째" 같은 서수는 전달 여부를 따질 사실이 아니라서 구간만 막는다
    ("ordinal", re.compile(NO_ALNUM_BEFORE + r"\d+\s?(?P<u>단계|번째|회차|주차|차|학년|학기)")),
    (
        "date",
        re.compile(
            NO_ALNUM_BEFORE
            + r"(?:"
            + r"(?P<yq>(?:19|20)\d{2})\s?년\s?(?P<q>[1-4])\s?분기"
            + r"|(?P<yh>(?:19|20)\d{2})\s?년\s?(?P<h>상|하)반기"
            + r"|(?P<ys>(?:19|20)\d{2})\s?학년도"
            + r"|'?(?P<yy>\d{2})\s?년\s?(?:(?P<q2>[1-4])\s?분기|(?P<h2>상|하)반기)"
            + r"|'(?P<yy2>\d{2})\s?년"
            + r"|(?P<y>(?:19|20)\d{2})\s?년(?:\s?(?P<m>\d{1,2})\s?월)?(?:\s?(?P<d>\d{1,2})\s?일)?"
            + r"|(?P<y2>(?:19|20)\d{2})[./-]\s?(?P<m2>\d{1,2})[./-]\s?(?P<d2>\d{1,2})\.?"
            + rf"|(?P<hyq>{HANGUL_YEAR})\s?년\s?(?P<hq>[일이삼사])\s?분기"
            + rf"|(?P<hyh>{HANGUL_YEAR})\s?년\s?(?P<hhalf>상|하)반기"
            + rf"|(?P<hy>{HANGUL_YEAR})\s?년(?:\s?(?P<hm>십일|십이|십|시|유|육|[일이삼사오칠팔구])\s?월)?"
            + r"|(?P<m3>\d{1,2})\s?월(?:\s?(?P<d3>\d{1,2})\s?일)?"
            + r"|(?P<q3>[1-4])\s?분기)"
        ),
    ),
    (
        "time",
        re.compile(
            NO_ALNUM_BEFORE
            + r"(?:(?:(?P<ampm>오전|오후)\s?)?(?P<hh>\d{1,2})\s?(?P<si>시)"
            + r"(?:\s?(?P<mi>\d{1,2})\s?분|\s?(?P<half>반))?"
            + r"|(?P<ampm2>오전|오후)\s?(?P<hh2>\d{1,2}):(?P<mi2>\d{2})"
            + r"|(?:(?P<ampm3>오전|오후)\s?)?(?<![가-힣])(?P<hh3>열한|열두|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s?(?P<si3>시)"
            + r"(?:\s?(?P<mi3>[일이삼사오육칠팔구십]+|\d{1,2})\s?분|\s?(?P<half3>반))?)"
        ),
    ),
    ("number", re.compile(NO_ALNUM_BEFORE + r"(?P<a>\d+)\s?(?:곱하기|[xX×*])\s?(?P<b>\d+)")),
    ("ratio", re.compile(NO_ALNUM_BEFORE + r"(?P<a>\d+)\s?(?:대|:)\s?(?P<b>\d+)(?![\d:])")),
    (
        "ratio_hangul",
        re.compile(r"(?<![가-힣])(?P<a>일|이|삼|사|오|십)\s?대\s?(?P<b>일|이|삼|사|오|십)"),
    ),
    (
        "range",
        re.compile(
            NO_ALNUM_BEFORE + PRE + NEG + rf"(?P<n>{NUMBER})(?P<sep>\s?(?:~|∼|에서|부터)\s?)"
        ),
    ),
    (
        "percentage",
        re.compile(
            NO_ALNUM_BEFORE + PRE + NEG + rf"(?P<n>{NUMBER})\s?(?P<u>{PERCENT_UNIT})" + POST
        ),
    ),
    (
        "money",
        re.compile(NO_ALNUM_BEFORE + PRE + NEG + rf"(?P<n>{NUMBER})\s?(?P<u>{MONEY_UNIT})" + POST),
    ),
    (
        "duration",
        re.compile(
            NO_ALNUM_BEFORE
            + PRE
            + rf"(?P<n>{NUMBER})\s?(?P<u>{DURATION_UNIT})"
            + r"(?:\s?(?:동안|간|만에))?"
            + POST
        ),
    ),
    (
        "quantity",
        re.compile(NO_ALNUM_BEFORE + PRE + rf"(?P<n>{NUMBER})\s?(?P<u>{COUNTER_UNIT})" + POST),
    ),
    (
        "native",
        re.compile(PRE + rf"(?<![가-힣])(?P<nat>{NATIVE_NUMBER})\s(?P<u>{NATIVE_UNIT})" + POST),
    ),
    ("number", re.compile(NO_ALNUM_BEFORE + PRE + NEG + rf"(?P<n>{KNUM})" + POST)),
]
TYPED_PATTERNS = {
    kind: p
    for kind, p in NUMERIC_PATTERNS
    if kind in ("percentage", "money", "duration", "quantity")
}

# ── Kiwi 형태소 확인 ─────────────────────────────────────────
UNIT_HEAD_TAGS = {"NNB", "NNG", "NR", "SW", "SL"}  # 단위의 첫 형태소: 의존명사·명사·수사·기호·영문
UNIT_TAIL_TAGS = UNIT_HEAD_TAGS | {"XSN"}  # 뒤따르는 형태소는 접미사도 허용 (번+째)


def _unit_is_morpheme(m: re.Match, group: str, morph: Morphemes) -> bool:
    return morph.is_whole(*m.span(group), UNIT_HEAD_TAGS, UNIT_TAIL_TAGS)


def _hangul_number_ok(m: re.Match, morph: Morphemes) -> bool:
    """수 부분이 한글로 시작하면('사십이', '만 구천')
    Kiwi 가 수사(NR)로 분석했는지 본다. '점'(소수점)은 의존명사다.
    "이 분이 오셨다" 의 '이' 는 관형사라서 수가 아니다."""
    if m.group("n")[0].isdigit():
        return True
    return morph.is_whole(*m.span("n"), {"NR"}, {"NR", "NNB"})


def _starts_unit_quantity(text: str, pos: int, morph: Morphemes) -> bool:
    """pos 에서 '수 + 단위' 가 시작하는가.
    "20대 30대" 의 30대처럼 뒤 수에 단위가 붙으면 비율이 아니다."""
    return any(
        (um := p.match(text, pos)) is not None
        and _unit_is_morpheme(um, "u", morph)
        and _hangul_number_ok(um, morph)
        for p in TYPED_PATTERNS.values()
    )


def _confirmed_by_kiwi(kind: str, m: re.Match, text: str, morph: Morphemes) -> bool:
    """정규식 후보를 Kiwi 형태소로 확인한다. 단위 글자가 더 긴 단어의 일부면 버린다."""
    if kind == "ordinal":
        return _unit_is_morpheme(m, "u", morph)
    if kind in ("percentage", "money", "duration", "quantity"):
        return _unit_is_morpheme(m, "u", morph) and _hangul_number_ok(m, morph)
    if kind == "time":  # "3시간", "세 시간", "5시리즈" 의 시는 시각이 아니다
        return all(m.group(g) is None or _unit_is_morpheme(m, g, morph) for g in ("si", "si3"))
    if kind == "ratio":
        return not _starts_unit_quantity(text, m.start("b"), morph)
    if kind == "ratio_hangul":
        # "일대일"(한 단어) 이거나 양쪽이 온전한 수사
        # ("삼 대 일이" 의 '이' 는 조사, "삼 대 일십" 은 아님)
        return morph.is_whole(*m.span(), {"NNG"}, set()) or all(
            morph.is_whole(*m.span(g), {"NR"}, {"NR"}) for g in ("a", "b")
        )
    if kind == "native":  # 한·두·세 … 가 관형사(MM)나 수사(NR)로 쓰였는가 ("한 대학" 은 아님)
        return _unit_is_morpheme(m, "u", morph) and morph.is_whole(
            *m.span("nat"), {"MM", "NR"}, {"MM", "NR"}
        )
    return True


def _fact_fields(kind: str, value: float, unit: str | None, qualifier: str | None) -> dict:
    return dict(
        type=kind,
        normalized=f"{format_number(value)}{unit or ''}",
        numeric_value=value,
        unit=unit,
        qualifier=qualifier,
    )


def _unit_of(kind: str, unit: str) -> str:
    if kind == "percentage":
        return "%p" if "포인트" in unit or unit.lower() == "%p" else "%"
    if kind == "money":
        return "달러" if unit in ("달러", "$") else "원"
    return DURATION_BASE.get(unit, unit)


def _numeric_fact(kind: str, m: re.Match) -> dict | None:
    g = m.groupdict()
    qualifier = " ".join(q for q in (g.get("pre"), g.get("post")) if q) or None
    none = dict(numeric_value=None, unit=None, qualifier=None)
    if kind == "ordinal":
        return None
    if kind == "number" and g.get("a"):
        return dict(type="number", normalized=f"{g['a']}×{g['b']}", **none)
    if kind == "ratio":
        return dict(type="ratio", normalized=f"{g['a']}:{g['b']}", **none)
    if kind == "ratio_hangul":
        return dict(
            type="ratio",
            normalized=f"{parse_korean_number(g['a']):g}:{parse_korean_number(g['b']):g}",
            **none,
        )
    if kind == "date":
        year = g.get("yq") or g.get("yh") or g.get("ys") or g.get("y") or g.get("y2")
        hangul_year = g.get("hyq") or g.get("hyh") or g.get("hy")
        if hangul_year:
            year = format_number(parse_korean_number(hangul_year)).replace(",", "")
        short = g.get("yy") or g.get("yy2")
        year = year or (f"20{short}" if short else None)
        quarter = (
            g.get("q")
            or g.get("q2")
            or g.get("q3")
            or (str(HANGUL_DIGITS[g["hq"]]) if g.get("hq") else None)
        )
        half = g.get("h") or g.get("h2") or g.get("hhalf")
        if quarter or half:
            period = f"Q{quarter}" if quarter else ("H1" if half == "상" else "H2")
            return dict(type="date", normalized=f"{year}-{period}" if year else period, **none)
        month = (
            g.get("m")
            or g.get("m2")
            or g.get("m3")
            or (str(MONTH_WORDS[g["hm"]]) if g.get("hm") else None)
        )
        day = g.get("d") or g.get("d2") or g.get("d3")
        parts = [year] + [f"{int(p):02d}" for p in (month, day) if p]
        return dict(type="date", normalized="-".join(p for p in parts if p), **none)
    if kind == "time":
        hour = (
            int(g.get("hh") or g.get("hh2"))
            if (g.get("hh") or g.get("hh2"))
            else HOUR_WORDS[g["hh3"]]
        )
        minute_text = g.get("mi") or g.get("mi2") or g.get("mi3")
        minute = (
            int(parse_korean_number(minute_text))
            if minute_text
            else (30 if g.get("half") or g.get("half3") else 0)
        )
        if (g.get("ampm") or g.get("ampm2") or g.get("ampm3")) == "오후" and hour < 12:
            hour += 12
        return dict(type="time", normalized=f"{hour:02d}:{minute:02d}", **none)
    if kind == "native":
        unit = g["u"]
        fact_kind = "duration" if unit in ("개월", "달", "시간", "주", "해") else "quantity"
        return _fact_fields(
            fact_kind, native_value(g["nat"]), DURATION_BASE.get(unit, unit), qualifier
        )

    value = parse_korean_number(g["n"])
    if value is None:
        return None
    if g.get("neg"):
        value = -value
    unit = _unit_of(kind, g["u"]) if g.get("u") else None
    return _fact_fields(kind, value, unit, qualifier)


def _range_lower_fact(m: re.Match, text: str, morph: Morphemes) -> dict | None:
    """'10~20%', '5~10억 원', '3에서 5명' 의 앞 수는 뒤 수의 단위를 따른다."""
    for kind, pattern in TYPED_PATTERNS.items():
        upper = pattern.match(text, m.end())
        if (
            upper is None
            or not _unit_is_morpheme(upper, "u", morph)
            or not _hangul_number_ok(upper, morph)
        ):
            continue
        value = parse_korean_number(m.group("n"))
        big = re.fullmatch(rf"(?:{NUM})([만억조])", upper.group("n").replace(" ", ""))
        if value is not None and big and not re.search(r"[만억조]", m.group("n")):
            value *= BIG_UNITS[big.group(1)]  # 5~10억 → 5억
        if value is None:
            return None
        if m.group("neg"):
            value = -value
        return _fact_fields(kind, value, _unit_of(kind, upper.group("u")), m.group("pre"))
    return None


def sentence_index(norm: NormalizedScript, pos: int) -> int:
    for s in norm.sentences:
        if s.start <= pos < s.end:
            return s.index
    return norm.sentences[-1].index if norm.sentences else 0


def _add_fact(facts: dict, norm: NormalizedScript, span: tuple[int, int], **fields) -> None:
    """(type, normalized) 가 같은 사실은 하나로 합치고 등장 위치만 늘린다."""
    key = (fields["type"], fields["normalized"])
    sent = sentence_index(norm, span[0])
    if key in facts:
        facts[key].spans.append(span)
        facts[key].sentence_indices.append(sent)
        return
    facts[key] = CriticalFact(
        value=norm.text[span[0] : span[1]], spans=[span], sentence_indices=[sent], **fields
    )


def _take(taken: list[bool], span: tuple[int, int]) -> None:
    taken[span[0] : span[1]] = [True] * (span[1] - span[0])


def extract_latin_names(norm: NormalizedScript, facts: dict, taken: list[bool]) -> None:
    """영문·숫자 이름. 한 글자(A안, x)와 숫자 뒤 단위 기호(5,000 mAh)는 이름이 아니다."""
    text = norm.text
    for m in LATIN_NAME.finditer(text):
        word = m.group(0)
        if word.lower() in LATIN_STOPWORDS or len(word) == 1:
            continue
        if word in UNIT_SYMBOLS and re.search(r"\d\s?$", text[: m.start()]):
            continue
        _take(taken, m.span())
        _add_fact(facts, norm, m.span(), type="proper_noun", normalized=word.casefold())
    for m in DIGIT_NAME.finditer(text):
        if re.sub(r"^\d+", "", m.group(0)) in UNIT_SYMBOLS or any(taken[m.start() : m.end()]):
            continue
        _take(taken, m.span())
        _add_fact(facts, norm, m.span(), type="proper_noun", normalized=m.group(0).casefold())


def extract_numeric_facts(
    norm: NormalizedScript, facts: dict, taken: list[bool], morph: Morphemes
) -> None:
    for kind, pattern in NUMERIC_PATTERNS:
        for m in pattern.finditer(norm.text):
            if kind == "range":
                span = (m.start(), m.start("sep"))
                usable = not any(taken[span[0] : span[1]]) and _hangul_number_ok(m, morph)
                fields = _range_lower_fact(m, norm.text, morph) if usable else None
                if fields:
                    _take(taken, span)
                    _add_fact(facts, norm, span, **fields)
                continue
            start, end = m.span()
            if any(taken[start:end]) or not _confirmed_by_kiwi(kind, m, norm.text, morph):
                continue  # 버린 후보는 구간을 차지하지 않으므로 뒤 패턴이 다시 볼 수 있다
            _take(taken, (start, end))
            fields = _numeric_fact(kind, m)
            if fields:
                _add_fact(facts, norm, (start, end), **fields)


PREDICATE_SUFFIX = {"XSV", "XSA"}  # 고려+하다, 보정+하다: 명사가 동사·형용사로 쓰인 경우


def _is_standalone_proper_noun(surface: str) -> bool:
    """단어만 따로 분석해도 NNP 한 덩어리로 나오는지.
    문맥 때문에 NNP 로 잘못 붙은 일반 명사를 걸러낸다."""
    best = get_kiwi().analyze(surface, top_n=1)[0][0]
    return len(best) == 1 and best[0].tag == "NNP"


def extract_korean_proper_nouns(
    norm: NormalizedScript, facts: dict, taken: list[bool], morph: Morphemes
) -> None:
    """Kiwi 형태소 분석의 고유명사(NNP) 태그.

    - NNP 로 시작해 띄어쓰기 없이 붙은 명사까지 한 이름으로 본다 (서울+시립+도서관)
    - 뒤에 '-하다'가 붙으면 고유명사가 아니다 (보정하는)
    - NNP 한 토큰뿐이면 단독 분석으로 한 번 더 확인한다

    정밀도 우선이다. 사람 이름처럼 Kiwi 가 놓치는 고유명사는 LLM 분기의 key_terms 가 보완한다.
    """
    tokens = morph.tokens
    i = 0
    while i < len(tokens):
        if tokens[i].tag != "NNP":
            i += 1
            continue
        j = i + 1
        while (
            j < len(tokens)
            and tokens[j].tag in ("NNP", "NNG", "SL")
            and tokens[j].start == tokens[j - 1].end
        ):
            j += 1
        run, nxt = tokens[i:j], tokens[j] if j < len(tokens) else None
        i = j
        span = (run[0].start, run[-1].end)
        surface = norm.text[span[0] : span[1]]
        if len(surface) < 2 or any(taken[span[0] : span[1]]):
            continue
        if nxt is not None and nxt.tag in PREDICATE_SUFFIX and nxt.start == span[1]:
            continue
        if len(run) == 1 and not _is_standalone_proper_noun(surface):
            continue
        _add_fact(facts, norm, span, type="proper_noun", normalized=surface)


def extract_critical_facts(norm: NormalizedScript) -> list[CriticalFact]:
    """Critical Fact Parser: 숫자·비율·날짜·시각·기간·금액·고유명사를 규칙으로 추출한다."""
    facts: dict = {}
    taken = [False] * len(norm.text)
    morph = Morphemes(norm.text)  # 한 번 분석해서 숫자 단위 확인과 고유명사 추출에 같이 쓴다
    extract_latin_names(norm, facts, taken)  # GPT-5, A100 안의 숫자를 숫자 사실로 잡지 않도록 먼저
    extract_numeric_facts(norm, facts, taken, morph)
    extract_korean_proper_nouns(norm, facts, taken, morph)

    ordered = sorted(facts.values(), key=lambda f: f.spans[0][0])
    for i, fact in enumerate(ordered, 1):
        fact.id = f"CF{i}"
    return ordered
