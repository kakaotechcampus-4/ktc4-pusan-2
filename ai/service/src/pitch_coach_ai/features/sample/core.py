"""대역의 진입 함수 셋 (`docs/ai/architecture.md` 7절의 judge · summarize · criteria).

실제 판정은 하지 않는다. 판정 모듈이 서비스에 붙는 규약(요청 검사 · 결과와 `meta` ·
공통 에러 본문 · `X-Request-ID`)을 확인하려고, 실제 모듈과 모양이 같은 결과를 아래 규칙으로 낸다.

- `judge(inputs, t_ms)` → 결과 하나(영역 `VOLUME`). 1초마다 부르는 자리다
  - 센 기록: `inputs.since_ms` 이상에서 시작해 `t_ms` 안에서 끝난 1초.
    같은 `t_ms` 가 두 번 오면 먼저 온 것만 쓴다 (6-2절)
  - `tally`: `[{t_ms: since_ms, values: {measured_ms, total_ms}}]` 한 조각.
    `total_ms` 는 `t_ms - since_ms` 다. 기록이 오지 않아도 시간은 흐른다
  - `metrics.measured_ratio` = measured_ms ÷ total_ms. 기준값 이상이면 `measurable`,
    `state` 는 `NORMAL`, 아니면 `UNKNOWN`
  - `issues` 는 늘 빈 목록이다(문제를 판정하지 않는다)
- `summarize(tally)` → `{"VOLUME": {"measured_ratio"}}`. 장 끝 · Take 끝의 합계로 낸다.
  `measured_ratio` 는 null 로 두지 않고 분모가 0이면 0이다 (7절)
- `criteria()` → `{}`. 내는 문제가 없다

예: since_ms=2000, t_ms=6000, 기록 2000 · 3000 · 5000 → measured 3000 / total 4000
    → measured_ratio 0.75, measurable true (`examples/`)

- 부르는 곳: `api/sample.py` 가 `judge` 를 부른다. `summarize` · `criteria` 는 코치 라우터가
  판정 모듈의 세 함수를 묶어 넘기는 자리를 보이려고 둔다(서비스에서 부르는 곳은 아직 없다)
- 파일 · 네트워크 · 환경변수 · 시계를 쓰지 않는다. 시간은 입력의 `t_ms` 뿐이다 (8절)
"""

from .config import DEFAULT, SampleConfig, criteria_version
from .schemas import IssueCriteria, JudgmentResult, SampleInput, TallyItem
from .version import FEATURE_VERSION

EVALUATOR = "sample"
AREA = "VOLUME"


def judge(inputs: SampleInput, t_ms: int, config: SampleConfig = DEFAULT) -> list[JudgmentResult]:
    since_ms = inputs.since_ms
    seen: set[int] = set()
    measured_ms = 0
    for record in inputs.records:
        if record.t_ms in seen:
            continue
        seen.add(record.t_ms)
        if since_ms <= record.t_ms and record.t_ms + record.duration_ms <= t_ms:
            measured_ms += record.duration_ms

    total_ms = max(t_ms - since_ms, 0)
    ratio = _ratio(measured_ms, total_ms)
    measurable = ratio >= config.min_measured_ratio
    tally = (
        [TallyItem(t_ms=since_ms, values={"measured_ms": measured_ms, "total_ms": total_ms})]
        if total_ms > 0
        else []
    )
    return [
        JudgmentResult(
            evaluator=EVALUATOR,
            area=AREA,
            t_ms=t_ms,
            # 커서는 되돌아가지 않는다 (since_ms 가 t_ms 보다 앞서 와도)
            counted_until_ms=max(t_ms, since_ms),
            criteria_version=criteria_version(FEATURE_VERSION, config),
            measurable=measurable,
            state="NORMAL" if measurable else "UNKNOWN",
            metrics={"measured_ratio": ratio},
            tally=tally,
            issues=[],
        )
    ]


def summarize(
    tally: dict[str, dict[str, float]], config: SampleConfig = DEFAULT
) -> dict[str, dict[str, float | dict[str, float] | None]]:
    """`tally` 는 영역 → 이름 → 합. 예: `{"VOLUME": {"measured_ms": 3000, "total_ms": 4000}}`."""
    sums = tally.get(AREA, {})
    return {AREA: {"measured_ratio": _ratio(sums.get("measured_ms", 0), sums.get("total_ms", 0))}}


def criteria(config: SampleConfig = DEFAULT) -> dict[str, IssueCriteria]:
    return {}


def _ratio(part: float, whole: float) -> float:
    """0–1. 분모가 0이면 0, 기록이 겹쳐 1을 넘으면 1로 자른다."""
    if whole <= 0:
        return 0.0
    return min(part / whole, 1.0)
