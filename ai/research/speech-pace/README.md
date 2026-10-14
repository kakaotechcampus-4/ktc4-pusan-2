# speech-pace

말 속도 · 멈춤 판정 모듈입니다. STT 확정 단어로 최근 말 속도(CPM)를 재고 단어 사이 멈춤을 세어, 코치가 1초마다 부르는 판정 결과(영역 `SPEED`)를 냅니다.

- 설계: [docs/ai/features/pace.md](../../../docs/ai/features/pace.md)
- 공통 계약: [docs/ai/architecture.md](../../../docs/ai/architecture.md) 7절
- Issue: #154

> **진행 중**: 판정 코어(`src/pace/`)까지 있습니다. `examples/`, 테스트, 평가 데이터 · 스크립트는 아직 없습니다.

## 폴더 · 파일 지도
| 위치 | 하는 일 |
|---|---|
| `src/pace/core.py` | 진입 함수 `judge` · `summarize` · `criteria` |
| `src/pace/schemas.py` | 입력 · 출력 pydantic 모델 (= 계약) |
| `src/pace/config.py` | 기준값 `PaceConfig`. 값의 해시가 `criteria_version`에 들어갑니다 |
| `src/pace/version.py` | 기능 버전 `pace-1.0` |

`src/pace/`는 나중에 AI 서비스의 `features/pace/`로 폴더째 옮깁니다. 코어는 pydantic 말고는 외부 패키지를 쓰지 않고, 다른 판정 모듈을 import하지 않습니다.

## 실행
Python 3.12. LLM을 쓰지 않아 `ai/.env`가 필요 없습니다.
```bash
cd ai/research/speech-pace
uv sync
uv run ruff check . && uv run ruff format --check .
uv run python -m pytest   # 테스트가 아직 없어 0개 실행(종료 코드 5)
```

직접 불러 보기:
```python
from pace.core import judge, summarize, criteria

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
| 시각이 거꾸로 · 겹친 단어 | 발화 시간만 0으로 두고 셈 | 계산에서 빼고 커서만 넘김 |
| `stt_status` 빠짐 | `ok`로 봄 | `ok`가 아닌 것으로 봄 |
| `stt_status` 모르는 값 | 문자열로 받아 `ok`가 아닌 것으로 봄 | 입력 오류 (pace.md 5-2의 enum) |

같은 입력에서 판정이 얼마나 달라지는지는 아직 재지 않았습니다. 코치 재생을 이 모듈로 다시 돌릴 때 여기에 적습니다.

## 문서에 정해지지 않아 고른 것
- STT가 `ok`가 아니면 `metrics.speak_ms`도 null입니다.
- `issues[].persistence_sec`는 0.0입니다(대역과 같음).
- `issues[].evidence.window_ms`는 실제로 쓴 창 길이입니다. STT 회복 직후나 Take 초반에는 15초보다 짧습니다.
- `tally` 값은 정수입니다(글자 수 · ms · 개수).

## 실험 결과
아직 없습니다. 평가 데이터 · `tools/evaluate.py` · `reports/results/`는 다음 단계입니다(pace.md 13절).
