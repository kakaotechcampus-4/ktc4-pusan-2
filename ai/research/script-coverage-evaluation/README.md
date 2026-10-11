# script-coverage-evaluation (대본 전달도)

발표자가 슬라이드 대본의 내용을 **실제로 얼마나 전달했는지** 채점하는 기능입니다.
대본을 한 번 분석해 슬라이드별 **평가 기준(rubric)** 을 만들어 두고, 발표 연습마다 음성 인식 결과(STT)를 그 기준과 비교해
**대본 문장 · Key Point · 핵심 수치** 단위로 판정합니다.

| | |
|---|---|
| 담당 | jewon-kim |
| 기능 버전 | `1.0` (`src/script_coverage/version.py`의 `FEATURE_VERSION`), 평가 기준 형식 `1.1` |
| 검증한 LLM | OpenAI 호환 API, 모델 `openai/gpt-5.6-luna` |
| 상태 | v1. **가상 데이터로만 측정했습니다.** 실제 Deepgram 결과와 실제 발표자의 말투로는 아직 측정하지 않았습니다 |
| 배포 | 코어(`src/script_coverage/`)는 나중에 AI 서버로 그대로 옮깁니다 → [DEPLOY.md](DEPLOY.md) |

> 이 프로젝트는 `ai/archive/workspaces/jewon-kim/script-coverage-evaluation/v1/local/`의 노트북 2개를 구조만 바꿔 옮긴 것입니다.
> 판정 로직과 프롬프트는 같고, 같은 LLM 응답 캐시로 돌리면 결과가 바이트 단위로 같습니다 ([옮긴 뒤 검증](#옮긴-뒤-검증)).

## 목차

1. [이 기능이 하는 일](#1-이-기능이-하는-일)
2. [코드 구조 한눈에 보기](#2-코드-구조-한눈에-보기)
3. [폴더와 파일](#3-폴더와-파일)
4. [실행 흐름 따라가기](#4-실행-흐름-따라가기)
5. [실행법](#5-실행법)
6. [코드 읽는 순서와 자주 묻는 것](#6-코드-읽는-순서와-자주-묻는-것)
7. [입출력](#7-입출력)
8. [버전별 결과표](#8-버전별-결과표)
9. [배포](#9-배포)
10. [설계 결정](#10-설계-결정)
11. [알려진 한계](#11-알려진-한계)
12. [가상 데이터와 정답 라벨](#12-가상-데이터와-정답-라벨)
13. [트러블슈팅](#13-트러블슈팅)
14. [원본](#14-원본)

---

## 1. 이 기능이 하는 일

| 단계 | 하는 일 | 언제 |
|---|---|---|
| 1 | 대본을 읽고 **평가 기준**을 만든다 | 대본을 등록 · 수정할 때 한 번 |
| 2 | 발표 STT를 평가 기준과 비교해 **채점**한다 | 연습할 때마다 |
| 3 | 판단을 보류한 곳을 발표자에게 물어 **다시 계산**한다 | 리뷰 화면에서 사용자가 답할 때 |
| 4 | 사람이 적은 정답과 비교해 **정확도**를 잰다 | 연구할 때만 (배포에는 없음) |

```
[대본 JSON] ──▶ 1. 평가 기준 만들기 (슬라이드당 LLM 3회) ──▶ 평가 기준
                                                             │
[슬라이드별 STT] ──▶ 2. 채점 (슬라이드당 LLM 1회 + 필요할 때 1회) ◀─┘
                                 │
                                 ▼
                 평가 결과 + 비슷한 말 위치 ──▶ (리뷰 agent) ──▶ 사용자 확인
                                 ▲                                    │
                                 └──── 3. 다시 계산 (LLM 없음) ◀───────┘
```

**역할 분담 원칙**: 숫자 · 이름 · 문장 짝짓기 · 점수처럼 규칙으로 확실히 정할 수 있는 것은 코드가 하고,
어떤 문장이 핵심인지, 말한 내용이 그 문장의 뜻을 전달했는지처럼 의미를 이해해야 하는 것은 LLM이 합니다.
LLM은 판정만 하고 **점수는 매기지 않습니다.**

**하는 것**

- 대본으로 평가 기준을 만든다: 문장별 역할, 핵심 주장, Key Point(중요도 · 근거 문장), 핵심 사실(숫자 · 이름), 전달 단위
- STT를 전달 단위마다 판정하고(말함 / 흐림 / 없음 / 다름), 코드가 문장 · Key Point 판정으로 모은다
- 핵심 수치를 **값으로** 비교한다. `42%`와 `사십이 퍼센트`, `오후 6시`와 `오후 여섯 시`를 같은 값으로 본다
- 발음이 비슷한 다른 말(`노쇼` → `노조`, `삼십 분` → `사십 분`)은 **판단을 보류**한다. 점수에서 빼고 위치만 남긴다
- 사용자 답(대본대로 말함 / 들린 대로 말함)으로 판정과 점수를 다시 계산한다
- 점수 3개(내용 전달 · 수치 정확도 · 대본 충실도)를 코드가 계산한다

**하지 않는 것**

- 음성 인식 (Deepgram 결과를 입력으로 받는다), 슬라이드 경계 추정 (슬라이드별 STT를 받는다고 전제)
- 발표자 실수인지 인식 오류인지 **최종 판단** (비슷한 말은 위치와 함께 넘기고, 사용자 답이 오면 다시 계산만 한다)
- 전달력(말 속도 · 간투사 빈도 · 시선) 평가, 코칭 문구 생성, HTTP API · DB 저장 (배포는 [DEPLOY.md](DEPLOY.md))

---

## 2. 코드 구조 한눈에 보기

코드는 세 층이고, import는 위에서 아래로만 합니다.

```
experiments/ · reports/     실행하고 결과를 보는 곳 (사람이 돌림)
        │ import
src/coverage_lab/           실험 도구: LLM 연결 · 캐시 · DB · 지표   → research 에만 남음
        │ import
src/script_coverage/        코어: 실제 판정 로직                     → 나중에 AI 서버로 폴더째 복사
```

| 층 | 하는 일 | 배포 때 |
|---|---|---|
| 코어 `script_coverage` | 평가 기준 만들기 · 채점 · 다시 계산 | AI 서버로 그대로 옮김 |
| 실험 도구 `coverage_lab` | LLM 연결, 캐시, DB 저장, 정확도 측정 | 옮기지 않음. 서버 공통 코드와 BE가 같은 역할 |
| 실행 · 보고 `experiments` · `reports` | 사람이 돌리고 결과를 봄 | 옮기지 않음 |

### 코어의 규칙 세 가지

코어만 떼어 서버로 옮겨도 코드를 고칠 필요가 없도록 지키는 규칙입니다. `tests/unit/test_core_boundary.py`가 코드를 읽어 자동으로 검사하므로, 어기면 쓰는 도중에 테스트가 실패합니다.

| 규칙 | 이유 |
|---|---|
| 패키지 안에서는 **상대 import만** (`from ..shared.text import …`) | 폴더 이름이 `pitch_coach_ai.features.script_coverage`로 바뀌어도 고칠 import가 없다 |
| **파일 · DB · 네트워크 · 환경변수를 직접 쓰지 않음** | 서버는 상태를 두지 않는다. 저장은 BE, 키와 설정은 서버 공통 코드가 맡는다 |
| **LLM과 캐시는 인자로 받음** (의존성은 pydantic · kiwipiepy · scikit-learn뿐) | research는 SQLite 캐시와 `.env` 키를, 서버는 캐시 없이(`cache=None`) 자기 클라이언트를 넘긴다. 코어는 어느 쪽인지 모른다 |

### 그 밖에 정한 것

- **`script_analysis/`와 `stt_evaluation/`은 서로 import하지 않습니다.** 둘이 같이 쓰는 것은 `shared/`에만 둡니다. 한쪽을 고쳐도 다른 쪽에 숨은 영향이 없습니다.
- **`shared/rubric.py`가 두 단계 사이의 약속입니다.** 1단계가 만들고 2단계가 읽는 평가 기준의 모양을 한 곳에 둡니다. 이 모양의 의미가 바뀌면 기능 버전을 올립니다.
- **프롬프트는 `.txt`가 아니라 `.py` 상수입니다.** 프롬프트 · 출력 스키마 · 모델 이름은 캐시 키(설정 해시)에 들어갑니다. 텍스트 파일은 git이 줄바꿈을 바꿀 수 있어 해시가 달라지고, 그러면 캐시가 모두 무효가 되어 전부 다시 과금됩니다.
- **scikit-learn은 `1.8.x`로 고정했습니다.** v1 결과(TF-IDF 키워드)를 만든 버전입니다. 올릴 때는 캐시 재생 비교로 평가 기준이 같은지 먼저 확인합니다.
- **`stt_evaluation/fillers.py`는 일부러 떼어 뒀습니다.** `re`만 쓰므로 나중에 간투사 횟수를 세는 기능이 그대로 가져갈 수 있습니다. 지금 목록은 지워도 안전한 소리(`음 · 어 · 으 · 엄 · 흠 · 아 · 에`)뿐이고, `그러니까` · `이제` · `그` · `저`는 뜻이 있을 수 있어 남깁니다. BE `realtime/fillers.py`의 목록과는 다르며, 무엇을 셀지는 그 기능을 만들 때 정합니다.

---

## 3. 폴더와 파일

### 3-1. 전체 트리

```
script-coverage-evaluation/
├── README.md                      이 문서
├── INTERFACE.md                   코어의 입출력 (API 를 만들 때 기준)
├── DEPLOY.md                      배포 가이드
├── pyproject.toml · uv.lock       라이브러리 목록 (코어용 / research 전용 구분) · 버전 고정
├── .gitignore                     outputs/ 와 data/ 제외 (data/virtual/ 만 커밋)
│
├── src/script_coverage/           ── 코어 ──
│   ├── version.py                 기능 버전 · 평가 기준 형식 버전
│   ├── shared/                    두 단계가 같이 쓰는 도구
│   ├── script_analysis/           1단계: 대본 → 평가 기준
│   └── stt_evaluation/            2 · 3단계: STT → 채점, 사용자 확인 뒤 다시 계산
│
├── src/coverage_lab/              ── 실험 도구 (한 폴더) ──
│
├── experiments/                   01 ~ 06 실행 스크립트 (번호 = 실행 순서)
├── reports/                       보고용 노트북 2개 + results/*.json (지표)
├── data/virtual/                  가상 대본 2 · 연습 STT 18 · 정답 라벨 18
├── outputs/                       rubrics.sqlite (LLM 캐시 + 결과 DB, git 제외)
└── tests/                         unit (코어) · lab (실험 도구) · live (실제 API)
```

### 3-2. 코어 `src/script_coverage/`

**`shared/`: 두 단계가 같이 쓰는 도구**

| 파일 | 역할 | 주요 이름 |
|---|---|---|
| `text.py` | 글 정리(따옴표 · 공백)와 문장 나누기, 구간 계산 도우미 | `normalize_script`, `NormalizedScript` |
| `kiwi.py` | 형태소 분석기(Kiwi)를 처음 쓸 때 한 번만 준비, 형태소 경계 확인 | `get_kiwi`, `Morphemes` |
| `numbers.py` | 한국어 숫자 읽기: `1,240만` · `천이백사십만` · `사점육` → 값 | `parse_korean_number` |
| `facts.py` | 핵심 사실(퍼센트 · 금액 · 수량 · 날짜 · 시간 · 영문 이름) **뽑기**. 대본과 STT 양쪽에 씀 | `extract_critical_facts`, `CriticalFact` |
| `rubric.py` | **평가 기준의 모양**. 1단계가 만들고 2단계가 읽는 약속 | `EvaluationRubric`, `KeyPoint`, `ContentUnit`, `SCORE_WEIGHT` |
| `llm_step.py` | LLM 호출 공통 도구: 캐시 확인 → 없는 것만 동시에 호출 → 저장, 캐시 키 계산 | `llm_step`, `cache_get`, `cache_put`, `LLMCache` |

**`script_analysis/`: 1단계, 대본 → 평가 기준**

`core.py`의 `analyze_script()`가 아래 순서로 부릅니다.

| 순서 | 파일 | 하는 일 | LLM |
|---|---|---|---|
| ① | `shared/text.py` | 문장 나누기, 번호 붙이기 `[S0] [S1] …` | |
| ② | `keywords.py` | TF-IDF로 이 슬라이드에서 특히 자주 나오는 단어 고르기 | |
| ③ | `shared/facts.py` | 숫자 · 이름 뽑기 (규칙) | |
| ③ 동시에 | `semantic.py` | 문장 역할(핵심 주장 · 근거 · 세부)과 Key Point 정하기 | 1회 |
| ④ | `draft.py` | Key Point 중요도 정하기, 숫자를 Key Point에 연결, 이상한 점 경고 | |
| ⑤ | `final.py` | 지금까지의 결과와 경고를 보여 주고 최종 확인받기 | 1회 |
| ⑥ | `units.py` | 문장을 전달 단위로 쪼개 평가 기준에 붙이기 | 1회 |

| 함께 쓰는 파일 | 내용 |
|---|---|
| `schemas.py` | 입력(`SlideScript`)과 LLM 답 3종(`SlideSemanticAnalysis`, `FinalReview`, `SlideUnits`)의 모양 |
| `prompts/semantic.py` · `final.py` · `units.py` | LLM 지시문. 캐시 키에 들어가므로 함부로 바꾸지 않는다 |
| `config.py` | 경고 문구, 인용 매칭 기준 |

**`stt_evaluation/`: 2 · 3단계, STT → 채점 → 다시 계산**

`core.py`의 `evaluate_take()`가 아래 순서로 부릅니다.

| 순서 | 파일 | 하는 일 | LLM |
|---|---|---|---|
| ① | `normalize.py` + `fillers.py` | 간투사 · 말 반복 지우기, 문장 나누기 `[T0] [T1] …`, 원본 STT 위치 되찾기 | |
| ② | `align.py` | 대본 문장 ↔ STT 문장 짝짓기, 대본 충실도 | |
| ③ | `fact_check.py` | 뽑은 숫자가 STT에 맞게 나왔는지 **확인**, 틀린 원인 추정 | |
| ④ | `similar.py` | 발음이 비슷한 말(`노쇼` → `노소`) 찾기 → 판단 보류 | |
| ⑤ | `judge.py` | 전달 단위마다 말함 · 흐림 · 없음 · 다름 판정 | 슬라이드당 1회 |
| ⑥ | `merge.py` | 단위 → 문장 판정으로 모으기, 규칙과 LLM이 어긋나면 "충돌" 표시 | |
| ⑦ | `verify.py` | 충돌이 있는 슬라이드만 다시 판정 | 필요할 때 1회 |
| ⑧ | `merge.py` | 문장 → Key Point 판정으로 모으기 | |
| ⑨ | `scoring.py` | 점수 3개 계산 | |

| 함께 쓰는 파일 | 내용 |
|---|---|
| `confirm.py` | 사용자 답(대본대로 / 들린 대로)으로 판정 · 점수 다시 계산. LLM 없음 |
| `schemas.py` | STT 입력(`Take`), LLM 답, 채점 결과(`SlideEvaluation`, `SimilarItem`)의 모양 |
| `prompts/semantic.py` · `verifier.py` | LLM 지시문 |
| `config.py` | 판정 기준 숫자(임계값), 점수표 |

`shared/facts.py`는 사실을 **뽑고**, `stt_evaluation/fact_check.py`는 뽑은 사실이 STT에 맞게 나왔는지 **대조합니다.**

### 3-3. 실험 도구 `src/coverage_lab/`

| 파일 | 역할 |
|---|---|
| `paths.py` | 폴더 위치 모음 (데이터 · DB · 결과) |
| `llm.py` | `ai/.env`에서 키를 읽어 LLM 객체 만들기 (이미 있는 환경변수가 우선) |
| `cache.py` | LLM 답을 SQLite에 저장 · 조회. 예전 노트북과 같은 표라서 옛 캐시도 그대로 쓴다 |
| `store.py` | 평가 기준 · 채점 결과 · 비슷한 말 · 사용자 확인 저장. 배포에서 BE가 할 일을 대신한다 |
| `datasets.py` | 대본 · STT 파일 읽기 |
| `runs.py` | 코어를 돌리고 DB에 저장하는 연결 다리 (`run_script_analysis`, `run_take_evaluation`) |
| `results.py` | 지표를 `reports/results/*.json`으로 저장 · 읽기 |
| `rubric_report.py` · `rubric_quality.py` · `rubric_consistency.py` | 평가 기준 요약 표 · 품질 지표 · 같은 대본 반복 분석의 흔들림 |
| `stt_report.py` · `stt_labels.py` · `stt_checks.py` · `stt_stability.py` | 채점 결과 표 · 정답 라벨 비교 · 충돌 점검과 사용자 확인 뒤 정확도 · 같은 STT 반복 채점의 흔들림 |

이름 규칙: `rubric_*`는 1단계 결과용, `stt_*`는 2단계 결과용, 접두어가 없는 파일은 공통입니다.

### 3-4. 실행 스크립트 `experiments/`

번호가 실행 순서입니다. LLM 호출 수는 **캐시가 비어 있을 때** 기준이고, 같은 입력으로 다시 돌리면 0회입니다.

| 파일 | 하는 일 | LLM 호출 | 남기는 것 |
|---|---|---|---|
| `01_build_rubrics.py` | 대본 2개로 슬라이드별 평가 기준 만들기 | 약 60회 (20장 × 3) | DB `evaluation_rubrics` |
| `02_reanalyze_edit.py` | 대본 한 문장을 고치면 그 슬라이드만 다시 분석되는지 확인 | 최대 3회 | `outputs/가상대본2_수정.json` |
| `03_rubric_consistency.py` | 품질 지표 + 같은 대본을 3번 분석해 흔들림 측정 | 약 80회 | `reports/results/rubric_consistency.json` |
| `04_evaluate_takes.py` | 연습 18개 채점 | 약 225회 | DB `slide_evaluations` · `similar_items` |
| `05_accuracy.py` | 정답 비교, 충돌 조건 점검, 사용자 확인 뒤 정확도 | 0회 | `reports/results/stt_accuracy.json` |
| `06_stability.py` | 같은 STT를 3번 채점해 흔들림 측정 | 약 450회 | `reports/results/stt_stability.json` |

- `01`이 먼저입니다. `02`~`04`는 `01`의 평가 기준을, `05` · `06`은 `04`의 채점을 읽습니다.
- 비싼 반복 측정(`03`, `06`)은 `RUBRIC_CONSISTENCY_SAMPLES=1` · `STT_CONSISTENCY_SAMPLES=1`로 끌 수 있고, 끄면 결과 JSON을 덮어쓰지 않습니다.

### 3-5. 나머지

| 폴더 | 내용 |
|---|---|
| `reports/` | `script_analysis.ipynb` · `stt_evaluation.ipynb`는 DB와 JSON을 읽어 표로 보여 주기만 한다(계산 · LLM 없음). `results/*.json`은 핵심 지표 (기능 버전 · 모델 포함) |
| `data/virtual/` | `scripts/` 대본 2개(9장 · 11장), `stt/` 연습 18개(대본마다 9가지 상황), `stt_labels/` 문장별 정답 18개. 정답 라벨은 채점에 쓰지 않고 정확도를 잴 때만 쓴다 |
| `outputs/` | `rubrics.sqlite` 하나에 LLM 캐시와 모든 결과가 들어 있다. git에 올리지 않는다 |
| `tests/unit/` | 코어 테스트 (`shared/` · `script_analysis/` · `stt_evaluation/`)와 코어 규칙 검사(`test_core_boundary.py`). 코어와 함께 서버로 간다 |
| `tests/lab/` | 실험 도구 테스트: 캐시 표 대응, 저장, 실패해도 DB가 망가지지 않는지 |
| `tests/live/` | 실제 API로 슬라이드 1장을 처음부터 끝까지. 과금되므로 따로 실행 |

---

## 4. 실행 흐름 따라가기

가상대본1의 6번 슬라이드가 코드를 지나며 어떻게 바뀌는지 따라가 봅니다.

**대본** (`data/virtual/scripts/가상대본1.json`)

```
2025년 3월부터 8주 동안 제휴 도서관 3곳에서 시범 운영을 했습니다.
참여한 이용자는 1,860명이었고, 빈자리를 찾는 데 걸린 시간은 평균 23분에서 13분으로 약 42퍼센트 줄었습니다.
알림을 받고 10분 안에 자리를 잡은 비율은 76%였습니다.
노쇼 비율도 18%에서 7%로 낮아졌습니다.
시범 운영 뒤 설문에서 이용자의 85퍼센트 이상이 계속 쓰고 싶다고 답했습니다.
```

**발표 STT** (`data/virtual/stt/가상대본1_take9.json`, 음성 인식이 적은 그대로)

```
이천이십오 년 삼 월부터 팔 주 동안 제휴 도서관 세 곳에서 시범 운영을 했습니다 참여한 이용자는 천팔백육십 명이었고 …
노소 비율도 18%에서 7%로 낮아졌습니다 …
```

숫자를 한글로 적었고, `노쇼`를 `노소`로 잘못 알아들었습니다.

### 4-1. 평가 기준 만들기 (`experiments/01_build_rubrics.py`)

```
01_build_rubrics.py
├─ load_settings() · connect() · script_llms()        coverage_lab   키 읽기, DB 열기, LLM 객체 3개
└─ 대본 파일마다 run_script_analysis()                 coverage_lab/runs.py
   ├─ load_script_json()                              대본 JSON → 슬라이드 목록
   ├─ analyze_script()                                script_coverage/script_analysis/core.py  ← 코어
   └─ save_rubric()                                   DB evaluation_rubrics 에 저장
```

| 단계 | 결과 (예시 슬라이드) |
|---|---|
| ① 문장 나누기 | `[S0] 2025년 3월부터 … 운영을 했습니다.` `[S1] 참여한 이용자는 …` … 5문장 |
| ② 키워드 | `시범`, `시범 운영`, `운영`, `비율`, `퍼센트` |
| ③ 숫자 · 이름 (규칙) | `CF1 2025년 3월 → 2025-03`, `CF2 8주`, `CF3 3곳`, `CF4 1,860명`, `CF5 평균 23분 → 23분` … |
| ③ 문장 역할 (LLM) | S0 세부 · S1 핵심 주장 · S2~S4 근거 |
| ④ 코드로 정리 | `KP1 보통: 시범 운영 기간 · 장소 (S0, CF1~CF3)`, `KP2 핵심: 탐색 시간 23→13분, 42% (S1)`, `KP3 높음: 10분 안에 76% (S2)` … |
| ⑤ 최종 확인 (LLM) | ①~④와 경고를 보여 주고 확정 |
| ⑥ 전달 단위 (LLM) | `S0-U1 2025년 3월부터 시범 운영함`, `S0-U2 8주 동안 시범 운영함`, `S0-U3 제휴 도서관 3곳에서 시범 운영함` … |

중요도는 문장 역할로 정합니다. 핵심 주장 문장의 Key Point는 critical, 근거 문장은 high, 나머지는 normal입니다.

> **캐시가 끼어드는 자리**: LLM을 부르기 직전마다 `shared/llm_step.py`가 "같은 질문을 전에 한 적 있나?"를 DB에서 찾아봅니다.
> - "같은 질문"은 같은 입력 · 프롬프트 · 출력 모양 · 모델입니다.
> - 있으면 저장된 답을 쓰고 LLM을 부르지 않습니다. 두 번째 실행부터는 0원입니다.

### 4-2. 채점 (`experiments/04_evaluate_takes.py`)

```
04_evaluate_takes.py
├─ load_settings() · connect() · stt_llms()           키 읽기, DB 열기, LLM 객체 2개
└─ 연습마다 run_take_evaluation()                      coverage_lab/runs.py
   ├─ load_rubrics()                                  DB 에서 그 대본의 평가 기준 꺼내기
   ├─ evaluate_take()                                 script_coverage/stt_evaluation/core.py  ← 코어
   └─ save_evaluation()                               DB slide_evaluations · similar_items 에 저장
```

| 단계 | 결과 (예시 슬라이드) |
|---|---|
| ① STT 다듬기 | 간투사 · 반복을 지우고 `[T0] [T1] …`로 나눔 |
| ② 문장 짝짓기 | `S0 ↔ T0`, `S1 ↔ T1` … |
| ③ 숫자 확인 | `이천이십오 년 삼 월` → 2025-03 = CF1 일치, `천팔백육십 명` → 1,860명 = CF4 일치 |
| ④ 비슷한 말 | `노쇼` → `노소` 발견 → 판단 보류 (점수에서 빼고 원본 STT 위치 기록) |
| ⑤ 단위 판정 (LLM) | `S0-U1 말함`, `S0-U2 말함`, `S0-U3 말함` … (근거: T0) |
| ⑥ 모으기 · 충돌 | 단위가 모두 "말함"이라 S0은 전달(said). 규칙과 어긋난 문장 없음 |
| ⑦ 교차 검증 | 충돌이 없어서 건너뜀 |
| ⑧ Key Point | KP1 · KP2 · KP3 모두 전달(covered) |
| ⑨ 점수 | 내용 전달 1.0 · 수치 정확도 1.0 · 대본 충실도 1.0 · 비슷한 말 1개 |

충돌의 예: LLM은 "말함"이라고 했는데 규칙이 그 숫자를 STT에서 못 찾았다면(`fact_missing`), 그 문장을 LLM에 한 번 더 보여 줍니다.

### 4-3. 다시 계산 (사용자 확인)

리뷰 화면에서 발표자에게 묻습니다. "여기서 '노쇼'라고 하셨나요, '노소'라고 하셨나요?"

```
store.confirm_similar_item()                 답 저장
└─ confirm.rescore_evaluation()              script_coverage/stt_evaluation/confirm.py  ← 코어 (LLM 없음)
   → DB confirmed_evaluations 에 저장 (처음 채점은 그대로 둔다)
```

| 답 | 뜻 | 결과 |
|---|---|---|
| `as_script` (대본대로 말함) | 음성 인식이 틀린 것 | 판정을 그대로 둔다 |
| `as_stt` (들린 대로 말함) | 발표자가 다른 말을 한 것 | 그 단위를 "다름"으로 바꾸고 문장 → Key Point → 점수를 다시 모은다 |

### 4-4. 정확도 재기 (`experiments/05_accuracy.py`, `06_stability.py`)

- `05_accuracy.py`: `stt_labels.py`가 채점 결과를 사람이 적은 정답(`data/virtual/stt_labels/`)과 비교합니다. `stt_checks.py`는 정답을 사용자 답 대신 넣어 5-3을 돌려 봅니다.
- `06_stability.py`: 같은 STT를 3번 채점해 판정이 흔들리는지 봅니다.
- 결과는 `results.py`가 `reports/results/*.json`으로 저장하고, 보고용 노트북이 표로 보여 줍니다.

---

## 5. 실행법

### 준비

```bash
cd ai/research/script-coverage-evaluation
uv sync                      # Python 3.12, 라이브러리 + 개발 도구 설치
```

`ai/.env.example`을 `ai/.env`로 복사해 채웁니다.

| 변수 | 필수 | 뜻 |
|---|---|---|
| `OPENAI_API_KEY` | 예 | API 키 |
| `OPENAI_MODEL` | 예 | 모델 이름. 엔드포인트가 허용하는 이름이어야 합니다 (아니면 400). 검증한 값은 `openai/gpt-5.6-luna` |
| `OPENAI_BASE_URL` | 아니오 | OpenAI 호환 엔드포인트. 없으면 OpenAI 기본 주소 |

- `coverage_lab/llm.py`가 현재 폴더부터 위로 올라가며 처음 만나는 `.env`를 읽고, 없으면 프로젝트 폴더부터 위로 찾습니다. 보통 `ai/.env`가 잡힙니다.
- 이미 설정된 환경변수는 `.env`가 덮어쓰지 않습니다.
- **mock 모드는 없습니다.** LLM 단계는 항상 실제 API를 부르고, 캐시에 없는 호출은 과금됩니다.
- 모델 이름도 캐시 키에 들어갑니다. 캐시를 재사용하려면 캐시를 만든 때와 같은 `OPENAI_MODEL`이어야 합니다.

**Windows 참고**: 이 PC에서는 `pytest.exe` · `jupyter-*.exe` 실행 파일이 앱 제어로 막혀 있어 `uv run python -m …` 형태로 부릅니다. 콘솔 한글이 깨지면 `PYTHONIOENCODING=utf-8`을 설정하세요.

### 캐시 재사용 (API를 부르지 않고 시작하기)

LLM 응답 캐시는 `outputs/rubrics.sqlite`에 쌓이고, 아카이브 노트북과 **같은 표 · 열**을 씁니다. 노트북을 돌려 본 사람은 그 DB를 가져와 쓸 수 있습니다.

```bash
mkdir -p outputs
cp ../../archive/workspaces/jewon-kim/script-coverage-evaluation/v1/local/outputs/rubrics.sqlite outputs/rubrics.sqlite
```

`outputs/`는 git에 없어서, 아카이브 노트북을 실제로 실행한 사람의 로컬에만 있습니다. 없으면 처음 한 번은 과금됩니다.

**과금 없이 돌리기**: `OPENAI_BASE_URL`을 닿지 않는 주소로 두면 캐시에 있는 호출만 성공하고, 캐시에 없는 호출은 실패해서 과금되지 않습니다.

```bash
# PowerShell
$env:OPENAI_BASE_URL = "http://127.0.0.1:9"; $env:OPENAI_API_KEY = "dummy"; $env:OPENAI_MODEL = "openai/gpt-5.6-luna"
uv run python experiments/01_build_rubrics.py
```

실패한 슬라이드는 평가 기준을 만들지 않고 `stats["failed"]`에 남습니다. 성공한 호출은 캐시에 남으므로, 나중에 제대로 된 주소로 다시 돌리면 실패한 것만 부릅니다.

### 실험 돌리기

`experiments/*.py`는 VS Code Interactive Window에서 셀 단위로 돌려도 되고, 아래처럼 끝까지 돌려도 됩니다. 순서와 비용은 [3-4](#3-4-실행-스크립트-experiments)의 표를 보세요.

```bash
uv run python experiments/01_build_rubrics.py
uv run python experiments/04_evaluate_takes.py
uv run python experiments/05_accuracy.py
```

프롬프트 · 출력 스키마 · 모델을 바꾸면 캐시 키가 바뀌어 전부 다시 부릅니다. 의도한 변경이 아니면 되돌리세요.

### 보고용 노트북

`reports/*.ipynb`는 DB와 `reports/results/*.json`을 표로 보여 줍니다. **로직이 없고 LLM을 부르지 않습니다.** 표시할 결과가 있으려면 위 실험을 먼저 돌려 두어야 합니다.

```bash
uv run python -m nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=3600 reports/script_analysis.ipynb
uv run python -m nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=3600 reports/stt_evaluation.ipynb
```

### 테스트

```bash
uv run python -m pytest                    # 기본: unit + lab (live 는 제외)
uv run python -m pytest tests/unit         # 코어만 (코어 규칙 검사 포함)
uv run python -m pytest tests/lab          # 실험 도구만
uv run python -m pytest -m live            # 실제 API 를 부르는 테스트만 (과금)
```

`tests/live/test_live_pipeline.py`는 가상대본1 슬라이드 6 한 장을 캐시 없이 평가 기준 만들기 → 채점까지 돌립니다(약 4~5회 호출). 캐시 재생으로는 확인되지 않는 경로, 곧 LLM 클라이언트를 만들어 실제로 부르는 경로를 확인합니다.

### 옮긴 뒤 검증

아카이브 노트북이 남긴 LLM 캐시를 새 코드로 재생해 비교했습니다 (LLM 호출 0회).

| 항목 | 결과 |
|---|---|
| 평가 기준 31개 (대본 2개 + 수정본) | 아카이브 결과와 바이트 단위로 같음 |
| 슬라이드 평가 180개 (연습 18개) | 같음 |
| 비슷한 말 · 사용자 확인 · 확인 뒤 평가 | 같음 |
| 정확도 · 안정성 지표 | 같음 |

코어를 고친 뒤에도 같은 방식(캐시 재생 → 결과 비교)으로, 평가 기준 · 평가 결과가 의도 밖으로 바뀌지 않았는지 확인할 수 있습니다.

---

## 6. 코드 읽는 순서와 자주 묻는 것

### 처음 읽는다면 이 순서로

1. `src/script_coverage/shared/rubric.py`: 평가 기준이 어떤 모양인지 봅니다. 모든 것의 중심입니다.
2. `src/script_coverage/script_analysis/core.py`: `analyze_script` 한 함수에 1단계 흐름이 다 보입니다.
3. `src/script_coverage/stt_evaluation/core.py`: `evaluate_take` 한 함수가 2단계 흐름입니다.
4. `src/script_coverage/stt_evaluation/schemas.py`: 채점 결과의 모양을 봅니다.
5. `src/coverage_lab/runs.py`: 코어와 DB가 어떻게 이어지는지 봅니다 (70줄 정도).
6. `experiments/01_build_rubrics.py`, `04_evaluate_takes.py`: 실제로 어떻게 돌리는지 봅니다.
7. 궁금한 단계가 생기면 그 단계 파일로 들어갑니다. 예: 숫자 비교가 궁금하면 `stt_evaluation/fact_check.py`

### 자주 묻는 것

**`core.py`가 왜 두 개인가요?**
단계마다 시작점이 하나씩 있기 때문입니다. `script_analysis/core.py`는 대본을 등록할 때 한 번, `stt_evaluation/core.py`는 연습할 때마다 실행됩니다.

**왜 LLM과 규칙을 둘 다 쓰나요?**
규칙은 숫자 비교처럼 정답이 분명한 일에 강하고 결과가 늘 같습니다. LLM은 "말을 바꿔 했지만 뜻은 같다" 같은 의미 판단에 강합니다. 둘이 어긋나는 곳(충돌)만 LLM에 다시 물어서 비용을 아끼면서 실수를 줄입니다.

**코어는 왜 파일이나 DB를 못 쓰게 했나요?**
서버로 옮기면 저장은 BE가 하고 캐시는 쓰지 않습니다. 코어가 저장 방법을 몰라야 어디로 옮겨도 그대로 동작합니다.

**무엇을 고칠 때 어디를 보나요?**

| 하려는 일 | 위치 | 주의 |
|---|---|---|
| 프롬프트 수정 | `*/prompts/*.py` | 캐시 키가 바뀌어 전부 다시 호출 (과금) |
| LLM 답의 항목 추가 | `*/schemas.py`의 LLM 스키마 | 위와 같음 |
| 판정 기준 숫자(임계값) | `stt_evaluation/config.py` | 바꾸기 전 지표를 먼저 기록해 두고 비교 |
| 판정 규칙 · 충돌 조건 | `stt_evaluation/merge.py` | |
| 점수 공식 | `stt_evaluation/scoring.py` | |
| 간투사 목록 | `stt_evaluation/fillers.py` | 채점 결과가 바뀜 |
| 새 지표 · 실험 | `coverage_lab/` + `experiments/` | 코어는 건드리지 않음 |
| 모델 · 키 | `ai/.env` | 모델 이름도 캐시 키에 들어감 |

---

## 7. 입출력

코어가 무엇을 받아 무엇을 돌려주는지는 **[INTERFACE.md](INTERFACE.md)**에 정리했습니다. API 요청 · 응답을 정할 때 기준이 되는 문서입니다.

| 내용 | INTERFACE.md |
|---|---|
| 진입점 3개(`analyze_script` · `evaluate_take` · `rescore_evaluation`)의 입력 · 출력 · 실패 처리와 실제 JSON 예시 | 1절 |
| 데이터 모델 필드: 입력, 평가 기준(`EvaluationRubric`), 평가 결과(`SlideEvaluation`), 판정 값의 뜻 | 2절 |
| 단계 모듈마다 주고받는 것 | 3절 |
| LLM에 보내는 것과 받는 스키마 5종 | 4절 |
| 버전 값과 호환 규칙 | 5절 |

다운스트림(BE · 리뷰 agent)과의 접점은 네 가지입니다: 평가 기준, 슬라이드 평가, 비슷한 말, 사용자 확인. 필드의 원본은 pydantic 모델이고, 문서와 코드가 다르면 코드가 맞습니다.

### research의 SQLite 표 (`coverage_lab/store.py`, `outputs/rubrics.sqlite`)

BE가 배포 환경에서 맡을 저장을 research 에서는 이 표들이 대신합니다. 표 이름 · 열은 아카이브 노트북의 DB와 같습니다.

| 표 | 키 | 내용 |
|---|---|---|
| `evaluation_rubrics` | (대본 이름, 슬라이드) | 평가 기준 JSON + 내용 해시 |
| `slide_evaluations` | (연습, 슬라이드) | 평가 결과 JSON |
| `similar_items` | (연습, 슬라이드, 항목) | 비슷한 말 한 행씩 |
| `similar_confirmations` | (연습, 슬라이드, 항목) | 사용자 답 + 항목 표현 |
| `confirmed_evaluations` | (연습, 슬라이드) | 사용자 확인으로 다시 계산한 평가 결과 |
| `semantic_cache` · `semantic_samples` · `final_cache` · `unit_cache` · `stt_llm_cache` | 입력 해시 + 설정 해시 | LLM 응답 캐시 (`coverage_lab/cache.py`). 호출 종류와 표의 대응은 그 파일 docstring |

`CREATE TABLE IF NOT EXISTS`로 만들기 때문에 컬럼을 바꾸면 기존 표를 지워야 합니다. `similar_items` · `slide_evaluations` · `confirmed_evaluations`는 다시 채워지는 파생 데이터라 지워도 되지만,
`similar_confirmations`는 사용자 답이라 지우지 마세요. Windows에서 DB 파일이 안 지워지면 커널(노트북 · Interactive Window)이 연결을 잡고 있는 것이니 먼저 종료합니다.

---

## 8. 버전별 결과표

> **모두 가상 데이터 기준입니다.** 가상 STT와 정답 라벨을 같은 작성자가 만들어 실제보다 쉬울 수 있고, 규칙 임계값 일부는 같은 데이터를 보고 정했습니다.
> 실제 Deepgram 출력과 실제 발표자로는 측정하지 않았습니다. LLM 판정의 실제 정확도는 아직 검증되지 않았습니다.
> 규칙 부분(수 파서, 수치 검증, 비슷한 말 위치)은 결정적이고 가상 데이터 전체에서 정답과 일치했습니다.

### v1 (기능 버전 `1.0`, 모델 `openai/gpt-5.6-luna`)

데이터: 대본 2개(슬라이드 20장, Key Point 80개), 연습 18번(대본당 9개 시나리오), 대본 문장 765개. 숫자는 `reports/results/*.json` 입니다.

**① 대본 분석** (`rubric_consistency.json`)

| 지표 | 값 |
|---|---|
| 중요도 분포 critical / high / normal | 19 / 40 / 21 |
| Key Point에 연결된 수치 사실 | 57 / 57 |
| 전달 단위 (나눈 문장당 평균) | 164 (1.93) |
| 검증 경고 (1차 → 최종 결론 후) | 8 → 2 |
| 같은 대본 3번 분석: 문장 역할 일치율 · Key Point 대응률 (최종) | 0.84 · 1.0 |
| 같은 대본 3번 분석: 이름 · 용어 일치율 (1차 → 최종) | 0.92 → 0.77 |
| 같은 가상 발화의 점수 차이 (100점, 평균 · 상위 10%) | 4.3 · 12.5 |

**② STT 평가** (`stt_accuracy.json`, `stt_stability.json`)

| 지표 | 규칙만 | LLM 1차 | 최종 |
|---|---|---|---|
| 대본 문장 판정 정확도 (765개) | 0.848 | 0.970 | **0.992** (3번 채점 0.988 ~ 0.992) |
| Key Point 판정 정확도 (720개) | 0.846 | 0.967 | **0.992** |
| 심각한 오판 (전달 ↔ 누락 · 모순) | 1.9% | 2.4~2.6% | **0.4%** (3번 채점 0.4 ~ 0.6%) |

| 지표 | 값 |
|---|---|
| 핵심 사실 정확도 (738개, 보류 18개 제외) | 0.999 ~ 1.0 (영문 이름 1개를 LLM 이름 확인이 채점마다 다르게 봄) |
| 라벨의 인식 오류 중 보류한 비율 | 1.0 |
| 보류한 비슷한 말 29개 중 실제 발표자 실수 | 3개 (사용자 확인으로 가려냄) |
| 슬라이드 내용 점수 평균 오차 (0~1) | 1차 0.044 → 최종 0.011 |
| 같은 STT 3번 채점: 문장 · Key Point 판정 일치율 | 0.992 · 0.992 |
| 같은 STT 3번 채점: 슬라이드 점수 최대 흔들림 | 60점 (발표 전체는 내용 3.8점 / 수치 4.8점) |

**사용자 확인 뒤** (정답 라벨로 사용자 답을 대신해 다시 계산, LLM 호출 없음)

| 지표 | 처음 채점 | 사용자 확인 뒤 |
|---|---|---|
| 대본 문장 판정 정확도 | 0.992 | **0.996** |
| Key Point 판정 정확도 | 0.992 | **0.996** |
| 심각한 오판 | 0.4% | **0%** |
| 슬라이드 내용 점수 평균 오차 | 0.011 | **0.002** |
| 보류한 사실 | 18개 | 0개 |

**대본별 일반화** — 판정 규칙을 다듬을 때 대본1의 오답만 보고 대본2는 검증용으로 남겼습니다 (문장 정확도, 최종).

| | 0번 | 1번 | 2번 |
|---|---|---|---|
| 대본1 (규칙을 다듬을 때 본 데이터) | 0.997 | 0.995 | 0.995 |
| 대본2 (보지 않은 데이터) | 0.987 | 0.985 | 0.982 |

대본2에서 1%p 정도 낮습니다. 새 데이터에서는 이 정도 낮아질 수 있다고 보세요. 시나리오별로는 충실 · 의역 · 누락 · 인식오류 · 더듬기 · 발음이 1.0에 가깝고,
요약형(take8)이 가장 낮습니다.

**결과 읽을 때** — 정확도 차이 ±0.004는 채점마다 달라지는 폭(2~3문장)과 비슷해 잡음입니다. 같은 데이터로 방식을 비교할 때는 이 폭을 넘는 차이만 봅니다.

### 새 버전을 올릴 때

출력 의미가 바뀌어 `FEATURE_VERSION`을 올렸다면 위 표 아래에 그 버전의 표를 추가합니다. 실제 Deepgram 데이터로 측정하면 가상 데이터 결과와 분리해서 적습니다.
v1을 "완료"라고 부르려면 실제 슬라이드별 Deepgram 결과와 녹음 5~10개에 정답 라벨(`data/virtual/stt_labels/` 형식)을 만들어
`05_accuracy.py` → `06_stability.py`를 돌려야 합니다.

---

## 9. 배포

AI 서버에 올리는 데 필요한 작업(서버 · 인프라 · BE 연동)과 순서, API, 비용, 결정해야 할 것은 [DEPLOY.md](DEPLOY.md)에 있습니다.

---

## 10. 설계 결정

| 결정 | 이유 |
|---|---|
| LLM은 전달 단위를 판정하고, 문장 · Key Point는 코드가 모음 | Key Point를 통째로 판정하면 "몇 문장을 말해야 일부 전달인가"가 매번 흔들렸습니다. 문장 단위로 바꾸자 요약형 발표 정확도 0.887 → 0.963. 단위 판정 → 문장 → Key Point로 모으는 규칙은 데이터가 바뀌어도 같습니다 |
| 규칙과 LLM을 독립적으로 돌리고, 어긋난 곳만 다시 판정 | LLM에 규칙 결과를 주면 끌려갑니다. 독립 판정을 비교하면 한쪽의 실수를 찾을 수 있고, 다시 판정하는 비용은 충돌한 곳에만 듭니다 (연습 슬라이드의 약 25%) |
| 규칙이 확실히 아는 것은 LLM 판정을 보정 | 이 문장에만 있는 수치 · 이름을 STT에서 찾았으면 그 단위는 "전혀 없음"일 수 없습니다 (`raised_by_rule`). LLM이 단위를 엄격하게 볼 때의 "일부 전달 → 빠짐" 오답을 막습니다 |
| 두 판정 방식을 함께 쓰지 않음 (시도 후 제외) | 문장 전체 판정과 전달 단위 판정이 어긋나면 교차 검증하는 방식을 재 봤는데, 검증자가 1차의 오답을 바로잡는 만큼 맞은 것을 망쳐 평균 0.990 → 0.988 이고 호출은 2배였습니다 |
| 전달 단위 방식을 기본으로 | 정확도는 문장 단위 판정과 같지만(0.990), 일부 전달의 경계를 코드가 정해 라벨 기준과 구조적으로 맞고, 리뷰 agent가 빠진 정보를 단위로 짚을 수 있고, 호출 수는 같습니다 |
| 근거 → 이유 → 판정 순서, 근거 번호는 코드가 확인 | 판정을 먼저 정하고 근거를 끼워 맞추지 않게 합니다. 근거 문장에 그 대본 문장의 내용이 있는지도 봅니다 (`evidence_unrelated`) |
| 충돌 조건은 1차 판정마다 "맞다면 규칙에서 보여야 할 것"으로 정의하고 발동을 기록 | 조건이 `said` · `missing` 쪽에만 있으면 `partial`은 검사되지 않습니다. 조건별 발동 · 단독 발동 · 바로잡음을 남겨 쓸모없는 조건을 가려냅니다 |
| 점수는 코드가 계산 | LLM이 점수를 매기면 같은 판정도 스케일이 흔들립니다. 틀린 말(`contradicted`)은 빠뜨린 것(`missing`)보다 낮게 봅니다 — 청중에게 잘못된 정보를 준 것입니다 |
| 수치는 문자열이 아니라 값으로 비교 | STT는 숫자를 들리는 대로 적습니다(`사십이 퍼센트`). 한글 수 파서를 넣자 STT 수치 검증 정확도 74.6% → 100% |
| 발음이 비슷한 말은 판단 보류 | 텍스트만으로는 발표자 실수와 인식 오류를 가릴 수 없습니다. LLM 에게 원인을 판정시켜도 규칙 추정보다 낫지 않았고, 수치는 억울한 감점이 생겼습니다. 그래서 비율에서 빼고 위치를 넘깁니다 |
| 보류한 말은 사용자 확인으로 코드가 다시 계산 | 발표자에게 물어야 아는 것은 묻습니다. 다시 계산은 단위 판정을 바꾸고 코드로 모으기만 하면 되어 LLM을 다시 부르지 않고, 같은 답이면 늘 같은 점수가 나옵니다 |
| STT 정규화는 규칙 (LLM으로 다시 쓰지 않음) | LLM이 문맥에 맞게 고쳐 쓰면(`노조` → `노쇼`) 인식 오류도 발표자 실수도 사라지고, 원본 위치 대응이 깨지고, 결과가 흔들립니다 |
| 대본 분석은 1차 분석 + 최종 결론 2회 | 최종 결론이 검증 경고를 줄였습니다 (8 → 2). 다만 분석 결과의 흔들림(일관성)은 줄이지 못했습니다 |
| 평가 기준은 대본 등록 때 한 번 만들어 저장 | 같은 대본의 연습끼리 같은 기준으로 비교됩니다. 발표할 때마다 대본을 다시 분석하지 않습니다 |

### 점수

LLM은 점수를 매기지 않습니다. 모두 `stt_evaluation/scoring.py`가 계산하고, 발표 전체 점수는 슬라이드 점수의 가중 평균(개수는 합)입니다.

| 점수 | 계산 |
|---|---|
| `content_coverage` | Σ(Key Point 판정 점수 × 중요도 가중치) / Σ(가중치). `covered` 1 / `partial` 0.5 / `missing` 0 / `contradicted` −0.5, 가중치 critical 3 / high 2 / normal 1. 음수면 0 |
| `critical_fact_accuracy` | Σ(사실 점수 × 가중치) / Σ(가중치). `matched` 1, `approximate` 0.5, 나머지 0. **`sound_alike`는 분자 · 분모에서 뺌** |
| `script_fidelity` | 순서를 지킨 형태소 일치의 recall · precision 조화평균. **비슷한 말 자리의 형태소는 양쪽에서 뺌** |
| `similar_words` / `similar_numbers` | 비율에서 뺀 비슷한 단어 · 수치의 개수 |

임계값은 가상 STT와 정답 라벨로 맞춘 값이라(`stt_evaluation/config.py`) 바꾸면 채점 결과가 달라집니다. 바꾸기 전에 기준값을 고정한 채 먼저 재고, 그다음에 조정하세요.

---

## 11. 알려진 한계

- **실제 데이터로 측정하지 않았습니다.** 실제 Deepgram 출력의 문장 분리, 인식 오류 양상, 실제 발표자의 말투는 가상 데이터와 다를 수 있습니다.
- **요약형 발표의 경계**가 가장 흔들립니다. 세부를 줄여 요약한 말을 `vague`로 보는 경우가 있어, 채점마다 판정이 바뀐 Key Point 6개 중 5개가 요약형(take8)입니다.
- **충돌로 잡지 못하는 1차 오답**이 한 번의 채점에 3개 안팎 남습니다 (주로 요약형의 `said` ↔ `partial`).
- **검증용 대본에서 1%p 정도 낮습니다.** 새 데이터에서는 기준값을 고정한 채 먼저 재고 조정하세요.
- **한 슬라이드가 채점마다 `covered` ↔ `contradicted`를 오갈 수 있습니다** (슬라이드 점수 최대 60점, 발표 전체는 최대 3.8점).
  리뷰 agent는 슬라이드 점수 숫자보다 문장 판정과 근거 문장을 보고 말해야 합니다.
- **발음이 비슷한 발표자 실수**(`18%` → `28%`)는 사용자가 확인하기 전까지 점수에 반영되지 않습니다.
- **대본 분석의 흔들림** — 같은 대본을 다시 분석하면 Key Point 묶음과 이름 · 용어 사실이 조금 달라집니다(일치율 0.77).
  평가 기준은 한 번 만들어 저장하므로 같은 대본의 연습끼리는 일관되지만, 기준을 다시 만들면 점수가 달라질 수 있습니다.
- **영문 이름의 인식 오류**(`Prophet` → `profit`)는 비슷한 말로 잡지 않고 LLM 이름 확인에 맡깁니다.
- **간투사 목록이 짧습니다.** `그`, `저` 같은 말은 뜻이 있을 수 있어 지우지 않습니다 (더듬기 연습에서 `그`가 14~17번 남음).
  간투사를 세는 기준은 필러 카운트 기능을 만들 때 정합니다 (`fillers.py` 설명 참고).
- **대본 형식** — 무대 지시문(괄호 안 동작 설명 등)이 없는 대본을 전제합니다.

---

## 12. 가상 데이터와 정답 라벨

실제 발표 대본과 녹음은 공개 저장소에 넣지 않습니다. `data/virtual/`의 모든 데이터는 팀이 만든 가상 데이터입니다.

- **대본** — `가상대본1`(도서관 좌석 예측 서비스, 9장), `가상대본2`(동네 빵집 재고 예보, 11장). 형식은 `[{"slide_number": 1, "script": "…"}, …]`
- **STT** — 대본마다 9개 시나리오: 충실 · 의역 · 누락 · 실수 · 혼합 · 인식오류 · 더듬기 · 요약 · 발음 (`take1` ~ `take9`). 형식은
  `{"script_name", "take_id", "scenario"(선택), "slides": [{"slide_number", "stt"}]}`.
  문장부호가 거의 없고, 간투사 · 말 반복 · 띄어쓰기 오류, 한글로 읽은 숫자, 발음대로 적은 영문 이름이 섞여 있습니다.
- **정답 라벨** (`stt_labels/<take_id>.json`) — 대본 **문장마다** 발표자가 **실제로 말한 것** 기준으로 적습니다. 평가 파이프라인은 라벨을 보지 않고 `coverage_lab/stt_labels.py`가 비교할 때만 읽습니다.

| `status` | 기준 |
|---|---|
| `verbatim` / `paraphrased` | 문장의 정보(주장 · 사실 · 수치 · 나열 항목)를 모두 말함. 꾸미는 말을 빼도 전달 |
| `partial` | 일부만 또는 흐리게 말함 (나열 항목 일부, 수치를 `많이` · `넘게`로, 이름만 말하고 내용 빠짐) |
| `missing` | 이 문장의 정보를 하나도 말하지 않음. 같은 주제나 앞뒤 문장의 내용만 말한 것도 `missing` |
| `contradicted` | 다른 값 · 반대 내용. 틀렸다가 바로 고친 것은 고친 말 기준 |

보조 필드: `dropped_values`(빠뜨린 값), `changed_values`(실제로 틀리게 말한 값), `asr_errors`(맞게 말했지만 인식이 다르게 적은 값 — 문장은 `verbatim` 등), `approximated_values`, `additions`, `note`.
**라벨은 채점과 같은 기준으로 적어야** 정확도가 의미 있습니다. 새 데이터의 라벨도 이 기준을 따르세요.

---

## 13. 트러블슈팅

| 증상 | 원인 · 해결 |
|---|---|
| `RuntimeError: .env에 OPENAI_API_KEY / OPENAI_MODEL이 없다` | `ai/.env`를 만들었는지, 키와 모델이 채워져 있는지 확인. 메시지에 읽은 파일 경로가 나옵니다 |
| LLM 호출이 400으로 실패 | 엔드포인트가 허용하지 않는 모델 이름. `OPENAI_MODEL` 확인 |
| 실행했더니 API가 많이 불림 | 프롬프트 · 스키마 · 모델 이름이 캐시를 만든 때와 다르거나 캐시(`outputs/rubrics.sqlite`)가 없습니다. 의도한 게 아니면 되돌리거나 아카이브 캐시를 복사 |
| `sqlite3.OperationalError: table … has N columns` | 표 컬럼을 바꿨는데 예전 표가 남아 있음. 해당 표를 지우고 다시 실행 (`similar_confirmations`는 지우지 말 것) |
| STT 쪽에서 평가 기준이 없다는 오류 | 평가 기준이 먼저 있어야 합니다. `01_build_rubrics.py`를 먼저 실행 |
| 콘솔에 한글이 깨짐 (Windows) | `PYTHONIOENCODING=utf-8` |
| `pytest.exe` · `jupyter-*.exe`가 실행되지 않음 (Windows 앱 제어) | `uv run python -m pytest`, `uv run python -m nbconvert` 처럼 모듈로 실행 |
| 반복 측정(`06_stability.py` · `03_rubric_consistency.py`)이 오래 걸리고 호출이 많음 | 검증용입니다. `STT_CONSISTENCY_SAMPLES=1`, `RUBRIC_CONSISTENCY_SAMPLES=1`로 끄세요 |

---

## 14. 원본

- 구조를 바꾸기 전 노트북: `ai/archive/workspaces/jewon-kim/script-coverage-evaluation/v1/local/` (동결본, 수정 · import 금지).
  전체 기술 레퍼런스는 같은 폴더 위의 `v1/README.md`입니다.
- 개편 직전 상태 그대로의 원본: git 태그 `ai-workspaces-final`
- 아카이브 규칙과 목차: [../../archive/README.md](../../archive/README.md)
- 폴더 규칙: [../README.md](../README.md), [../../README.md](../../README.md)
