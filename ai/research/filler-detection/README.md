# filler-detection

군더더기 판정 모듈입니다. STT 확정 단어마다 "음 · 그 · 그니까" 같은 군더더기인지 층(T1 · T2 · T3)과 함께 판정하고, 최근 빈도로 코치가 1초마다 부르는 판정 결과(영역 `FILLER`)를 냅니다. 장 · Take 의 층별 · 단어별 횟수와 STT keyterm 단어 목록도 이 모듈이 냅니다.

- 설계: [docs/ai/features/filler.md](../../../docs/ai/features/filler.md)
- 공통 계약: [docs/ai/architecture.md](../../../docs/ai/architecture.md) 7절
- Issue: #152

## 실행
Python 3.12. LLM을 쓰지 않아 `ai/.env`가 필요 없습니다.
```bash
cd ai/research/filler-detection
uv sync
uv run ruff check . && uv run ruff format --check .
uv run python -m pytest
```

직접 불러 보기:
```python
from filler.core import judge

judge(
    {
        "t_ms": 41000,
        "since_ms": 40000,
        "words_since_ms": 38950,
        "stt_status": "ok",
        "utterance_ends": [36800],
        "words": [{"word": "음", "start_ms": 40050, "end_ms": 40400}],
    },
    t_ms=41000,
)
```

## 파일 지도
| 파일 | 하는 일 | 입력 → 출력 | 부르는 곳 | 고칠 때 |
|---|---|---|---|---|
| `src/filler/core.py` | 진입 함수. 단어별 층 · 간격 판정, 보류, 새 몫과 커서, 최근 창 지표, 잦음 문제, 장 · Take 합계 | `judge(inputs, t_ms, config)` → `[JudgmentResult]`, `summarize(tally)` → `{"FILLER": ...}`, `criteria()`, `keyterms()` | 코치(`/coach/evaluate` · `/coach/finalize` · `replay`) | 규칙 · 이유 문자열의 뜻이 바뀌면 기능 버전을 올리고 filler.md 6-1 · `examples/` 를 함께 고칩니다 |
| `src/filler/schemas.py` | 입출력 pydantic 모델 (= 계약). 입력은 모르는 필드를 무시하고 출력은 정해진 필드만 냅니다 | `judge` 입력 dict → `FillerInput`, 결과 → `JudgmentResult` · `FillerSummary` · `IssueCriteria` | `core.py`, 테스트 | 필드 이름 · 뜻이 바뀌면 `schema_version` · filler.md 5절을 함께 고칩니다 |
| `src/filler/config.py` | 사전(T1 · T2 · T3 · keyterm)과 기준값 `FillerConfig`, 단어 정규화 `normalize`. 값의 해시가 `criteria_version`에 들어갑니다 | — → `FillerConfig` | `core.py`, 테스트 | 단어 · keyterm 순서를 바꾸면 filler.md 5-2의 표와 BE keyterm 목록도 맞춥니다 |
| `src/filler/version.py` | 기능 버전 `filler-1.0` | — → 문자열 | `core.py` | 판정 규칙 · 출력의 뜻이 바뀔 때만 올립니다 |
| `src/filler/examples/` | `judge` · `summarize` · `criteria` 입력 · 출력 예시 JSON. 코치 · 말 속도가 이 예시로 먼저 개발합니다 | `<이름>_input.json` ↔ `<이름>_output.json` | `tests/test_examples.py`, 코치 개발 | 입력만 손으로 고치고 출력은 `tools/make_examples.py`로 다시 만듭니다 |
| `tools/make_examples.py` | 예시 입력을 읽어 지금 코드로 출력 JSON을 다시 씁니다 | `examples/*_input.json` → `examples/*_output.json` · `criteria.json` | 사람(기준값 · 규칙을 바꾼 뒤) | 출력 JSON을 손으로 고치지 않습니다 |
| `tests/` | 규칙 · 보류 · 커서 · 지표 · 합계 · 계약 · 예시 테스트 | — | pytest | 아래 '테스트' |

`src/filler/`는 나중에 AI 서비스의 `features/filler/`로 폴더째 옮깁니다. 코어는 pydantic 말고는 외부 패키지를 쓰지 않고, 다른 판정 모듈을 import하지 않습니다.

## 예시
`src/filler/examples/`에 입력 · 출력 JSON이 있습니다. 출력은 지금 코드로 돌린 값이고, `criteria_version`은 기본 기준값의 값입니다.

| 파일 | 보여 주는 것 |
|---|---|
| `judge_input.json` · `judge_output.json` | filler.md 5-5 첫 예시. 41초, 새 군더더기 "음" 하나만 세고 최근 창 2개 · 분당 2.93 |
| `judge_input_pending.json` · `judge_output_pending.json` | filler.md 5-5 경계 예시. 마지막 "그"가 보류 단어라 단어 커서가 그 앞에서 멈춤 |
| `judge_input_take_end.json` · `judge_output_take_end.json` | 같은 "매출은 · 그" 창에서 Take 끝 시각(`take_end_ms`)이 와 "그"가 정해져 셈 |
| `judge_input_stt_down.json` · `judge_output_stt_down.json` | `stt_status`가 `reconnecting`. 단어를 판정하지 않고 시간만 세며 `UNKNOWN` |
| `judge_input_frequent.json` · `judge_output_frequent.json` | 최근 창에 군더더기 8개(T1 · T2 · T3 · 바로 반복). `HIGH`와 `FILLER_FREQUENT` 하나 |
| `summarize_input.json` · `summarize_output.json` | Take `tally` 합계 → 군더더기 7개 · 분당 2.4 · 측정 비율 0.97 (filler.md 5-5 값) |
| `criteria.json` | `FILLER_FREQUENT` 판정 기준 |

기준값(`config.py`)이나 규칙을 바꾸면 출력과 `criteria_version`이 달라져 `tests/test_examples.py`가 깨집니다. 그때는 입력은 그대로 두고 출력만 다시 만들어 같은 PR에 넣습니다(architecture.md 12절).
```bash
uv run python tools/make_examples.py
```
다시 만든 뒤에도 `test_examples.py`의 문서 대조 테스트(상태 · 보류 등)가 깨지면, 새 기준값에서 예시가 보여 주려던 상황이 달라진 것이므로 입력을 고칩니다.

## 테스트
`uv run python -m pytest`로 모두 돕니다.

| 파일 | 보는 것 |
|---|---|
| `test_rules.py` | 층별 규칙: T1 늘 군더더기, T2 · T3 간격 경계(기준과 같으면 단독, 1 ms 짧으면 아님), 바로 반복, 창 첫 단어의 앞 간격, 문장 첫 단어, 사전 밖 단어, 문장부호 |
| `test_pending.py` | 마지막 T2 · T3 단어의 보류, 다음 단어 · 문장 끝 신호 · Take 끝 시각으로 정해지는 경우 |
| `test_cursor.py` | 단어 커서(보류 앞에서 멈춤, 되돌아가지 않음), 새 몫, 겹친 창을 이어 불러도 한 번만 셈, 1초마다 시간 몫, 시각이 거꾸로 · 겹친 단어 |
| `test_metrics.py` | 최근 창 경계, 분당 수의 분모, `HIGH` · 심각도 · 지속 시간, STT 끊김 |
| `test_summarize.py` | 합계 → 지표, 분모 0, 단어별 정렬 |
| `test_contract.py` | 입력 · 출력 모델(모르는 필드 · 필수 필드 · `stt_status` enum), 출력 JSON 키, `criteria_version`, 사전 검증, `criteria` · `keyterms` |
| `test_examples.py` | 예시 출력이 지금 코드 출력과 같음, 예시가 filler.md 5-5 설명과 맞음 |
| `test_core_boundary.py` | 코어가 상대 import와 표준 모듈 · pydantic만 쓰고 파일 · 출력을 다루지 않음 (어기면 실패) |
| `helpers.py` | 입력 도우미와 공통 계약 검사. `run()`은 판정할 때마다 공통 계약도 봅니다 |

규칙 테스트는 `helpers.CFG`(지금 기본값을 따로 적은 것)로 돕니다. 기준값을 다시 골라도 규칙 테스트는 그대로이고, 예시만 다시 만들면 됩니다.

## 기준값
모두 잠정값입니다.

| 값 | 기본값 | 근거 |
|---|---|---|
| 앞 · 뒤 간격 기준 | 400 ms | filler.md 5-5 예시("그니까" 뒤 간격 400 ms가 단독)와 맞춘 첫 값 |
| 문장 첫 단어 앞 간격 | 1000 ms | Deepgram `utterance_end_ms`(1초)와 같은 값 |
| 최근 창 | 60초 | BE가 보내는 창 60초 안 |
| 잦음 기준 · 아주 나쁨 | 6개 · 15개 | 코치 연구용 대역과 같은 값(코치 재생과 비교할 수 있음) |
| 분당 수를 내는 최소 관측 시간 | 10초 | 짧은 관측의 분당 수는 크게 흔들려서 |
| 문제 시작 · 끝이 늦게 잡히는 폭 | 30초 · 30초 | 대역과 같은 값 |

## 문서에 정해지지 않아 고른 것
- `issues[].persistence_sec`는 최근 창 군더더기의 끝 시각을 이른 순으로 놓고 기준 개수 번째 단어의 끝(조건이 성립한 시각)부터 지금까지입니다. 창 안 단어만으로 본, 조건이 이어진 시간의 하한입니다(그 앞에 이미 성립했다가 창 밖으로 나간 단어가 있을 수 있습니다).
- `severity`는 소수점을 자르지 않고 그대로 냅니다.
- 문장 첫 단어 규칙(T2의 마지막 조건)은 지금 기준값에서는 "뒤 간격이 기준 이상" 조건에 포함돼 결과를 바꾸지 않습니다. 기능 문서 규칙을 그대로 옮겨 두었습니다.
- `frequent_count`는 1 이상이어야 합니다(`persistence_sec`을 셀 단어가 있어야 해서).
