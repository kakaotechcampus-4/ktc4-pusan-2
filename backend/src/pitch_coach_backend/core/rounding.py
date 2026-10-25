"""DB 요약에 쓰는 공통 반올림 규칙 (architecture.md 1-1)."""

from decimal import ROUND_HALF_UP, Decimal


def round_half_up(value: int | float, *, scale: int = 1) -> int:
    """value × scale 을 반올림한다(.5 는 올림).

    round() 는 짝수 쪽으로 반올림하고(round(0.5) == 0), floor(x + 0.5) 는 이진 소수 오차로
    0.285 × 100 = 28.499… 를 28 로 만든다. 그래서 값을 적힌 자릿수(repr) 그대로 Decimal 로 센다.
    """
    exact = Decimal(repr(value)) * scale
    return int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP))
