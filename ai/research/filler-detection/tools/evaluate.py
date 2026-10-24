"""군더더기 판정의 가상 평가 스크립트 — 코치처럼 1초마다 이어 불러 정밀도 · 재현율을 잰다.

하는 일: 가상 발표 데이터(`data/virtual/words.json`, 발화를 이어 붙인 Take 하나)를 실제 서비스처럼
`judge` 를 1초 간격으로 이어 부르고, 센 군더더기를 정답과 맞춰 층별(T1 · T2 · T3)과 전체
정밀도 · 재현율, 놓치거나 잘못 센 단어 목록, `gap_ms` 민감도 표, 바로 반복 규칙의 결과를
결과 JSON 으로 쓴다(filler.md 13 · 14절).
누가 부르나: 사람이 사전 · 기준값 · 규칙을 바꾼 뒤 한 번 돌린다. `tests/test_evaluate.py` 가
계산부를 부르고 커밋된 결과 파일의 `criteria_version` 이 지금 기준값과 같은지 본다.
코어(`src/filler/`)에는 파일을 읽는 코드를 두지 않으므로 평가는 모두 이 도구에 있다.
입력과 출력: 입력은 `make_virtual_data.py` 가 만든 `words.json`. 출력은
`reports/results/filler_eval.json`(`criteria_version` · 개수 · `by_tier` · `all` ·
`out_of_dictionary` · `streaming_matches_single_call` · `sweep_gap_ms` · `repeat_rule` ·
`errors`)이고 소수는 4자리, 분모가 0 이면 null 이다.
처리 순서 (Take 마다):
  1. t = 1000, 2000, … 마다 그 시각에 확정된 단어만 보낸다. 단어는 `end_ms + 800 <= t`
     (STT 확정 지연), 문장 끝 신호는 `u + 1000 <= t`(UtteranceEnd 는 1초 쉰 뒤 온다)일 때.
     창은 t 앞 60초(Take 가 60초보다 길어 창이 Take 를 따라 움직인다).
  2. 코치처럼 돌려받은 `counted_until_ms` · `words_counted_until_ms` 를 다음 요청의 커서로
     넘긴다(`stt_status` ok, `stt_ok_since_ms` 0). 마지막 요청은 t = `take_end_ms` 로 마지막
     창의 단어와 신호에 Take 끝 시각을 넣는다.
  3. 모든 요청의 `filler_count` 단어 몫을 `t_ms`(= 단어 start_ms)로 단어에 맞춘다. 같은 단어가
     두 번 세어지면 중복 집계 오류로 멈춘다.
  4. 같은 Take 의 모든 단어를 한 번에(창 없이 + `take_end_ms`) 부른 결과와 센 단어가 같은지
     확인한다(60초 창으로 나눠 부르는 일이 결과를 바꾸지 않는다는 확인이다).
고칠 때 주의: 가상 데이터의 단어 시각은 `make_virtual_data.py` 의 분포에서 나온 것이라 이 숫자는
실제 정확도가 아니다. 지표 정의(filler.md 14절)나 결과 키를 바꾸면 README 의 결과 표와
`tests/test_evaluate.py` 도 함께 고친다.

실행: `ai/research/filler-detection` 에서
`uv run python tools/evaluate.py --data data/virtual/words.json`
(`--out` 을 안 주면 `reports/results/filler_eval.json` 에 쓴다)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from filler.config import DEFAULT, FillerConfig, normalize
from filler.core import REASON_REPEAT, criteria_version, judge

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "virtual" / "words.json"
OUT = ROOT / "reports" / "results" / "filler_eval.json"

TICK_MS = 1_000
#: STT 가 단어를 확정해 BE 에 넘기기까지의 지연(ms). 단어는 끝난 지 이만큼 뒤에 보인다
CONFIRM_DELAY_MS = 800
#: Deepgram UtteranceEnd 는 말이 끊긴 지 1초 뒤에 온다
SIGNAL_DELAY_MS = 1_000
WINDOW_MS = 60_000
SWEEP_GAP_MS = [200, 300, 400, 500, 600, 800]
TIERS = ("T1", "T2", "T3")
WORD_KEY = "filler_word:"
CONTEXT_WORDS = 2


# --------------------------------------------------------------------------
# 이어 부르기
# --------------------------------------------------------------------------


def _request(
    take: dict[str, Any], t: int, since: int, words_since: int, final: bool, windowed: bool = True
) -> dict[str, Any]:
    """시각 t 에 코치가 보낼 입력. 마지막(final) 요청은 Take 끝 시각을 넣고 확정 지연을 두지 않는다.

    `windowed` 면 t 앞 60초 단어 · 신호만 싣는다. 끄면 Take 의 모든 단어 · 신호를 싣는다(한 번에
    부르는 비교용).
    """
    lo = t - WINDOW_MS if windowed else -1
    if final:
        words = [w for w in take["words"] if w["start_ms"] >= lo]
        ends = [u for u in take["utterance_ends"] if u >= lo]
    else:
        words = [
            w for w in take["words"] if w["end_ms"] + CONFIRM_DELAY_MS <= t and w["start_ms"] >= lo
        ]
        ends = [u for u in take["utterance_ends"] if u + SIGNAL_DELAY_MS <= t and u >= lo]
    return {
        "t_ms": t,
        "since_ms": since,
        "words_since_ms": words_since,
        "stt_status": "ok",
        "stt_ok_since_ms": 0,
        "words": [
            {"word": w["word"], "start_ms": w["start_ms"], "end_ms": w["end_ms"]} for w in words
        ],
        "utterance_ends": ends,
        "take_end_ms": take["take_end_ms"] if final else None,
    }


def _index_words(take: dict[str, Any]) -> dict[int, list[int]]:
    """시작 시각 → 그 시각에 시작하는 단어 번호들(시작이 같은 단어가 둘일 수 있다)."""
    by_start: dict[int, list[int]] = {}
    for i, w in enumerate(take["words"]):
        by_start.setdefault(w["start_ms"], []).append(i)
    return by_start


def _collect(take: dict[str, Any], result: Any, counted: dict[int, str]) -> None:
    """결과의 단어 몫을 단어에 맞춰 `counted`(단어 번호 → 층)에 넣는다.

    몫은 `t_ms`(= 단어 start_ms)와 `filler_word:<단어>` 키(정규화한 단어)가 모두 맞는 단어에 붙인다.
    시작이 같은 단어가 둘이어도 섞이지 않는다. 같은 단어가 두 번 세어지면 중복 집계 오류,
    센 층이 정답의 사전 층과 다르면 층 불일치 오류로 멈춘다(정답 층도 같은 사전에서 나온 값이다).
    """
    by_start = _index_words(take)
    for item in result.tally:
        if item.values.get("filler_count") != 1:
            continue
        key = next(k[len(WORD_KEY) :] for k in item.values if k.startswith(WORD_KEY))
        found = [
            i for i in by_start.get(item.t_ms, []) if normalize(take["words"][i]["word"]) == key
        ]
        if not found:
            raise RuntimeError(f"센 몫에 맞는 단어가 없다: {key!r} ({item.t_ms} ms)")
        idx = found[0]
        word = take["words"][idx]
        if idx in counted:
            raise RuntimeError(
                f"중복 집계: {word['utt']} 의 {word['word']!r} ({word['start_ms']} ms)"
            )
        tier = next(t for t in TIERS if item.values.get(f"filler_{t.lower()}") == 1)
        if tier != word["label_tier"]:
            raise ValueError(
                f"층 불일치: {word['utt']} 의 {word['word']!r} 를 {tier} 로 셌는데 "
                f"정답 층은 {word['label_tier']} 이다(둘 다 같은 사전에서 나와야 한다)"
            )
        counted[idx] = tier


def stream_take(
    take: dict[str, Any], config: FillerConfig = DEFAULT
) -> tuple[dict[int, str], dict[int, str]]:
    """발화 하나를 1초마다 이어 불러 센 단어를 낸다.

    반환: (센 단어 번호 → 층, 단어 번호 → 판정이 처음 정해진 요청에서의 이유).
    """
    counted: dict[int, str] = {}
    reasons: dict[int, str] = {}
    by_span = {(w["start_ms"], w["end_ms"]): i for i, w in enumerate(take["words"])}
    since = words_since = 0
    take_end = take["take_end_ms"]
    for t in [*range(TICK_MS, take_end, TICK_MS), take_end]:
        inp = _request(take, t, since, words_since, final=t == take_end)
        [result] = judge(inp, t, config)
        _collect(take, result, counted)
        since, words_since = result.counted_until_ms, result.words_counted_until_ms
        for wj in result.words:
            # 정해진 판정(보류 아님)의 이유만 처음 정해졌을 때의 것을 남긴다. 창이 밀려 창의
            # 첫 단어가 된 뒤의 판정(앞 간격을 모름)으로 덮지 않는다
            if wj.is_filler is not None:
                reasons.setdefault(by_span[(wj.start_ms, wj.end_ms)], wj.reason)
    return counted, reasons


def single_call_take(take: dict[str, Any], config: FillerConfig = DEFAULT) -> dict[int, str]:
    """같은 단어를 한 번에(모든 단어 + `take_end_ms`) 불러 센 단어를 낸다."""
    counted: dict[int, str] = {}
    inp = _request(take, take["take_end_ms"], 0, 0, final=True, windowed=False)
    [result] = judge(inp, take["take_end_ms"], config)
    _collect(take, result, counted)
    return counted


# --------------------------------------------------------------------------
# 지표 (filler.md 14절)
# --------------------------------------------------------------------------


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def _scores(tp: int, fp: int, fn: int) -> dict[str, Any]:
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
    }


def tier_metrics(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], int]:
    """단어 행 → (층별 지표, 층 전체 지표, 사전에 없는 정답 군더더기 수).

    행: `label_filler` · `label_tier` · `counted`(센 층, 안 셌으면 None).
    정밀도는 그 층으로 센 단어 중 정답 비율, 재현율은 그 층의 정답 군더더기 중 센 비율이다.
    사전에 없는 정답 군더더기는 어떤 층에도 들지 않아 재현율 분모에 넣지 않고 따로 센다.
    """
    by_tier = {}
    for tier in TIERS:
        tp = sum(1 for r in rows if r["counted"] == tier and r["label_filler"])
        fp = sum(1 for r in rows if r["counted"] == tier and not r["label_filler"])
        fn = sum(
            1
            for r in rows
            if r["label_tier"] == tier and r["label_filler"] and r["counted"] is None
        )
        by_tier[tier] = _scores(tp, fp, fn)
    total = _scores(*(sum(by_tier[t][k] for t in TIERS) for k in ("tp", "fp", "fn")))
    ood = sum(1 for r in rows if r["label_filler"] and r["label_tier"] is None)
    return by_tier, total, ood


def _context(words: list[dict[str, Any]], i: int) -> str:
    left = [w["word"] for w in words[max(0, i - CONTEXT_WORDS) : i]]
    right = [w["word"] for w in words[i + 1 : i + 1 + CONTEXT_WORDS]]
    return " ".join([*left, f"[{words[i]['word']}]", *right])


def _rows(data: dict[str, Any], config: FillerConfig) -> tuple[list[dict[str, Any]], bool]:
    """모든 발화를 이어 불러 단어 행을 만든다. 두 번째 값은 한 번에 부른 결과와 같은지."""
    rows: list[dict[str, Any]] = []
    same = True
    for take in data["takes"]:
        counted, reasons = stream_take(take, config)
        same = same and set(counted) == set(single_call_take(take, config))
        words = take["words"]
        for i, w in enumerate(words):
            before = w["start_ms"] - words[i - 1]["end_ms"] if i > 0 else None
            after = words[i + 1]["start_ms"] - w["end_ms"] if i + 1 < len(words) else None
            rows.append(
                {
                    "take": take["id"],
                    "id": w["utt"],
                    "i": i,
                    "word": w["word"],
                    "start_ms": w["start_ms"],
                    "label_filler": w["label_filler"],
                    "label_tier": w["label_tier"],
                    "counted": counted.get(i),
                    "reason": reasons.get(i),
                    "context": _context(words, i),
                    "gap_before_ms": before,
                    "gap_after_ms": after,
                }
            )
    return rows, same


def _errors(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """놓친 단어(사전에 없는 정답 군더더기 포함)와 잘못 센 단어."""
    out = []
    for r in rows:
        counted = r["counted"] is not None
        if counted == r["label_filler"]:
            continue
        out.append(
            {
                "id": r["id"],
                "word": r["word"],
                "start_ms": r["start_ms"],
                "label_tier": r["label_tier"],
                "label_filler": r["label_filler"],
                "counted": counted,
                "reason": r["reason"],
                "context": r["context"],
                "gap_before_ms": r["gap_before_ms"],
                "gap_after_ms": r["gap_after_ms"],
            }
        )
    return out


def repeat_rule(data: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """바로 반복 규칙(앞의 것만 셈)을 데이터로 확인한다 (filler.md 13절).

    T2 · T3 단어가 문장 끝 신호 없이 같은 단어로 이어지는 쌍을 모아, 두 단어의 정답 조합마다
    앞 · 뒤 단어가 세어진 수를 낸다. 규칙은 반복의 앞 단어에 `REASON_REPEAT` 를 주고, 뒤 단어는
    자기 간격으로만 센다. 조합 `앞정답_뒤정답`: T_F 는 앞이 군더더기이고 뒤가 정상 단어
    ("그 그 방법은"), T_T 는 둘 다 군더더기("그 그 서비스는"), F_F 는 둘 다 정상이다
    ("진짜 진짜 좋아요").
    """
    by_key = {(r["take"], r["i"]): r for r in rows}
    combos: dict[str, dict[str, int]] = {}
    for take in data["takes"]:
        words = take["words"]
        for i in range(len(words) - 1):
            a, b = words[i], words[i + 1]
            if normalize(a["word"]) != normalize(b["word"]) or a["label_tier"] not in ("T2", "T3"):
                continue
            if any(a["end_ms"] <= u <= b["start_ms"] for u in take["utterance_ends"]):
                continue
            ra, rb = by_key[(take["id"], i)], by_key[(take["id"], i + 1)]
            name = f"{'T' if a['label_filler'] else 'F'}_{'T' if b['label_filler'] else 'F'}"
            c = combos.setdefault(
                name, {"pairs": 0, "first_counted": 0, "second_counted": 0, "first_by_repeat": 0}
            )
            c["pairs"] += 1
            c["first_counted"] += ra["counted"] is not None
            c["second_counted"] += rb["counted"] is not None
            c["first_by_repeat"] += ra["reason"] == REASON_REPEAT and ra["counted"] is not None
    return dict(sorted(combos.items()))


def evaluate(data: dict[str, Any], config: FillerConfig = DEFAULT) -> dict[str, Any]:
    """데이터 전체의 평가 결과(JSON 으로 쓸 dict, 민감도 제외)."""
    rows, same = _rows(data, config)
    by_tier, total, ood = tier_metrics(rows)
    return {
        "criteria_version": criteria_version(config),
        "data": "virtual",
        "seed": data["seed"],
        "n_utterances": len({w["utt"] for t in data["takes"] for w in t["words"]}),
        "n_words": len(rows),
        "by_tier": by_tier,
        "all": total,
        "out_of_dictionary": ood,
        "streaming_matches_single_call": same,
        "repeat_rule": repeat_rule(data, rows),
        "errors": _errors(rows),
    }


def sweep_gap_ms(data: dict[str, Any], gaps: list[int] | None = None) -> list[dict[str, Any]]:
    """`gap_ms` 만 바꿔 같은 평가를 다시 돌린 층별 지표 (다른 기준값은 기본값)."""
    out = []
    for gap in gaps or SWEEP_GAP_MS:
        rows, _ = _rows(data, FillerConfig(gap_ms=gap))
        by_tier, total, _ = tier_metrics(rows)
        out.append({"gap_ms": gap, "by_tier": by_tier, "all": total})
    return out


def run(data: dict[str, Any]) -> dict[str, Any]:
    """기본 평가 + 민감도. 결과 JSON 의 키 순서는 명세를 따르고 `errors` 가 맨 끝이다."""
    result = evaluate(data)
    errors = result.pop("errors")
    repeat = result.pop("repeat_rule")
    result["sweep_gap_ms"] = sweep_gap_ms(data)
    result["repeat_rule"] = repeat
    result["errors"] = errors
    return result


def dumps(result: dict[str, Any]) -> str:
    """결과 JSON 텍스트. 틀린 예 · 민감도 한 항목을 한 줄로 두어 길이를 줄인다."""
    parts = []
    for key, value in result.items():
        if key in ("errors", "sweep_gap_ms"):
            body = ",\n".join("  " + json.dumps(v, ensure_ascii=False) for v in value)
            parts.append(f' "{key}": [\n{body}\n ]' if value else f' "{key}": []')
        else:
            text = json.dumps(value, ensure_ascii=False, indent=1)
            parts.append(f' "{key}": ' + text.replace("\n", "\n "))
    return "{\n" + ",\n".join(parts) + "\n}\n"


def _print_summary(result: dict[str, Any]) -> None:
    print(
        f"{result['criteria_version']}  발화 {result['n_utterances']}개"
        f" · 단어 {result['n_words']}개"
        f" · 사전에 없는 정답 {result['out_of_dictionary']}개"
        f" · 이어 부르기 = 한 번에 부르기: {result['streaming_matches_single_call']}"
    )
    print(f"{'층':<4}{'tp':>5}{'fp':>5}{'fn':>5}{'정밀도':>9}{'재현율':>9}")
    for name, m in [*result["by_tier"].items(), ("전체", result["all"])]:
        print(
            f"{name:<4}{m['tp']:>5}{m['fp']:>5}{m['fn']:>5}{m['precision']!s:>9}{m['recall']!s:>9}"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path, default=DATA)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    data = json.loads(args.data.read_text(encoding="utf-8"))
    result = run(data)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # 줄 끝 자동 변환을 피하려고 바이트로 쓴다
    args.out.write_bytes(dumps(result).encode("utf-8"))
    _print_summary(result)
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
