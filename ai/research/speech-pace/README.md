# speech-pace

말 속도 · 멈춤 판정 모듈입니다. STT 확정 단어로 최근 말 속도(CPM)를 재고 단어 사이 멈춤을 세어, 코치가 1초마다 부르는 판정 결과(영역 `SPEED`)를 냅니다.

- 설계: [docs/ai/features/pace.md](../../../docs/ai/features/pace.md)
- 공통 계약: [docs/ai/architecture.md](../../../docs/ai/architecture.md) 7절
- Issue: #154

> **진행 중**: 판정 모듈 · 예시 · 테스트는 AI 서비스로 옮겼습니다(`ai/service/src/pitch_coach_ai/features/pace/` · `ai/service/tests/unit/pace/`). 이 폴더는 평가 데이터 · 스크립트 자리이고, 그것은 아직 없습니다.

## 폴더 · 파일 지도
판정 모듈의 원본은 서비스 한 곳입니다. 이 폴더는 서비스를 editable로 설치해 `pitch_coach_ai.features.pace`를 그대로 import합니다.

| 위치 | 하는 일 |
|---|---|
| `pyproject.toml` · `uv.lock` | 서비스(`ai/service`)를 editable로 설치. 판은 서비스 `uv.lock`과 같게 맞춥니다 |
| 서비스 `features/pace/core.py` | 진입 함수 `judge` · `summarize` · `criteria` |
| 서비스 `features/pace/schemas.py` | 입력 · 출력 pydantic 모델 (= 계약) |
| 서비스 `features/pace/config.py` | 기준값 `PaceConfig`. 값의 해시가 `criteria_version`에 들어갑니다 |
| 서비스 `features/pace/version.py` | 기능 버전 `pace-1.0` |
| 서비스 `features/pace/examples/` | `judge` · `summarize` · `criteria` 입력 · 출력 예시 JSON. 계약 테스트가 읽습니다 (아래 '예시') |
| 서비스 `tests/unit/pace/` | 판정 규칙 · 커서 · 보류 · 계약 테스트 (아래 '테스트') |

판정 모듈은 이 폴더의 `src/pace/`에서 서비스의 `features/pace/`로 폴더째 옮겼습니다(코드는 그대로, 테스트는 import 접두어와 예시 · 코어 폴더를 패키지 위치로 찾는 것만 바꿈). 코어는 pydantic 말고는 외부 패키지를 쓰지 않고, 다른 판정 모듈을 import하지 않습니다.

## 실행
Python 3.12. LLM을 쓰지 않아 `ai/.env`가 필요 없습니다. 테스트는 서비스 폴더에서 돌립니다.
```bash
cd ai/service
uv sync
uv run python -m pytest tests/unit/pace

cd ../research/speech-pace   # 평가 도구를 쓸 때 (서비스를 editable로 설치)
uv sync
uv run ruff check . && uv run ruff format --check .
```

직접 불러 보기:
```python
from pitch_coach_ai.features.pace.core import judge, summarize, criteria

judge(
    {
        "since_ms": 40000,
        "words_since_ms": 39300,
        "stt_status": "ok",
        "words": [{"word": "분기", "start_ms": 39700, "end_ms": 40050}],
        "fillers": [],
    },
    t_ms=41000,
)
```

## 예시
서비스 `features/pace/examples/`에 입력 · 출력 JSON이 있습니다. 세 `judge` 예시는 한 발표(단어 68개)를 41초 · 52초에 판정한 것이고, 출력은 지금 코드로 돌린 값입니다. `judge` 입력에는 `t_ms`도 실려 있습니다(입력 모델은 모르는 필드를 무시합니다).

| 파일 | 보여 주는 것 |
|---|---|
| `judge_input.json` · `judge_output.json` | pace.md 5-5 예시. 41초, 새 단어 "분기" · "매출은"만 세고 `NORMAL` |
| `judge_input_pending.json` · `judge_output_pending.json` | 52초, 마지막 "그"가 보류 단어라 단어 커서가 그 앞에서 멈춤. 말이 빨라져 `FAST` · `PACE_FAST` |
| `judge_input_fillers_null.json` · `judge_output_fillers_null.json` | 52초, 군더더기 판정 실패(`fillers: null`). 단어를 세지 않고 단어 커서 그대로, `UNKNOWN` |
| `criteria.json` | `PACE_FAST` · `PACE_SLOW` 판정 기준 |
| `summarize_input.json` · `summarize_output.json` | Take `tally` 합계 → `cpm` · `pause_count` · `measured_ratio` (pace.md 5-5 값) |

```python
import json
from pathlib import Path

from pitch_coach_ai.features.pace import core
from pitch_coach_ai.features.pace.core import judge

examples = Path(core.__file__).parent / "examples"
inputs = json.loads((examples / "judge_input.json").read_text(encoding="utf-8"))
judge(inputs, inputs["t_ms"])  # = judge_output.json
```

기준값(`config.py`)을 바꾸면 출력과 `criteria_version`이 달라져 서비스 `tests/unit/pace/test_examples.py`가 깨집니다. 그때는 출력 JSON을 다시 만들어 같은 PR에 넣습니다(architecture.md 12절). 입력은 그대로 두고 출력만 다시 만듭니다(`ai/service`에서).
```bash
uv run python - <<'EOF'
import json
from pathlib import Path

from pitch_coach_ai.features.pace import core
from pitch_coach_ai.features.pace.core import criteria, judge, summarize

ex = Path(core.__file__).parent / "examples"

def load(name):
    return json.loads((ex / name).read_text(encoding="utf-8"))

def save(name, data):
    (ex / name).write_bytes((json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

for case in ("", "_pending", "_fillers_null"):
    inputs = load(f"judge_input{case}.json")
    save(f"judge_output{case}.json", [r.model_dump(mode="json") for r in judge(inputs, inputs["t_ms"])])
save("criteria.json", {k: v.model_dump(mode="json") for k, v in criteria().items()})
save("summarize_output.json", summarize(load("summarize_input.json")))
EOF
```
다시 만든 뒤에도 `test_examples.py`의 문서 대조 테스트(상태 · 보류 등)가 깨지면, 새 기준값에서 예시가 보여 주려던 상황이 달라진 것이므로 입력을 고칩니다.

## 테스트
서비스 `tests/unit/pace/`에 있고, `ai/service`에서 `uv run python -m pytest tests/unit/pace`로 모두 돕니다. 이 폴더에는 연구 도구를 쓰는 테스트가 아직 없어 남은 테스트가 없습니다.

| 파일 | 보는 것 |
|---|---|
| `test_examples.py` | 예시 출력이 지금 코드 출력과 같음, 공통 계약, 같은 입력이면 같은 결과, 돌려받은 커서로 다시 부르면 새 몫 없음, 예시가 pace.md 5-5 설명과 맞음 |
| `test_judge.py` | 빠름 · 보통 · 느림, 기준과 같으면 `NORMAL`, 심각도 · 신뢰도, 최근 창 · 짧은 창, 측정 최소 기준, STT 끊김 · 회복, `fillers: null` |
| `test_tally.py` | 단어 몫 · 커서, 군더더기 · 보류 단어, 멈춤, 시각이 거꾸로이거나 겹친 단어, 1초마다 시간 몫, 겹친 창을 1초마다 보내도 단어마다 한 번씩만 셈 |
| `test_contract.py` | 입력 · 출력 모델(모르는 필드 · 필수 필드 · `stt_status` enum), `criteria_version`, `summarize` · `criteria` |
| `test_core_boundary.py` | 코어가 상대 import와 표준 모듈 · pydantic만 쓰고 파일 · 출력을 다루지 않음 (어기면 실패) |
| `helpers.py` | 입력 도우미와 공통 계약 검사. `run()`은 판정할 때마다 공통 계약도 봅니다 |

규칙 테스트는 `helpers.CFG`(지금 기본값을 따로 적은 것)로 돕니다. 기준값을 다시 골라도 규칙 테스트는 그대로이고, 예시만 다시 만들면 됩니다.

## 기준값
모두 잠정값입니다. 평가 데이터가 생기면 다시 고릅니다(pace.md 13절).

| 값 | 기본값 | 출처 |
|---|---|---|
| 빠름 · 느림 기준 | 350 · 275 CPM | 옛 노트북 (`archive/workspaces/seojin-lee/stt-live/`) |
| 아주 빠름 · 아주 느림 | 450 · 200 CPM | 코치 연구용 대역 `pace-0.2` |
| 최근 창 · 짧은 창 | 15초 · 6초 | 대역 (15초는 stt-live v1과 같은 값) |
| 측정 최소 발화 · 단어 수 | 최근 창 4초 · 5개, 짧은 창 2.5초 · 3개 | 대역 |
| 멈춤 기준 | 2초 | 대역 |

## 코치 연구용 대역(`pace-0.2`)과 달라진 점
대역: `ai/research/coach-agent/src/coach_lab/judges/pace.py`

| | 대역 `pace-0.2` | 이 모듈 `pace-1.0` |
|---|---|---|
| 글자 수 | 공백만 뺌 | 공백과 문장부호(유니코드 P 범주)를 뺌. 대본 파싱 `script_chars`와 같은 정의 |
| 군더더기 단어의 `tally` | 발화 시간을 `speak_ms`에 넣음 | 글자 수 · 발화 시간 모두 넣지 않음 (창 계산과 같은 규칙) |
| 보류 단어 | 창 CPM에 넣음 | 판정이 정해질 때까지 창에서도 뺌 |
| 시간 몫 (`ok_ms` · `total_ms`) | 빠진 구간을 한 조각으로 냄 | 1초마다 그 1초의 시작 시각으로 냄 |
| `fillers: null`인 1초 | STT 상태대로 `ok_ms`를 셈 | `ok_ms` 0 (단어를 세지 못한 시간) |
| 시각이 거꾸로 · 겹친 단어 | 거꾸로인 단어는 글자 수를 세고 발화 시간은 0, 겹친 단어는 그대로 셈 | 계산에서 빼고 커서만 넘김 |
| `stt_status` 빠짐 | `ok`로 봄 | `ok`가 아닌 것으로 봄 |
| `stt_status` 모르는 값 | 문자열로 받아 `ok`가 아닌 것으로 봄 | 입력 오류 (pace.md 5-2의 enum) |
| `since_ms` · `words_since_ms` · `fillers` | 빠지면 기본값 0 · −1 · `[]` | 필수 (빠지면 입력 오류) |
| `issues[].evidence` | `cpm` · `window_ms` · `speak_ms` | `cpm` · `window_ms` |
| `confidence`가 다 차는 발화 시간 | `min_speak_ms` × 2 | 따로 둔 `confidence_full_speak_ms`, 소수 넷째 자리에서 반올림 (기본값에서는 결과 같음) |
| 짧은 창 최소 단어 수 | 3으로 고정 | 설정 `short_min_words` (기본 3) |

코치 연구 시나리오 16개(`ai/research/coach-agent/scenarios/`)를 대역 대신 이 모듈로 돌려 봤습니다.
- 코치 쪽 기대 조건 위반 0건, pace 예외 0건
- 말 속도 개입 횟수는 대역과 같음
- Take CPM은 시뮬레이터에 넣은 값(300)과 같아짐 (대역은 약 296). 군더더기 발화 시간을 `tally`에서 빼고 시간 몫을 1초마다 나눈 효과

## 문서에 정해지지 않아 고른 것
- STT가 `ok`가 아니면 `metrics.speak_ms`도 null입니다.
- `issues[].persistence_sec`는 0.0입니다(대역과 같음).
- `issues[].evidence.window_ms`는 실제로 쓴 창 길이입니다. STT 회복 직후나 Take 초반에는 15초보다 짧습니다.
- `tally` 값은 정수입니다(글자 수 · ms · 개수).

## 실험 결과
아직 없습니다. 평가 데이터 · `tools/evaluate.py` · `reports/results/`는 다음 단계입니다(pace.md 13절).
