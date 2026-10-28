# review-agent (리뷰)

Take가 끝난 뒤 코치의 **Take 결과**와 **대본 전달도 채점**을 읽어 차원 상태 · 점수 · 미션 판정과 다음 미션 · 반복 문제를 규칙으로 정하고, LLM 한 번으로 요약 · 강점 · 개선점 문장과 다음 미션 설명을 씁니다. LLM 문장 속 숫자는 코드가 입력과 대조합니다.

| | |
|---|---|
| 담당 | seojin-lee |
| 기능 버전 | `review-1.2`, 요청 · 응답 스키마 `1.0` (`src/review/version.py`) |
| 상태 | 패키지 + 테스트(규칙 단계 · 가짜 LLM · 숫자 검사 계약 · 실제 LLM). **가상 발표 48 Take로만 평가했습니다** (실제 리허설 없음) |
| 설계 | 목적 · BE ↔ 리뷰 계약 · 처리 · 배포 → [`docs/ai/features/review-agent.md`](../../../docs/ai/features/review-agent.md) |

## 1. 폴더 · 파일 지도

```
review-agent/
├─ README.md
├─ pyproject.toml · uv.lock
├─ src/review/            리뷰 코어 → 서비스 features/review/ 로 폴더째 옮긴다
│   ├─ prompts/           LLM 지시문
│   └─ examples/review/   요청 · 응답 예시 (LLM 없음 · LLM 성공)
├─ src/review_lab/        연구 전용 (평가 · 실제 LLM 실행). 옮기지 않는다
├─ data/                  평가 묶음: takes/ (가상 발표 Take 결과 48개) · answers.json (정답 리뷰)
├─ reports/results/       커밋하는 평가 결과 (review_eval.json)
└─ tests/unit/ · live/    코어 경계 · 단계별 규칙 · 가짜 LLM · 숫자 검사 · 예시 계약 / 실제 LLM (live 표시)
```

**코어 `src/review/`**

| 파일 | 하는 일 | 설계 |
|---|---|---|
| `core.py` | 진입점 `apply_rules`(규칙 단계, 문장 없음) · `check_numbers`(숫자 검사) · `review_take`(최종: 규칙 → LLM → 숫자 검사) | 4-2 · 6-2 |
| `schemas.py` | 요청 · 응답 pydantic 모델 = 계약, LLM 출력 `ReviewDraft` | 5 · 6-2 |
| `llm_input.py` | 후보에 `candidate_id`(강점 `s1…` · 개선점 `i1…` · 다음 미션 `n1…`)를 붙이고 LLM 입력을 만든다 | 6-2 |
| `prompts/review.py` | 문장 쓰기 · 다음 미션 고르기 지시문 | 6-2 |
| `config.py` | 모든 기준값 · 가중치 · 다음 미션 지표. `load_config()`로 덮어쓰기, `criteria_version()` | — |
| `vocab.py` | 고정 어휘와 차원 표 | 5-3 |
| `version.py` | 스키마 · 기능 버전 | 9 |
| `metrics.py` | 지표 이름으로 값 찾기(`find_metric`), 비교(`compare`) | — |
| `evaluable.py` | 판단 가능 여부, 잰 영역(`measured_areas`) | 6-1 1번 |
| `missions.py` | 미션 · 사용자 기준 판정 | 6-1 2 · 3번 |
| `burden.py` | 차원 · 장 · 영역별 부담 | 6-1 4번 |
| `dimensions.py` | 차원 상태 | 6-1 5번 |
| `comparison.py` | 이전 Take 비교 | 6-1 6번 |
| `next_missions.py` | 다음 미션 후보, LLM이 고른 순서 붙이기(`merge_picked_missions`) | 6-1 7번 · 6-2 |
| `recurring.py` | 반복 문제 | 6-1 8번 |
| `score.py` | 점수 | 6-1 9번 |
| `text.py` | 강점 · 개선점 후보(`text_candidates`, `IMPROVED` 강점의 장 번호 포함)와 기본 문장 | 6-2 |

**예시 `src/review/examples/review/`**

| 파일 | 내용 |
|---|---|
| `request.json` | 요청 |
| `response.json` | LLM이 없을 때의 응답 (기본 문장, `fallback_reason: LLM_UNAVAILABLE`) |
| `llm_output.json` | 실제 LLM 출력(`ReviewDraft`) 그대로 |
| `response_llm.json` | 그 출력으로 만든 LLM 성공 응답 (`fallback_reason: null`) |

계약 테스트(`tests/unit/test_examples.py`)가 `request.json`의 응답이 `response.json`과, 가짜 LLM이 `llm_output.json`을 돌려줄 때의 응답이 `response_llm.json`과 같은지 본다.

**연구 도구 `src/review_lab/`**

| 파일 | 하는 일 |
|---|---|
| `llm.py` | `ai/.env`로 실제 LLM 만들기(호출 12초 · 재시도 1번), 응답 캐시 `CachedLLM` |
| `evaluate.py` | 평가 묶음 · 정답 리뷰로 13 · 14절 지표를 잰다 |
| `examples.py` | LLM 성공 예시(`llm_output.json` · `response_llm.json`) 만들기 |
| `build_data.py` | 코치 가상 발표를 재생해 `data/takes/`를 만든다 (코치 `.venv`로 실행) |
| `paths.py` | 폴더 위치 |

## 2. 실행법

```bash
cd ai/research/review-agent
uv sync                                   # Python 3.12, pydantic + 연구용 LLM 라이브러리 + 개발 도구
```

실제 LLM을 부르는 일(평가 · 예시 만들기)만 `ai/.env`의 기본 묶음(`OPENAI_API_KEY` · `OPENAI_MODEL` · `OPENAI_BASE_URL`)이 필요하고 과금됩니다. LLM 응답은 `outputs/llm_cache.json`에 캐시해 같은 프롬프트 · 모델 · 입력이면 다시 부르지 않습니다(실패한 호출은 캐시하지 않음, `outputs/`는 git에 없음). Windows에서는 `uv run python -m …` 형태로 부르고, 콘솔 한글이 깨지면 `PYTHONIOENCODING=utf-8`을 붙입니다.

| 하는 일 | 명령 | 과금 |
|---|---|---|
| 테스트 | `uv run python -m pytest` (실제 LLM 테스트는 기본으로 뺀다) | 없음 |
| 실제 LLM 테스트 | `uv run python -m pytest -m live` (예시 요청 1번) | 있음 |
| 린트 | `uv run ruff check . && uv run ruff format --check .` | 없음 |
| 평가 (규칙 단계만) | `uv run python -m review_lab.evaluate --rules-only` | 없음 |
| 평가 (실제 LLM) | `uv run python -m review_lab.evaluate --out reports/results/review_eval.json` | 있음 |
| LLM 성공 예시 다시 만들기 | `uv run python -m review_lab.examples` | 있음 |
| 평가 묶음 다시 만들기 | `../coach-agent/.venv/Scripts/python.exe src/review_lab/build_data.py` (macOS · Linux는 `.venv/bin/python`) | 없음 |

`config.py`를 바꾸면 `criteria_version`이 바뀌므로 `response.json` · LLM 성공 예시 · 평가 결과를 다시 만들어 같은 PR에 넣습니다(`architecture.md` 12절).

파이썬에서 직접

```python
from review import apply_rules, load_config, review_take
from review_lab.llm import load_settings, review_llm

# LLM 없이: 기본 문장 + fallback_reason LLM_UNAVAILABLE. dict 로: resp.model_dump(mode="json")
resp = review_take(request_dict)
# 실제 LLM
s = load_settings()
resp = review_take(request_dict, review_llm(s), model=s.model)
# 규칙 단계만 (문장 없음): rules.evaluable · rules.burden · rules.next_mission_candidates …
rules = apply_rules(request_dict)
# 설정 덮어쓰기
resp = review_take(request_dict, config=load_config(dimension={"priority_max_count": 2}))
```

설정은 `config.py`에만 있습니다. 코드를 고치지 말고 바꿀 값만 `load_config(...)`로 덮어씁니다.

## 3. 실험 결과

`reports/results/review_eval.json` (`review-1.1+99c57adfbbb0`, 모델 `openai/gpt-6-luna`)

**평가 묶음** (`data/`)
- 공통 기준 Take(`architecture.md` 12절)가 아직 없어, 코치 가상 발표 시나리오 16개(`coach-agent/scenarios/`)를 잡음 clean · noisy · harsh로 한 번씩 재생한 Take 결과 48개로 대신했습니다(`coach-1.2`).
- 정답 리뷰(`answers.json`)는 시나리오 설명 · 기대 결과로 **AI가 적은 초안**이고 리뷰 결과를 보기 전에 적었습니다. 시나리오로 정할 수 없는 차원 · 미션은 비워 평가에서 뺐습니다. 사람 검토가 필요합니다.
- 대본 전달도 채점이 없어 `content_delivery`는 늘 `NOT_EVALUABLE`이고(내용 규칙은 평가하지 않음), 이어진 Take가 없어 반복 `PRIORITY` 비율은 재지 않았습니다. 사용자 평가 기준이 있는 Take도 없습니다.

**규칙 단계** (정답이 있는 칸만)

| 잡음 | Take | 차원 상태 일치 (칸) | 1순위 일치 (Take) | 미션 판정 일치 | 거짓 강점 / Take |
|---|---|---|---|---|---|
| 전체 | 48 | 0.992 (252) | 0.933 (45) | 1.0 (6) | 0 |
| clean | 16 | 0.988 | 0.933 | 1.0 | 0 |
| noisy | 16 | 1.0 | 0.867 | 1.0 | 0 |
| harsh | 16 | 0.988 | 1.0 | 1.0 | 0 |

틀린 곳: 09번 clean의 긴 침묵(부담 3.2 → `STABLE`), 09번 harsh의 대본 응시(잡음으로 부담 7.9 → `PRIORITY`), 1순위가 04번 noisy(믿을 수 없는 시선이 `PRIORITY`) · 16번 noisy(센서가 나쁠 때의 속도가 1위)에서 어긋남.

**LLM 단계** (48번)

| 지표 | 값 |
|---|---|
| 스키마 유효율 | 0.938 (실패 3번 모두 `LLM_TIMEOUT`) |
| 입력에 없는 숫자 (숫자 검사 전) | 0 / 416 |
| 숫자 검사에 걸린 문장 | 0 / 330 |
| 출력 길이 | 평균 633자 · 최대 856자 (JSON) |
| 지연 | 캐시 없이 새로 부른 45번 중앙값 7.6초 · 최대 24.6초. 12초 제한 + 재시도 1번이라 24초 안팎은 시간 초과다 |

지금 모델은 리뷰 한 번에 8초 안팎이 걸려 호출 한 번 12초 제한(설계 8절)에 걸리는 Take가 있습니다(48번 중 3번, 잡음마다 1번씩).

**`config.py`에서 고른 값**: `priority_burden_min` 10 → 6. 나머지(허용치 · 한 걸음 폭 · 점수 · 가중)는 정답이 없거나 적어(미션 6개, 점수 정답 없음) 그대로 두었습니다.

| `priority_burden_min` | 차원 상태 일치 | 1순위 일치 | 정답이 없는 칸의 `PRIORITY` |
|---|---|---|---|
| 3 | 0.996 | 0.956 | 6 (15번의 작은 대본 응시까지) |
| 4 | 0.992 | 0.933 | 6 |
| 5 · 6 | 0.992 | 0.933 | 5 |
| 7 | 0.988 | 0.911 | 5 |
| 8 | 0.972 | 0.822 | 5 |
| 10 (이전 값) | 0.972 | 0.822 | 4 |

10이면 시간 초과 · 긴 침묵 · 짧은 대본 응시(부담 7 안팎)가 `STABLE`로 빠졌습니다. 5와 6이 같은 결과라, 반복 차원이 `IMPROVED`로 내려오는 기준이기도 한 이 값을 덜 엄격한 6으로 정했습니다(규칙 단계만, `--rules-only`로 잼).
