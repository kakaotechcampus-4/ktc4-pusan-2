# evaluation-criteria

사용자가 자유 문장으로 작성한 발표 평가 기준을 LLM에 넣어, 화면에 보일 **표시 기준**(`display_criteria`), **요소별 값**(`values`, 요소 9개), 리뷰가 판정할 **숫자 목표**(`targets`), 요소에 없는 **뺀 기준**(`excluded`)을 냅니다.

- 설계 · 계약(입출력 필드, 요소 → 지표, 숫자 목표 규칙, 실패 처리): [`docs/ai/features/evaluation-criteria.md`](../../../docs/ai/features/evaluation-criteria.md) 5 · 6절
- 원본 노트북: [archive/workspaces/seojin-lee/evaluation-criteria/v1/local/evaluation_criteria_analysis.ipynb](../../archive/workspaces/seojin-lee/evaluation-criteria/v1/local/evaluation_criteria_analysis.ipynb)

```
standard_text ──▶ LLM 한 번 (SYSTEM_PROMPT + 출력 스키마 EvaluationCriteriaAnalysis)
                    │  display_criteria · values · targets · excluded
                    ▼
                 표시 기준 10개까지 → check_targets (지표 · 연산자 · 값 범위 · display 짝 · 중복 · 20개)
                    │
                    ▼
                 응답 EvaluationCriteriaResponse (+ meta)
```

---

## 구조

코어는 AI 서비스 `ai/service/src/pitch_coach_ai/features/evaluation_criteria/`에 있습니다(이 폴더의 `src/evaluation_criteria/`에서 폴더째 옮겼습니다). 이 폴더는 서비스를 editable로 설치해 코어를 그대로 import하므로, 코어는 서비스에서만 고칩니다. pydantic과 계산용 표준 모듈만 씁니다.

| 위치 | 내용 |
|---|---|
| (서비스) `features/evaluation_criteria/schemas.py` | 요청 · LLM 출력 · 응답 모델 = 계약. 지표 · 연산자 enum, 표시 기준 50자 검사 |
| (서비스) `features/evaluation_criteria/core.py` | `analyze_criteria(request, llm)` 진입 함수, `check_targets(targets, display_criteria, config)` 숫자 목표 검사 |
| (서비스) `features/evaluation_criteria/config.py` | 요소 → 지표 연결(원본), 표시 기준 50자 · 10개, 목표 20개, 지표별 값 범위 |
| (서비스) `features/evaluation_criteria/version.py` | `SCHEMA_VERSION` · `FEATURE_VERSION` |
| (서비스) `features/evaluation_criteria/prompts/system.py` | `SYSTEM_PROMPT` — 요소 표 · 단위 규칙 · 예시 |
| (서비스) `features/evaluation_criteria/examples/` | 요청 · 응답 예시 3쌍 (기능 문서 5-5: 기본 · 긴 침묵 경계 · 숫자 목표 없음) |
| `run.py` | 실험 도구. `build_llm()`(`ai/.env`로 LLM 생성), 문장 실행, `--eval` 채점 |
| `data/examples.json` | 평가 데이터 15문장과 정답(`values` · `targets` · `excluded`) |
| `tests/` | 가짜 LLM 테스트 (과금 없음, 아래 표). `fakes.py`는 서비스 테스트 도우미 중 `test_eval.py`가 쓰는 것의 사본 |
| `tests/live/` | 실제 LLM 테스트 (`live` 표시, 과금) |
| `results/` | `run.py --all` 결과 (gitignore) |
| `reports/results/` | `--eval` 결과 JSON (커밋) |

| 테스트 | 위치 | 보는 것 |
|---|---|---|
| `test_schema.py` | 서비스 `tests/unit/evaluation_criteria/` | 스키마 고정 필드 · 요청 길이(1~2,000자) · 표시 기준 50자 · 지표 · 연산자 enum |
| `test_check_targets.py` | 같음 | 값 범위 · `display` 짝 · 중복 · 20개 상한 |
| `test_core.py` | 같음 | `analyze_criteria` — 표시 기준 10개, `meta`, `script_used` 세 값, LLM 예외를 잡지 않음 |
| `test_examples.py` | 같음 | `examples/`가 계약을 통과하고 코어가 예시 응답을 그대로 냄 |
| `test_config.py` | 같음 | 요소 → 지표 연결이 스키마 enum · 프롬프트 요소 표와 같음 |
| `test_core_boundary.py` | 같음 | 코어 import 규약 (`architecture.md` 8절) |
| `test_api.py` | 같음 | 엔드포인트 `POST /evaluation-criteria/parse` — 응답 · `meta.model`, 에러 상태 코드, 25초 예산 · 역할, 로그에 기준 문장이 없음 |
| `test_data.py` | 이 폴더 `tests/` | 평가 데이터 정답이 계약에 맞고 13절 경우를 담음, 프롬프트 예시와 겹치지 않음 |
| `test_eval.py` | 같음 | `--eval` 채점 규칙 · 측정 기록 |
| `live/test_live_analyze.py` | 같음 | 실제 LLM 출력이 스키마를 통과 |

---

## 실행

```bash
# ai/research/evaluation-criteria/ 에서 (Python 3.12)
uv sync
```

OneDrive 폴더처럼 하드 링크가 안 되는 곳에서는 `UV_LINK_MODE=copy`를 설정하고 `uv sync`를 실행합니다.

실제 LLM을 부르는 명령만 `ai/.env`의 `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL`이 필요합니다(`ai/.env.example` 복사). 경로가 코드에 고정되어 있어 어느 디렉터리에서 실행하든 같은 파일을 읽고, 키나 모델이 비어 있으면 바로 에러가 납니다.

| 하는 일 | 명령 | 과금 |
|---|---|---|
| 테스트 (이 폴더) | `uv run python -m pytest` | 없음 |
| 코어 · 엔드포인트 테스트 (`ai/service`에서) | `uv run python -m pytest tests/unit/evaluation_criteria` | 없음 |
| 실제 LLM 테스트 | `uv run python -m pytest -m live` | `examples/` 요청 3번 |
| 직접 입력 | `uv run python run.py --text "추임새는 5번 이하로 해주세요."` | 1번 |
| 평가 데이터 일부 | `uv run python run.py --index 1 3` | 고른 수 |
| 평가 데이터 전체 | `uv run python run.py --all` → `results/examples.json` | 15번 |
| 채점 | `uv run python run.py --eval --out reports/results/evaluation_criteria_eval.json` | 15번 |

코드에서 직접 쓸 때 (`llm`은 필수 인자입니다. 코어는 LLM을 만들지 않습니다):

```python
from pitch_coach_ai.features.evaluation_criteria import analyze_criteria
from pitch_coach_ai.features.evaluation_criteria.schemas import EvaluationCriteriaRequest
from run import build_llm

llm = build_llm()                    # 여러 입력을 돌릴 때는 재사용
response = analyze_criteria(EvaluationCriteriaRequest(standard_text=text), llm)
response.model_dump()                # meta.model 은 None (서비스 라우터가 채움)
```

---

## 평가 결과

`run.py --eval` (2026-10-25 16:26, `gpt-6-luna`, `data/examples.json` 15문장) → [`reports/results/evaluation_criteria_eval.json`](reports/results/evaluation_criteria_eval.json). 지표 정의는 기능 문서 14절입니다.

| 지표 | 값 |
|---|---|
| 요소 분류 정확도 | 1.0 (135/135) |
| 숫자 목표 재현율 · 정밀도 | 1.0 · 1.0 (13/13) |
| 지어낸 목표 수 (문장당) | 0 |
| 표시 기준이 사용자 표현을 살린 비율 (사람이 봄) | 0.846 (11/13, 표시 기준이 없는 2문장 제외) |

| 측정 | 최소 | 중앙 | 최대 |
|---|---|---|---|
| 호출 한 번 시간 | 2.36초 | 3.7초 | 63.04초 (그다음 5.11초) |
| 출력 길이 | 268자 | 346자 | 633자 |
| 출력 토큰 (추론 포함) | 143 | 315 | 432 |
| 그중 추론 토큰 | 52 | 107 | 245 |

- 63.04초는 1문장(초당 글자 → cpm)입니다. 출력이 211토큰으로 짧아 생성이 아니라 연결 · 대기로 보입니다. `run.py`의 LLM은 시간 제한을 두지 않습니다(서비스는 호출 한 번 12초, `architecture.md` 9절).
- 같은 날 앞선 두 번의 실행(어미 규칙을 바꾸기 전 03:06 · 바꾼 뒤 16:23)도 14절 지표는 같았습니다. 세 번 중 가장 큰 값은 호출 8.0초(63초 제외), 출력 토큰 720(그중 추론 525)입니다.
- 평가 데이터: 요소 9개 · 지표 9개 · 연산자 4개 · `script_used` 세 값이 모두 한 번 이상 나옵니다. 퍼센트 · 분당 · 초당 바꾸기, 범위, 근사 · 절대 dB · 길이 조건(목표를 만들지 않음), 긴 침묵과 말 사이 멈춤 구분, 측정할 수 없는 기준만 있는 문장, 여러 기준이 섞인 문장을 담았습니다.
- 사용자 표현 판정에서 false는 2문장입니다. 사용자 단어를 다른 용어로 바꿨습니다: "1분에 3번을 넘지 않게" → "1분에 3번 이하", "50%를 넘어야" → "50% 초과".
- 점수가 높게 나오는 조건입니다: 15문장뿐이고, 정답은 프롬프트를 쓴 쪽이 같은 규칙으로 붙였습니다. 다른 사람이 만든 문장으로 다시 재야 합니다.
- LLM 출력은 실행마다 조금씩 다릅니다.

---

## 알아둘 것

| 항목 | 내용 |
|---|---|
| 고정 필드 스키마 | OpenAI Structured Output의 `json_schema` strict 모드는 자유 키 dict(`dict[str, ...]`)를 지원하지 않습니다. 요소를 `Criteria`의 고정 필드로, 지표 · 연산자를 enum으로 둡니다. 언급이 없는 요소도 `null`로 반환합니다 |
| 표시 기준 50자 | LLM에 보내는 JSON 스키마에는 길이 제한을 싣지 않고(strict 모드 호환), 응답을 pydantic으로 검증할 때 봅니다. 넘으면 자르지 않고 `ValidationError` → 서비스에서 `LLM_INVALID_OUTPUT` |
| 요소 → 지표 연결 | 원본은 서비스 `config.py`입니다. 프롬프트 요소 표 · 스키마 enum과 어긋나면 서비스 `test_config.py`가 깨지므로 셋을 함께 고칩니다 |
| 프롬프트 변경 | 프롬프트는 서비스에서 고칩니다. 결과의 뜻이 바뀌면 `version.py`의 `FEATURE_VERSION`을 올리고 `--eval`로 다시 잽니다. 평가 데이터 문장을 프롬프트 예시로 쓰지 않습니다(`test_data.py`) |
| `excluded` 어미 | 끝맺음("합니다 · 해 주세요 · 입니다")과 마침표만 빼고 "~해야 · ~이어야"는 남깁니다. 처음에는 "뗐을 때 명사로 끝나면 뗀다"였는데, LLM이 프롬프트 예시와 같은 구절("발표 주제는 창의적이어야")에서도 어미를 남겨 지금 규칙으로 줄였습니다. 그 규칙으로 돌린 실행에서는 15문장 중 5문장이 정답과 달랐습니다(어미를 남김 4 · 그중 1문장은 한 구절을 둘로 나눔, "~하기"로 바꿈 1). 지금 결과에서는 1문장이 정답과 다릅니다("10분 이내로 해야" → "10분 이내로"). 14절 지표에는 들어가지 않습니다 |
| `excluded` 한 구절 | "같은 대상에 대한 기준은 한 구절로" 규칙도 실행마다 다르게 지켜집니다. 어미 규칙을 바꾸기 전 실행에서 1문장("발표 내용은 이해하기 쉽고 논리적으로 구성되어야")을 둘로 나눴고, 지금 결과에서는 나누지 않았습니다 |
| 사람 판정 칸 | `--eval`을 다시 돌리면 `cases[].display_user_wording`과 `display_user_wording_ratio`가 `null`로 돌아갑니다. 결과를 보고 다시 채웁니다 |
