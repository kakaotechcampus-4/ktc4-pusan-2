"""가상 발표 발화 원본에서 단어 시각이 붙은 평가 데이터를 만드는 도구.

하는 일: 손으로 쓴 `data/virtual/source.json`(표기가 달린 문장)을 읽어, 정해진 seed 의 난수로
단어마다 시작 · 끝 시각(ms)과 정답(군더더기 여부 · 사전 층)을 붙여
`data/virtual/words.json` 을 쓴다.
누가 부르나: 사람이 원본이나 시각 분포를 바꾼 뒤 한 번 돌린다. `tools/evaluate.py` 가 결과 파일을
읽고, `tests/test_virtual_data.py` 가 같은 seed 로 다시 만든 결과가 커밋된 파일과 같은지 본다.
입력과 출력: 입력은 `[{"id": "v01", "text": "..."}]`. `text` 는 공백으로 나눈 토큰이다.
  - 단어 토큰: 뒤에 `*` 가 붙으면 말한 사람이 군더더기로 넣은 단어(정답 군더더기), 없으면 정상 단어.
    정답은 판정 규칙이 아니라 사람이 들었을 때의 뜻으로 단다(규칙이 틀릴 사례도 일부러 넣는다).
  - 쉼 토큰: `,` 짧은 쉼, `..` 머뭇거림, `|` 문장 끝(뒤로 1초 넘게 쉼).
  출력은 `{"seed", "source", "takes": [{"id", "words", "utterance_ends", "take_end_ms"}]}`
  이고 Take 는 하나다. 단어는
  `{"utt", "word", "start_ms", "end_ms", "label_filler", "label_tier"}`(`utt` 는 발화 id)다.
처리 순서: 발화를 원본 순서대로 하나의 Take 시간선(0 ms 에서 시작)에 잇는다. 발화 사이에는
  U(1100, 1800) ms 를 두고 앞 발화의 마지막 단어 끝을 `utterance_ends` 에 넣는다.
  발화 안에서는 단어를 차례로 놓는다. 단어 길이는
  90 + 70 × 글자 수 × U(0.8, 1.2) ms(글자 수는 문장부호를 뺀 글자 수), 앞 단어와의 간격은
  쉼이 없으면 U(0, 120), `,` U(150, 380), `..` U(300, 900)(기준값 400 근처를 일부러 걸침),
  `|` U(1100, 1800) ms 다. `|` 를 만나면 그 앞 단어의 끝 시각을 `utterance_ends` 에 넣는다
  (Deepgram UtteranceEnd 는 마지막 단어의 끝 시각을 알린다). `take_end_ms` 는 마지막 단어 끝 + 1500.
고칠 때 주의: 단어 시각은 여기서 정한 분포에서 나오므로 평가 결과는 실제 정확도가 아니다.
분포나 난수 부르는 순서를 바꾸면 `words.json` 이 달라지니 다시 만들어 커밋한다. 생성 파일은
손으로 고치지 않는다.

실행: `ai/research/filler-detection` 에서 `uv run python tools/make_virtual_data.py --seed 0`
"""

from __future__ import annotations

import argparse
import json
import random
import unicodedata
from pathlib import Path
from typing import Any

from pitch_coach_ai.features.filler.config import DEFAULT, FillerConfig, normalize

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "virtual" / "source.json"
OUT = ROOT / "data" / "virtual" / "words.json"

#: 쉼 토큰 → 다음 단어 앞 간격의 범위(ms)
PAUSES: dict[str, tuple[float, float]] = {",": (150, 380), "..": (300, 900), "|": (1100, 1800)}
TIGHT_GAP = (0, 120)
TAKE_TAIL_MS = 1_500


def tier_of(word: str, config: FillerConfig = DEFAULT) -> str | None:
    """사전 층(정규화한 단어 기준). 사전에 없으면 None."""
    key = normalize(word)
    for name, words in (("T1", config.t1_words), ("T2", config.t2_words), ("T3", config.t3_words)):
        if key in words:
            return name
    return None


def char_count(word: str) -> int:
    """단어 길이를 정할 글자 수: 문장부호를 뺀 글자 수."""
    return sum(1 for ch in word if not unicodedata.category(ch).startswith("P"))


def build_utterance(uid: str, text: str, rng: random.Random) -> dict[str, Any]:
    """문장 하나를 단어 시각이 붙은 발화로 만든다."""
    words: list[dict[str, Any]] = []
    ends: list[int] = []
    pause: str | None = None
    clock = 0.0
    for token in text.split():
        if token in PAUSES:
            if pause is not None or not words:
                raise ValueError(f"{uid}: 쉼 토큰 {token!r} 위치가 잘못됐다")
            pause = token
            if token == "|":
                ends.append(words[-1]["end_ms"])
            continue
        label = token.endswith("*")
        word = token[:-1] if label else token
        if not word:
            raise ValueError(f"{uid}: 빈 단어 토큰")
        if words:
            lo, hi = PAUSES[pause] if pause else TIGHT_GAP
            clock += rng.uniform(lo, hi)
        start = clock
        clock += 90 + 70 * char_count(word) * rng.uniform(0.8, 1.2)
        words.append(
            {
                "word": word,
                "start_ms": round(start),
                "end_ms": round(clock),
                "label_filler": label,
                "label_tier": tier_of(word),
            }
        )
        pause = None
    if not words:
        raise ValueError(f"{uid}: 단어가 없다")
    return {
        "id": uid,
        "words": words,
        "utterance_ends": ends,
        "take_end_ms": words[-1]["end_ms"] + TAKE_TAIL_MS,
    }


def build(source: list[dict[str, str]], seed: int) -> dict[str, Any]:
    """원본 전체 → 출력 dict. 같은 seed 면 늘 같은 결과다.

    발화를 원본 순서대로 하나의 Take 시간선에 잇는다. 발화 사이에는 문장 사이 간격
    U(1100, 1800) ms 를 두고, 앞 발화의 마지막 단어 끝 시각을 문장 끝 신호에 넣는다
    (이미 `|` 로 들어 있으면 그대로). 그래서 발화의 첫 단어도 앞 간격을 알 수 있다
    (첫 발화는 앞 신호가 없다).
    """
    rng = random.Random(seed)
    words: list[dict[str, Any]] = []
    ends: list[int] = []
    for u in source:
        utt = build_utterance(u["id"], u["text"], rng)
        offset = 0
        if words:
            last_end = words[-1]["end_ms"]
            if last_end not in ends:
                ends.append(last_end)
            offset = last_end + round(rng.uniform(*PAUSES["|"]))
        for w in utt["words"]:
            words.append(
                {
                    "utt": u["id"],
                    **w,
                    "start_ms": w["start_ms"] + offset,
                    "end_ms": w["end_ms"] + offset,
                }
            )
        ends.extend(e + offset for e in utt["utterance_ends"])
    return {
        "seed": seed,
        "source": "data/virtual/source.json",
        "takes": [
            {
                "id": "virtual-take-1",
                "words": words,
                "utterance_ends": sorted(set(ends)),
                "take_end_ms": words[-1]["end_ms"] + TAKE_TAIL_MS,
            }
        ],
    }


def dumps(data: dict[str, Any]) -> str:
    """파일에 쓸 텍스트. 단어 하나를 한 줄로 두어 diff 를 읽기 쉽게 한다."""
    nl = "\n"
    lines = ["{", f' "seed": {data["seed"]},', f' "source": {json.dumps(data["source"])},']
    lines.append(' "takes": [')
    takes = []
    for t in data["takes"]:
        words = ("," + nl).join("    " + json.dumps(w, ensure_ascii=False) for w in t["words"])
        takes.append(
            "  {"
            + nl
            + f'   "id": {json.dumps(t["id"])},'
            + nl
            + f'   "utterance_ends": {json.dumps(t["utterance_ends"])},'
            + nl
            + f'   "take_end_ms": {t["take_end_ms"]},'
            + nl
            + '   "words": ['
            + nl
            + words
            + nl
            + "   ]"
            + nl
            + "  }"
        )
    lines.append(("," + nl).join(takes))
    lines.append(" ]")
    lines.append("}")
    return nl.join(lines) + nl


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--source", type=Path, default=SOURCE)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    source = json.loads(args.source.read_text(encoding="utf-8"))
    data = build(source, args.seed)
    # 줄 끝 자동 변환을 피하려고 바이트로 쓴다
    args.out.write_bytes(dumps(data).encode("utf-8"))
    n_words = sum(len(t["words"]) for t in data["takes"])
    print(f"{args.out.name}: 발화 {len(source)}개, 단어 {n_words}개, seed {args.seed}")


if __name__ == "__main__":
    main()
