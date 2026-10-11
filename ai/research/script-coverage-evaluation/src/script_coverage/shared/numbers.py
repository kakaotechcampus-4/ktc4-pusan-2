"""한국어 수 표기 파서. 아라비아 숫자·한글 수사·혼합 표기를 수로 바꾼다."""

import re

HANGUL_DIGITS = {
    "영": 0,
    "공": 0,
    "일": 1,
    "이": 2,
    "삼": 3,
    "사": 4,
    "오": 5,
    "육": 6,
    "륙": 6,
    "칠": 7,
    "팔": 8,
    "구": 9,
}
SMALL_UNITS = {"십": 10, "백": 100, "천": 1000}
BIG_UNITS = {"만": 10**4, "억": 10**8, "조": 10**12}
NUMBER_TOKEN = re.compile(r"\d+(?:\.\d+)?|[영공일이삼사오육륙칠팔구]|[십백천]|[만억조]|점")


def parse_korean_number(s: str) -> float | None:
    """아라비아 숫자·한글 수사·혼합 표기를 수로 바꾼다.

    "1,240만" → 12400000, "1천만" → 10000000, "4.6" → 4.6,
    "천이백사십만" → 12400000, "사점육" → 4.6, "이공이육" → 2026.
    STT 는 숫자를 한글로 적는 경우가 많아서, STT 쪽 숫자를 읽을 때도 같은 함수를 쓸 수 있다.
    """
    s = s.replace(",", "").replace(" ", "")
    tokens = NUMBER_TOKEN.findall(s)
    if not tokens or "".join(tokens) != s:
        return None

    total, section, current = 0.0, 0.0, None
    fraction, fraction_scale = None, 1.0  # "점" 뒤 한글 소수부
    for tok in tokens:
        if fraction is not None:
            if tok not in HANGUL_DIGITS:
                return None
            fraction_scale /= 10
            fraction += HANGUL_DIGITS[tok] * fraction_scale
        elif tok[0].isdigit():
            current = float(tok)
        elif tok in HANGUL_DIGITS:
            # "이공이육" 처럼 단위 없이 이어지는 한글 숫자는 자릿수 나열로 본다
            current = HANGUL_DIGITS[tok] if current is None else current * 10 + HANGUL_DIGITS[tok]
        elif tok in SMALL_UNITS:
            section += (1 if current is None else current) * SMALL_UNITS[tok]
            current = None
        elif tok in BIG_UNITS:
            section += current or 0
            total += (section or 1) * BIG_UNITS[tok]
            section, current = 0.0, None
        elif tok == "점":
            fraction = 0.0
    value = total + section + (current or 0) + (fraction or 0)
    return value


def format_number(x: float) -> str:
    return f"{x:,.0f}" if float(x).is_integer() else f"{x:,.10g}"
