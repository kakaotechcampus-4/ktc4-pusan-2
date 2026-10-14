from pace.core import analyze
from pace.schemas import Word

# 예시 입력
words = [
    [
        Word(word="안녕하세요", start=0, end=600),
        Word(word="오늘은", start=700, end=1200),
        Word(word="발표를", start=1300, end=1700),
        Word(word="시작하겠습니다", start=1800, end=2700),
        Word(word="하하", start=2800, end=3000),
    ],
    [
        Word(word="저희", start=3000, end=3300),
        Word(word="서비스는", start=3400, end=3900),
        Word(word="발표자의", start=4000, end=4500),
        Word(word="발표를", start=4600, end=5000),
        Word(word="분석합니다", start=5100, end=5600),
        Word(word="음", start=5700, end=5800),
        Word(word="네", start=5900, end=6000),
    ],
    [
        Word(word="말하기", start=6000, end=6500),
        Word(word="속도와", start=6600, end=7100),
        Word(word="침묵을", start=7200, end=7700),
        Word(word="분석해", start=7800, end=8200),
        Word(word="피드백을", start=8300, end=8700),
        Word(word="제공해요", start=8800, end=9000),
    ],
    [
        Word(word="또", start=9000, end=9300),
        Word(word="발표", start=9400, end=9700),
        Word(word="시선", start=9800, end=10100),
        Word(word="보고", start=10200, end=10600),
        Word(word="발표", start=10700, end=11000),
        Word(word="습관", start=11100, end=11400),
        Word(word="분석", start=11500, end=12000),
    ],
    [
        Word(word="쉬면서", start=12000, end=12300),
        Word(word="발표", start=12400, end=12700),
        Word(word="하려고", start=16000, end=16300),
        Word(word="해요", start=16400, end=18000),
    ],
    [
        Word(word="속도를", start=18000, end=18300),
        Word(word="줄여", start=18400, end=21000),
    ],
]

if __name__ == "__main__":
    analyze(words)
