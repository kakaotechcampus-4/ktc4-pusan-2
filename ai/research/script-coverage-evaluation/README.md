# script-coverage-evaluation

발표자가 슬라이드 대본의 내용을 **실제로 얼마나 전달했는지** 채점하는 기능의 코어와 연구 도구입니다.
대본을 한 번 분석해 슬라이드별 **평가 기준(Evaluation Rubric)** 을 만들어 두고, 발표 연습마다 음성 인식 결과(STT)를 그 기준과 비교해
**대본 문장 · Key Point · 핵심 수치** 단위로 판정합니다.

| | |
|---|---|
| 담당 | jewon-kim |
| 기능 버전 | `1.0` (`src/script_coverage/version.py`의 `FEATURE_VERSION`), 평가 기준 스키마 `1.1` |
| 검증한 LLM | OpenAI 호환 API, 모델 `openai/gpt-5.6-luna` |
| 상태 | v1. **가상 데이터로만 측정했습니다.** 실제 Deepgram 결과와 실제 발표자의 말투로는 아직 측정하지 않았습니다 |
| 이전 | 코어(`src/script_coverage/`)는 나중에 `ai/service/src/pitch_coach_ai/features/script_coverage/` 로 그대로 옮깁니다 ([배포 가이드](DEPLOY.md)) |

> 이 프로젝트는 `ai/archive/workspaces/jewon-kim/script-coverage-evaluation/v1/local/` 의 노트북 2개를 구조만 바꿔 옮긴 것입니다.
> 판정 로직과 프롬프트는 같고, 같은 LLM 응답 캐시로 돌리면 결과가 바이트 단위로 같습니다 ([옮긴 뒤 검증](#옮긴-뒤-검증)).

---

## 무엇을 하나

```
[대본 JSON]  ──▶ ① script_analysis (대본 등록 · 수정 시 1번, 슬라이드당 LLM 3회) ──▶ 평가 기준(rubric)
                                                                                       │
[슬라이드별 STT] ──▶ ② stt_evaluation (연습마다, 슬라이드당 LLM 1회 + 필요할 때 1회) ◀──┘
                                    │
                                    ▼
                  평가 결과 + 비슷한 말 위치 ──▶ (리뷰 agent) ──▶ 사용자 확인
                                    ▲                                 │
                                    └── confirm.rescore_evaluation (코드, LLM 없음) ◀┘
```

- **① 대본 분석** — 규칙으로 수치 · 이름을 뽑고, LLM 이 문장 역할 · 핵심 주장 · Key Point 를 정하고, LLM 이 한 번 더 최종 결론을 냅니다.
  마지막으로 문장마다 **전달 단위**(주장 · 사실 · 수치 · 나열 항목 하나)를 나눠 둡니다.
- **② STT 평가** — 규칙으로 정렬 · 수치 검증 · 비슷한 말 찾기를 하고, LLM 이 **전달 단위마다** 말했는지 판정하면 코드가 대본 문장 판정으로 모읍니다.
  규칙과 LLM 이 어긋난 문장만 LLM 이 다시 보고, 점수는 코드가 계산합니다.

역할 분담은 한 가지 원칙입니다. 수치 · 이름 · 정렬 · 점수처럼 규칙으로 확실히 정할 수 있는 것은 코드가,
어떤 문장이 핵심인지 · 말한 내용이 문장의 뜻을 전달했는지처럼 의미를 이해해야 하는 것은 LLM 이 맡습니다. LLM 은 판정만 하고 점수는 매기지 않습니다.

**하는 것**

- 대본을 평가 기준으로 만든다: 문장별 역할, 핵심 주장, Key Point(중요도 · 근거 문장), 핵심 사실(수치 · 이름), 전달 단위
- STT 를 전달 단위마다 판정하고(`said` / `vague` / `missing` / `contradicted`) 코드가 문장(`said` / `partial` / `missing` / `contradicted`) · Key Point 판정으로 모은다
- 핵심 수치를 **값으로** 검증한다. 아라비아 숫자와 한글로 읽은 수(`사십이 퍼센트`, `오후 여섯 시`)를 같은 정규형으로 비교하고, 어림 표현과 다른 값을 구분한다
- 발음이 비슷한 다른 말(`노쇼` → `노조`, `삼십 분` → `사십 분`)은 **판단 보류**한다. 점수 비율에서 빼고 개수와 위치만 남긴다
- 사용자 확인(대본대로 말함 / STT 대로 말함)으로 판정과 점수를 코드로 다시 계산한다
- 점수 3개(내용 전달 · 수치 정확도 · 대본 충실도)와 비슷한 말 개수를 코드가 계산한다
- LLM 응답을 캐시해 같은 입력 · 프롬프트 · 모델이면 다시 부르지 않는다. 대본을 고치면 바뀐 슬라이드만 다시 분석한다

**하지 않는 것**

- 음성 인식(Deepgram 결과를 입력으로 받는다), 슬라이드 경계 추정(웹이 슬라이드별 STT 를 준다고 전제)
- 발표자 실수인지 인식 오류인지 **최종 판단** — 비슷한 말은 위치와 함께 넘기고, 사용자 확인이 오면 다시 계산만 한다
- 전달력(말 속도 · 간투사 빈도 · 시선) 평가, 코칭 문구 생성, HTTP API · DB 저장

---

## 폴더 구조와 역할

```
script-coverage-evaluation/
├── README.md · DEPLOY.md        # 이 문서, 배포 가이드
├── pyproject.toml, uv.lock
├── src/
│   ├── script_coverage/            # 코어. 나중에 service 로 폴더째 옮긴다
│   │   ├── version.py              #   FEATURE_VERSION, RUBRIC_SCHEMA_VERSION
│   │   ├── shared/                 #   두 단계가 같이 쓰는 것
│   │   │   ├── rubric.py           #     평가 기준 모델 — 두 단계 사이의 계약
│   │   │   ├── text.py             #     대본 · STT 공통 정규화, 문장 분리, 구간(span)
│   │   │   ├── numbers.py          #     한국어 수 표기 파서
│   │   │   ├── facts.py            #     규칙 기반 Critical Fact Parser
│   │   │   ├── kiwi.py             #     Kiwi 지연 생성, 형태소 경계 확인
│   │   │   └── llm_step.py         #     설정 해시 · 캐시 프로토콜 · 병렬 호출
│   │   ├── script_analysis/        #   ① 대본 → 평가 기준. 진입점 core.analyze_script
│   │   │   ├── core.py             #     슬라이드별 파이프라인
│   │   │   ├── semantic.py · draft.py · final.py · units.py · keywords.py
│   │   │   ├── schemas.py · config.py
│   │   │   └── prompts/            #     프롬프트 상수 (.py)
│   │   └── stt_evaluation/         #   ② STT → 평가 결과. 진입점 core.evaluate_take
│   │       ├── core.py             #     연습 1회 파이프라인
│   │       ├── normalize.py · fillers.py · align.py · fact_check.py · similar.py
│   │       ├── judge.py · merge.py · verify.py · scoring.py
│   │       ├── confirm.py          #     rescore_evaluation: 사용자 확인 뒤 다시 계산
│   │       ├── schemas.py · config.py
│   │       └── prompts/
│   └── coverage_lab/               # research 전용 도구. service 로 옮기지 않는다
│       ├── llm.py                  #   ai/.env 읽기, 구조화 출력 ChatOpenAI 클라이언트
│       ├── cache.py                #   LLM 응답 캐시 (SQLite)
│       ├── store.py                #   평가 기준 · 평가 결과 · 비슷한 말 · 사용자 확인 저장 (SQLite)
│       ├── datasets.py · paths.py · results.py · runs.py
│       ├── rubric_report.py · rubric_quality.py · rubric_consistency.py   # ① 결과 표, 품질 지표, 일관성 측정
│       └── stt_report.py · stt_labels.py · stt_checks.py · stt_stability.py  # ② 결과 표, 정답 라벨 비교, 충돌 조건 점검, 반복 채점 안정성
├── experiments/                    # `# %%` 셀 스크립트, 번호 순서대로 실행 (아래 실행법)
│   └── 01_build_rubrics · 02_reanalyze_edit · 03_rubric_consistency · 04_evaluate_takes · 05_accuracy · 06_stability
├── reports/
│   ├── script_analysis.ipynb       # 보고용 노트북 (import + 표시만)
│   ├── stt_evaluation.ipynb
│   └── results/*.json              # 핵심 지표 (기능 버전 + 모델 포함)
├── data/virtual/                   # 팀이 만든 가상 데이터 (커밋됨)
│   ├── scripts/                    #   대본 2개 (9장 + 11장)
│   ├── stt/                        #   연습 STT 18개
│   └── stt_labels/                 #   문장별 정답 라벨 18개
├── outputs/                        # gitignore. 실행하면 생기는 rubrics.sqlite 등
└── tests/
    ├── unit/                       # 코어 테스트 (코어와 함께 옮긴다)
    └── lab/                        # coverage_lab 테스트
```

`data/` 아래에서 `data/virtual/` 만 커밋합니다. 그 밖의 데이터(실제 대본 · 녹음)는 gitignore 입니다.

### 왜 이렇게 나눴나

**코어와 lab 의 기준은 "나중에 service 로 가는가"입니다.** `script_coverage` 는 배포 서버가 그대로 쓸 코드이고,
`coverage_lab` 은 실험하고 보고하기 위한 도구(파일 · SQLite · pandas · print 를 자유롭게 씀)라 옮기지 않습니다.
service 는 아직 만들지 않았으므로 코어도 지금은 여기에 둡니다. 최종본을 service 로 옮길 때 코드를 고치지 않아도 되도록, 코어에 세 가지 규칙을 둡니다.

| 코어 규칙 | 이유 |
|---|---|
| 패키지 안에서는 **상대 import 만** | `script_coverage` 가 `pitch_coach_ai.features.script_coverage` 로 이름이 바뀌어도 고칠 import 가 없다 |
| **파일 · DB · 네트워크 · 환경변수에 접근하지 않음** | service 는 상태를 두지 않는 서버다. 저장은 BE, 키와 설정은 service 의 공통 코드가 맡는다 |
| **LLM 클라이언트와 캐시는 인자로 받음** (의존성은 pydantic · kiwipiepy · scikit-learn 만) | research 는 로컬 SQLite 캐시와 `.env` 키를, service 는 캐시 없이(`cache=None`) 자기 클라이언트를 넘긴다. 코어는 어느 쪽인지 모른다 |

세 규칙은 `tests/unit/test_core_boundary.py` 가 코드를 읽어 검사합니다. 어기면 테스트가 실패하므로 옮길 때가 아니라 쓰는 도중에 걸립니다.
scikit-learn 은 v1 결과(TF-IDF 키워드)를 만든 `1.8.x` 로 고정했습니다. 올릴 때는 캐시 재생 비교로 평가 기준이 같은지 먼저 확인합니다.

**`script_analysis/` 와 `stt_evaluation/` 는 한 기능의 하위 폴더입니다.** 두 단계는 호출 시점 · 입출력이 다르지만(대본 등록 때 1번 / 연습마다),
평가 기준이라는 같은 데이터로 묶여 한 기능 버전으로 움직입니다. 평가 기준의 의미가 바뀌면 두 단계가 함께 바뀌므로 `features/` 아래 별개 기능 둘로 쪼개지 않았습니다.
다만 둘은 **서로 import 하지 않고** 둘 다 `shared/` 만 import 합니다. 그래서 한쪽을 고쳐도 다른 쪽에 숨은 영향이 없습니다.

**`shared/rubric.py` 가 두 단계 사이의 계약입니다.** `script_analysis` 가 쓰고 `stt_evaluation` 이 읽는 `EvaluationRubric` 과 그 상수를 한 곳에 둬서,
두 폴더가 서로를 알 필요 없이 같은 모양을 봅니다. 이 모델의 의미가 바뀌면 기능 버전을 올립니다.

**프롬프트와 LLM 출력 스키마는 `.py` 상수입니다.** 프롬프트 · 메시지 형식 · 출력 스키마(클래스 이름 · docstring · 필드 순서 · 설명) · 모델은
`shared/llm_step.py` 의 `config_hash` 로 해시되어 캐시 키에 들어갑니다. 텍스트 파일(`.txt`, `.md`)로 두면 git `autocrlf` 가 줄바꿈을 바꿔 해시가 달라지고
(캐시가 전부 무효가 되어 전부 다시 과금), `.py` 안의 문자열 상수는 그 영향을 받지 않습니다. 그래서 프롬프트 파일에는 "글자 하나도 바꾸지 않는다"는 주석이 있습니다.

**`stt_evaluation/fillers.py` 는 일부러 떼어 뒀습니다.** 간투사 · 반복 정규식이 들어 있고 `re` 만 import 하므로,
나중에 간투사 횟수를 세는 기능을 만들 때 이 파일을 그대로 가져갈 수 있습니다. 주의할 점:

- `normalize_stt` 가 쓰는 목록은 **지워도 안전한 소리뿐인 간투사**(`음 · 어 · 으 · 엄 · 흠 · 아 · 에`)만 담습니다. `그러니까`, `이제`, `그`, `저` 는 뜻이 있을 수 있어 남깁니다.
- `find_fillers` 는 위치(span)를 돌려주지만 **파이프라인은 쓰지 않습니다.**
- 이 목록은 BE 의 `backend/src/pitch_coach_backend/realtime/fillers.py` 와 다릅니다. 무엇을 간투사로 셀지는 필러 카운트 기능을 만들 때 정합니다.

---

## 실행법

### 준비

```bash
cd ai/research/script-coverage-evaluation
uv sync                      # Python 3.12 이상, 의존성 + dev 도구 설치
```

**`ai/.env`** — `ai/.env.example` 을 `ai/.env` 로 복사해 `OPENAI_API_KEY` 를 채웁니다.

| 변수 | 필수 | 뜻 |
|---|---|---|
| `OPENAI_API_KEY` | 예 | API 키 |
| `OPENAI_MODEL` | 예 | 모델 이름. 엔드포인트가 허용하는 이름이어야 합니다 (아니면 400). 검증한 값은 `openai/gpt-5.6-luna` |
| `OPENAI_BASE_URL` | 아니오 | OpenAI 호환 엔드포인트. 없으면 OpenAI 기본 주소 |

- `coverage_lab/llm.py` 가 현재 디렉터리부터 위로 올라가며 처음 만나는 `.env` 를 읽고, 없으면 프로젝트 폴더부터 올라가며 찾습니다. `ai/.env` 가 걸립니다.
- 이미 설정된 환경 변수는 `.env` 가 덮어쓰지 않습니다.
- **mock 모드는 없습니다.** LLM 단계는 항상 실제 API 를 부릅니다. 캐시에 없는 호출은 과금됩니다.
- 모델 이름은 캐시 키(설정 해시)에 들어갑니다. 캐시를 재사용하려면 캐시를 만든 때와 같은 `OPENAI_MODEL` 이어야 합니다.

**Windows 참고** — 이 PC 에서는 `pytest.exe` · `jupyter-*.exe` 실행 파일이 앱 제어로 막혀 있어 `uv run python -m ...` 형태로 부릅니다.
콘솔 한글이 깨지면 `PYTHONIOENCODING=utf-8` 을 설정하세요.

### 캐시 재사용 (API 를 부르지 않고 시작하기)

LLM 응답 캐시는 `outputs/rubrics.sqlite` 에 쌓이고, 아카이브 노트북과 **같은 표 · 열**을 씁니다. 그래서 노트북을 돌려 본 사람은 그 DB 를 가져올 수 있습니다.

```bash
mkdir -p outputs
cp ../../archive/workspaces/jewon-kim/script-coverage-evaluation/v1/local/outputs/rubrics.sqlite outputs/rubrics.sqlite
```

`outputs/` 는 gitignore 라 저장소에는 없습니다. 아카이브 노트북을 실제로 실행한 사람의 로컬에만 있습니다. 없으면 처음 한 번은 과금됩니다.

**과금 없이 돌리기** — `OPENAI_BASE_URL` 을 닿지 않는 주소로 두면 캐시에 있는 호출만 성공하고, 캐시에 없는 호출은 실패해서 새로 과금되지 않습니다.

```bash
# PowerShell
$env:OPENAI_BASE_URL = "http://127.0.0.1:9"; $env:OPENAI_API_KEY = "dummy"; $env:OPENAI_MODEL = "openai/gpt-5.6-luna"
uv run python experiments/01_build_rubrics.py
```

실패한 슬라이드는 평가 기준을 만들지 않고 `stats["failed"]` 에 남습니다. 성공한 호출은 캐시에 남으므로 나중에 제대로 된 주소로 다시 돌리면 실패한 것만 부릅니다.
`ai/.env` 에 이미 키가 있으면 그 키로 실제 호출이 일어나니, 확신이 없을 때는 위처럼 먼저 막아 두세요.

### 실험 순서

`experiments/*.py` 는 `# %%` 셀 스크립트라 VS Code Interactive Window 에서 셀 단위로 돌릴 수 있고,
`uv run python experiments/<번호_이름>.py` 로 끝까지 돌릴 수도 있습니다. 비용은 **캐시가 비어 있을 때** 기준이고, 같은 입력으로 다시 돌리면 0회입니다.

| 순서 | 스크립트 | 하는 일 | LLM 호출 (캐시 없음) | 남기는 것 |
|---|---|---|---|---|
| 1 | `01_build_rubrics.py` | 대본 2개를 분석해 슬라이드별 평가 기준 저장 | 슬라이드당 3회 (1차 · 최종 · 전달 단위), 20장 약 60회 | `evaluation_rubrics`, 캐시 |
| 2 | `02_reanalyze_edit.py` | 가상대본2 슬라이드 5 의 한 문장을 고쳐 바뀐 슬라이드만 다시 분석되는지 확인 | 최대 3회 | `outputs/가상대본2_수정.json` |
| 3 | `03_rubric_consistency.py` | 품질 지표 + 같은 대본을 `RUBRIC_CONSISTENCY_SAMPLES` 벌(기본 3) 분석해 흔들림 측정 | 약 80회 | `reports/results/rubric_consistency.json` |
| 4 | `04_evaluate_takes.py` | 연습 18번을 평가 기준으로 채점, 결과 표 | 슬라이드당 1회 + 충돌 슬라이드 1회, 약 225회 | `slide_evaluations`, `similar_items` |
| 5 | `05_accuracy.py` | 정답 라벨과 비교, 충돌 조건 점검, 사용자 확인 뒤 정확도 | **0회** | `reports/results/stt_accuracy.json` |
| 6 | `06_stability.py` | 같은 STT 를 `STT_CONSISTENCY_SAMPLES` 번(기본 3) 채점해 판정 흔들림 측정 | 약 450회 | `reports/results/stt_stability.json` |

- 1 이 먼저입니다. 2~4 는 1 의 평가 기준을, 5 · 6 은 4 의 채점을 읽습니다.
- 비싼 측정(3, 6)은 `RUBRIC_CONSISTENCY_SAMPLES=1` · `STT_CONSISTENCY_SAMPLES=1` 로 끌 수 있습니다.
- 프롬프트 · 출력 스키마 · 모델을 바꾸면 설정 해시가 바뀌어 캐시가 모두 무효가 되고 전부 다시 부릅니다. 의도한 변경이 아니면 되돌리세요.

### 보고용 노트북

`reports/script_analysis.ipynb`, `reports/stt_evaluation.ipynb` 는 `coverage_lab` 과 `script_coverage` 의 함수만 불러 DB 와 `reports/results/*.json` 을 표로 보여 줍니다.
**로직이 없고 LLM 을 부르지 않습니다.** 표시할 결과가 있으려면 위 실험을 먼저 돌려 두어야 합니다.

```bash
uv run python -m nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=3600 reports/script_analysis.ipynb
uv run python -m nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=3600 reports/stt_evaluation.ipynb
```

### 테스트

```bash
uv run python -m pytest                    # 기본: unit + lab. live 는 제외
uv run python -m pytest tests/unit         # 코어만 (경계 규칙 검사 포함)
uv run python -m pytest tests/lab          # coverage_lab 만
uv run python -m pytest -m live            # 실제 API 를 부르는 테스트만 (과금)
```

`live` 표식이 붙은 테스트는 실제 LLM 을 부르므로 기본 실행(`addopts = "-m 'not live'"`)에서 빠집니다.
`tests/live/test_live_pipeline.py` 는 가상대본1 슬라이드 6 한 장을 캐시 없이 대본 분석 → STT 평가까지 돌립니다 (약 4~5회 호출).
캐시 재생 비교로는 확인되지 않는 부분, 곧 LLM 클라이언트를 만들어 실제로 부르고 구조화 출력을 받는 경로를 확인합니다.

### 옮긴 뒤 검증

아카이브 노트북이 남긴 LLM 캐시를 새 코드로 재생해 비교했습니다 (LLM 호출 0회).

| 항목 | 결과 |
|---|---|
| 평가 기준 31개 (대본 2개 + 수정본) | 아카이브 결과와 바이트 단위로 같음 |
| 슬라이드 평가 180개 (연습 18번) | 같음 |
| 비슷한 말 · 사용자 확인 · 확인 뒤 평가 | 같음 |
| 정확도 · 안정성 지표 | 같음 |

코어를 고친 뒤에는 같은 방식(캐시 재생 → 결과 비교)으로 평가 기준 · 평가 결과가 의도 밖으로 바뀌지 않았는지 확인할 수 있습니다.

---

## 출력 계약

다운스트림(BE · 리뷰 agent)과의 접점은 네 가지입니다: 평가 기준, 슬라이드 평가, 비슷한 말, 사용자 확인.
**원본은 pydantic 클래스입니다** — 평가 기준(`EvaluationRubric`)은 `shared/rubric.py`, 평가 결과(`SlideEvaluation`, `SimilarItem` 등)는 `stt_evaluation/schemas.py` 입니다.
이 문서의 표는 읽기 쉽게 요약한 것이고, 필드가 어긋나면 코드가 맞습니다.
service 에서는 이 모델이 `contracts/` 로 생성되고 BE 가 저장합니다. research 에서는 `coverage_lab/store.py` 의 SQLite 표가 그 역할을 대신합니다 (아래 대응표).

### 평가 기준 — `EvaluationRubric` (① 이 만들고 ② 가 읽음)

| 필드 | 뜻 |
|---|---|
| `meta` | `rubric_id`, 대본 이름, 슬라이드 번호, `content_hash`, 스키마 버전(`1.1`), LLM 모델, 1차 · 최종 · 전달 단위 설정 해시 |
| `sentences`, `sentence_roles` | 정규화한 대본 문장과 문장별 역할 `claim` / `evidence` / `detail` / `skip` |
| `core_claim` | 슬라이드의 핵심 주장 한 문장 |
| `key_points[]` | `id`, `content`, `importance`(`critical` / `high` / `normal`), `sentence_indices`(근거 문장), `source_quote`, `key_terms`, `fact_ids` |
| `critical_facts[]` | `id`, `type`, `value`(대본 표기), `normalized`(비교용 정규형), `numeric_value`, `unit`, `qualifier`, `spans`, `sentence_indices`, `source`(`rule` / `llm`), `key_point_ids`, `importance` |
| `content_units[]` | 전달 단위: `id`(`S1-U2`), `sentence_index`, `text`, `quote`(대본 속 표현), `span`, `fact_ids`, `source`(`llm` / `fact` / `sentence`). `skip` 문장은 없음 |
| `keywords`, `warnings`, `review_changes`, `name_decisions` | TF-IDF 키워드, 최종 경고, 최종 결론이 1차에서 바꾼 점, 이름 · 용어 후보 판정 |

### 슬라이드 평가 — `SlideEvaluation` (② 가 만들고 소비자가 읽음)

| 필드 | 뜻 |
|---|---|
| `scores` | `content_coverage`, `critical_fact_accuracy`(사실이 없으면 `null`), `script_fidelity`, `similar_words`, `similar_numbers` |
| `sentences[]` | 대본 문장별 `status` · `first_status`(LLM 1차) · `evidence`(T번호) · `reason` · `confidence` · `units`(전달 단위별 `status` · `first_status` · `raised_by_rule`) · `lexical_coverage` · `evidence_coverage` · `fact_statuses` · `similar_ids` · `conflicts` · `verified` |
| `key_points[]` | Key Point 별 `status`(`covered` / `partial` / `missing` / `contradicted`) — 근거 문장 판정을 모은 값 |
| `critical_facts[]` | 사실별 `status`(`matched` / `approximate` / `mismatched` / `missing` / `sound_alike` / `unverified`), STT 표기, 원인 |
| `similar_items[]` | 비슷한 말 (아래) |
| `stt_text`, `stt_sentences`, `confirmations` | 정규화한 STT 전체 · 문장(T번호가 가리키는 원문), 반영한 사용자 확인 (처음 채점에는 비어 있음) |
| `rubric_id`, `alignments`, `fidelity`, `verification` | 채점에 쓴 평가 기준, 문장 정렬, 충실도 세부, 교차 검증 대상과 충돌 조건별 문장 번호 |

| 문장 `status` | 뜻 | 사실 `status` | 뜻 |
|---|---|---|---|
| `said` | 뜻이 전달됨 (의역 포함, 핵심 수치 · 이름까지) | `matched` | 같은 값을 말함 |
| `partial` | 일부만 (수치를 흐리거나 내용 일부만) | `approximate` | 어림해 말함, 사실은 맞음 |
| `missing` | 없음 | `mismatched` | 다른 값을 말함 (발음이 전혀 다르거나 음절 순서가 바뀜) |
| `contradicted` | 다른 수치 · 반대 내용 | `sound_alike` | 발음이 비슷한 다른 말 — **판단 보류** |
| | | `unverified` | 규칙으로 못 찾은 영문 이름 — LLM 이 확인 |

### 비슷한 말 — `SimilarItem` (한 항목씩)

| 열 | 뜻 |
|---|---|
| `kind`, `script_text`, `stt_text` | `word` / `number`, 대본 표현, STT 표현 |
| `script_sentence_index`, `script_start`, `script_end` | 대본 문장 번호와 정규화 대본 안의 위치 |
| `stt_sentence_index`, `stt_start`, `stt_end` | STT 문장 번호(T번호)와 정규화 STT 안의 위치 |
| `stt_raw_start`, `stt_raw_end` | **원본 STT** 안의 위치 (간투사를 지우기 전 기준). Deepgram 단어 타임스탬프와 맞춰 녹음 구간을 찾는 데 쓴다 |
| `fact_id`, `key_point_ids` | 관련 Critical Fact, 이 자리가 걸린 Key Point |
| `rule_guess`, `signals` | 규칙 추정(`asr_error` / `speaker_error`)과 근거 신호 — **참고용** |

### 사용자 확인과 다시 계산 — `confirm.rescore_evaluation`

| 사용자 답 | 뜻 | 다시 계산 |
|---|---|---|
| `as_script` | 대본대로 말함 — 음성 인식이 잘못 적음 | 사실 `sound_alike` → `matched`. 문장 판정은 그대로 (처음 채점이 이미 대본대로 말한 것으로 보고 판정) |
| `as_stt` | STT 대로 말함 — 발표자가 다른 말을 함 | 사실 → `mismatched`, 그 자리가 든 전달 단위 → `contradicted` → 문장 · Key Point · 점수를 다시 모음 |

- 사용자 확인에는 답과 함께 항목의 대본 · STT 표현을 남겨, 다시 채점해서 항목 번호가 바뀌어도 다른 항목에 적용되지 않게 합니다.
- 확인 뒤 평가는 처음 채점과 같은 모양에 `confirmations` 가 채워진 것이고, **처음 채점 결과는 바꾸지 않습니다.**
- 답하지 않은 항목은 계속 보류입니다. 평가 기준이 바뀌었으면(`rubric_id` 가 다르면) 다시 계산하지 않고 다시 채점하라고 알립니다.
- 코드만 씁니다 (LLM 호출 없음, DB 없음). 읽고 저장하는 일은 호출하는 쪽이 합니다.

이 모양들의 **의미**가 바뀌면 기능 버전을 올립니다.

### research 의 SQLite 표 (`coverage_lab/store.py`, `outputs/rubrics.sqlite`)

BE 가 배포 환경에서 맡을 저장을 research 에서는 이 표들이 대신합니다. 표 이름 · 열은 아카이브 노트북의 DB 와 같습니다.

| 표 | 키 | 내용 |
|---|---|---|
| `evaluation_rubrics` | (대본 이름, 슬라이드) | 평가 기준 JSON + 내용 해시 |
| `slide_evaluations` | (연습, 슬라이드) | 평가 결과 JSON |
| `similar_items` | (연습, 슬라이드, 항목) | 비슷한 말 한 행씩 |
| `similar_confirmations` | (연습, 슬라이드, 항목) | 사용자 답 + 항목 표현 |
| `confirmed_evaluations` | (연습, 슬라이드) | 사용자 확인으로 다시 계산한 평가 결과 |
| `semantic_cache` · `semantic_samples` · `final_cache` · `unit_cache` · `stt_llm_cache` | 입력 해시 + 설정 해시 | LLM 응답 캐시 (`coverage_lab/cache.py`). 호출 종류와 표의 대응은 그 파일 docstring |

`CREATE TABLE IF NOT EXISTS` 로 만들기 때문에 컬럼을 바꾸면 기존 표를 지워야 합니다. `similar_items` · `slide_evaluations` · `confirmed_evaluations` 는 다시 채워지는 파생 데이터라 지워도 되지만,
`similar_confirmations` 는 사용자 답이라 지우지 마세요. Windows 에서 DB 파일이 안 지워지면 커널(노트북 · Interactive Window)이 연결을 잡고 있는 것이니 먼저 종료합니다.

---

## 버전별 결과표

> **모두 가상 데이터 기준입니다.** 가상 STT 와 정답 라벨을 같은 작성자가 만들어 실제보다 쉬울 수 있고, 규칙 임계값 일부는 같은 데이터를 보고 정했습니다.
> 실제 Deepgram 출력과 실제 발표자로는 측정하지 않았습니다. LLM 판정의 실제 정확도는 아직 검증되지 않았습니다.
> 규칙 부분(수 파서, 수치 검증, 비슷한 말 위치)은 결정적이고 가상 데이터 전체에서 정답과 일치했습니다.

### v1 (기능 버전 `1.0`, 모델 `openai/gpt-5.6-luna`)

데이터: 대본 2개(슬라이드 20장, Key Point 80개), 연습 18번(대본당 9개 시나리오), 대본 문장 765개. 숫자는 `reports/results/*.json` 입니다.

**① 대본 분석** (`rubric_consistency.json`)

| 지표 | 값 |
|---|---|
| 중요도 분포 critical / high / normal | 19 / 40 / 21 |
| Key Point 에 연결된 수치 사실 | 57 / 57 |
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

**대본별 일반화** — 판정 규칙을 다듬을 때 대본1 의 오답만 보고 대본2 는 검증용으로 남겼습니다 (문장 정확도, 최종).

| | 0번 | 1번 | 2번 |
|---|---|---|---|
| 대본1 (규칙을 다듬을 때 본 데이터) | 0.997 | 0.995 | 0.995 |
| 대본2 (보지 않은 데이터) | 0.987 | 0.985 | 0.982 |

대본2 에서 1%p 정도 낮습니다. 새 데이터에서는 이 정도 낮아질 수 있다고 보세요. 시나리오별로는 충실 · 의역 · 누락 · 인식오류 · 더듬기 · 발음이 1.0 에 가깝고,
요약형(take8)이 가장 낮습니다.

**결과 읽을 때** — 정확도 차이 ±0.004 는 채점마다 달라지는 폭(2~3문장)과 비슷해 잡음입니다. 같은 데이터로 방식을 비교할 때는 이 폭을 넘는 차이만 봅니다.

### 새 버전을 올릴 때

출력 의미가 바뀌어 `FEATURE_VERSION` 을 올렸다면 위 표 아래에 그 버전의 표를 추가합니다. 실제 Deepgram 데이터로 측정하면 가상 데이터 결과와 분리해서 적습니다.
v1 을 "완료"라고 부르려면 실제 슬라이드별 Deepgram 결과와 녹음 5~10개에 정답 라벨(`data/virtual/stt_labels/` 형식)을 만들어
`05_accuracy.py` → `06_stability.py` 를 돌려야 합니다.

---

## 배포

AI 서버에 올리는 데 필요한 작업(서버 · 인프라 · BE 연동)과 순서, API, 비용, 결정해야 할 것은 [DEPLOY.md](DEPLOY.md) 에 있습니다.

---

## 설계 결정

| 결정 | 이유 |
|---|---|
| LLM 은 전달 단위를 판정하고, 문장 · Key Point 는 코드가 모음 | Key Point 를 통째로 판정하면 "몇 문장을 말해야 일부 전달인가"가 매번 흔들렸습니다. 문장 단위로 바꾸자 요약형 발표 정확도 0.887 → 0.963. 단위 판정 → 문장 → Key Point 로 모으는 규칙은 데이터가 바뀌어도 같습니다 |
| 규칙과 LLM 을 독립적으로 돌리고, 어긋난 곳만 다시 판정 | LLM 에 규칙 결과를 주면 끌려갑니다. 독립 판정을 비교하면 한쪽의 실수를 찾을 수 있고, 다시 판정하는 비용은 충돌한 곳에만 듭니다 (연습 슬라이드의 약 25%) |
| 규칙이 확실히 아는 것은 LLM 판정을 보정 | 이 문장에만 있는 수치 · 이름을 STT 에서 찾았으면 그 단위는 "전혀 없음"일 수 없습니다 (`raised_by_rule`). LLM 이 단위를 엄격하게 볼 때의 "일부 전달 → 빠짐" 오답을 막습니다 |
| 두 판정 방식을 함께 쓰지 않음 (시도 후 제외) | 문장 전체 판정과 전달 단위 판정이 어긋나면 교차 검증하는 방식을 재 봤는데, 검증자가 1차의 오답을 바로잡는 만큼 맞은 것을 망쳐 평균 0.990 → 0.988 이고 호출은 2배였습니다 |
| 전달 단위 방식을 기본으로 | 정확도는 문장 단위 판정과 같지만(0.990), 일부 전달의 경계를 코드가 정해 라벨 기준과 구조적으로 맞고, 리뷰 agent 가 빠진 정보를 단위로 짚을 수 있고, 호출 수는 같습니다 |
| 근거 → 이유 → 판정 순서, 근거 번호는 코드가 확인 | 판정을 먼저 정하고 근거를 끼워 맞추지 않게 합니다. 근거 문장에 그 대본 문장의 내용이 있는지도 봅니다 (`evidence_unrelated`) |
| 충돌 조건은 1차 판정마다 "맞다면 규칙에서 보여야 할 것"으로 정의하고 발동을 기록 | 조건이 `said` · `missing` 쪽에만 있으면 `partial` 은 검사되지 않습니다. 조건별 발동 · 단독 발동 · 바로잡음을 남겨 쓸모없는 조건을 가려냅니다 |
| 점수는 코드가 계산 | LLM 이 점수를 매기면 같은 판정도 스케일이 흔들립니다. 틀린 말(`contradicted`)은 빠뜨린 것(`missing`)보다 낮게 봅니다 — 청중에게 잘못된 정보를 준 것입니다 |
| 수치는 문자열이 아니라 값으로 비교 | STT 는 숫자를 들리는 대로 적습니다(`사십이 퍼센트`). 한글 수 파서를 넣자 STT 수치 검증 정확도 74.6% → 100% |
| 발음이 비슷한 말은 판단 보류 | 텍스트만으로는 발표자 실수와 인식 오류를 가릴 수 없습니다. LLM 에게 원인을 판정시켜도 규칙 추정보다 낫지 않았고, 수치는 억울한 감점이 생겼습니다. 그래서 비율에서 빼고 위치를 넘깁니다 |
| 보류한 말은 사용자 확인으로 코드가 다시 계산 | 발표자에게 물어야 아는 것은 묻습니다. 다시 계산은 단위 판정을 바꾸고 코드로 모으기만 하면 되어 LLM 을 다시 부르지 않고, 같은 답이면 늘 같은 점수가 나옵니다 |
| STT 정규화는 규칙 (LLM 으로 다시 쓰지 않음) | LLM 이 문맥에 맞게 고쳐 쓰면(`노조` → `노쇼`) 인식 오류도 발표자 실수도 사라지고, 원본 위치 대응이 깨지고, 결과가 흔들립니다 |
| 대본 분석은 1차 분석 + 최종 결론 2회 | 최종 결론이 검증 경고를 줄였습니다 (8 → 2). 다만 분석 결과의 흔들림(일관성)은 줄이지 못했습니다 |
| 평가 기준은 대본 등록 때 한 번 만들어 저장 | 같은 대본의 연습끼리 같은 기준으로 비교됩니다. 발표할 때마다 대본을 다시 분석하지 않습니다 |

### 점수

LLM 은 점수를 매기지 않습니다. 모두 `stt_evaluation/scoring.py` 가 계산하고, 발표 전체 점수는 슬라이드 점수의 가중 평균(개수는 합)입니다.

| 점수 | 계산 |
|---|---|
| `content_coverage` | Σ(Key Point 판정 점수 × 중요도 가중치) / Σ(가중치). `covered` 1 / `partial` 0.5 / `missing` 0 / `contradicted` −0.5, 가중치 critical 3 / high 2 / normal 1. 음수면 0 |
| `critical_fact_accuracy` | Σ(사실 점수 × 가중치) / Σ(가중치). `matched` 1, `approximate` 0.5, 나머지 0. **`sound_alike` 는 분자 · 분모에서 뺌** |
| `script_fidelity` | 순서를 지킨 형태소 일치의 recall · precision 조화평균. **비슷한 말 자리의 형태소는 양쪽에서 뺌** |
| `similar_words` / `similar_numbers` | 비율에서 뺀 비슷한 단어 · 수치의 개수 |

임계값은 가상 STT 와 정답 라벨로 맞춘 값이라(`stt_evaluation/config.py`) 바꾸면 채점 결과가 달라집니다. 바꾸기 전에 기준값을 고정한 채 먼저 재고, 그다음에 조정하세요.

---

## 알려진 한계

- **실제 데이터로 측정하지 않았습니다.** 실제 Deepgram 출력의 문장 분리, 인식 오류 양상, 실제 발표자의 말투는 가상 데이터와 다를 수 있습니다.
- **요약형 발표의 경계**가 가장 흔들립니다. 세부를 줄여 요약한 말을 `vague` 로 보는 경우가 있어, 채점마다 판정이 바뀐 Key Point 6개 중 5개가 요약형(take8)입니다.
- **충돌로 잡지 못하는 1차 오답**이 한 번의 채점에 3개 안팎 남습니다 (주로 요약형의 `said` ↔ `partial`).
- **검증용 대본에서 1%p 정도 낮습니다.** 새 데이터에서는 기준값을 고정한 채 먼저 재고 조정하세요.
- **한 슬라이드가 채점마다 `covered` ↔ `contradicted` 를 오갈 수 있습니다** (슬라이드 점수 최대 60점, 발표 전체는 최대 3.8점).
  리뷰 agent 는 슬라이드 점수 숫자보다 문장 판정과 근거 문장을 보고 말해야 합니다.
- **발음이 비슷한 발표자 실수**(`18%` → `28%`)는 사용자가 확인하기 전까지 점수에 반영되지 않습니다.
- **대본 분석의 흔들림** — 같은 대본을 다시 분석하면 Key Point 묶음과 이름 · 용어 사실이 조금 달라집니다(일치율 0.77).
  평가 기준은 한 번 만들어 저장하므로 같은 대본의 연습끼리는 일관되지만, 기준을 다시 만들면 점수가 달라질 수 있습니다.
- **영문 이름의 인식 오류**(`Prophet` → `profit`)는 비슷한 말로 잡지 않고 LLM 이름 확인에 맡깁니다.
- **간투사 목록이 짧습니다.** `그`, `저` 같은 말은 뜻이 있을 수 있어 지우지 않습니다 (더듬기 연습에서 `그` 가 14~17번 남음).
  간투사를 세는 기준은 필러 카운트 기능을 만들 때 정합니다 (`fillers.py` 설명 참고).
- **대본 형식** — 무대 지시문(괄호 안 동작 설명 등)이 없는 대본을 전제합니다.

---

## 가상 데이터와 정답 라벨

실제 발표 대본과 녹음은 공개 저장소에 넣지 않습니다. `data/virtual/` 의 모든 데이터는 팀이 만든 가상 데이터입니다.

- **대본** — `가상대본1`(도서관 좌석 예측 서비스, 9장), `가상대본2`(동네 빵집 재고 예보, 11장). 형식은 `[{"slide_number": 1, "script": "…"}, …]`
- **STT** — 대본마다 9개 시나리오: 충실 · 의역 · 누락 · 실수 · 혼합 · 인식오류 · 더듬기 · 요약 · 발음 (`take1` ~ `take9`). 형식은
  `{"script_name", "take_id", "scenario"(선택), "slides": [{"slide_number", "stt"}]}`.
  문장부호가 거의 없고, 간투사 · 말 반복 · 띄어쓰기 오류, 한글로 읽은 숫자, 발음대로 적은 영문 이름이 섞여 있습니다.
- **정답 라벨** (`stt_labels/<take_id>.json`) — 대본 **문장마다** 발표자가 **실제로 말한 것** 기준으로 적습니다. 평가 파이프라인은 라벨을 보지 않고 `coverage_lab/stt_labels.py` 가 비교할 때만 읽습니다.

| `status` | 기준 |
|---|---|
| `verbatim` / `paraphrased` | 문장의 정보(주장 · 사실 · 수치 · 나열 항목)를 모두 말함. 꾸미는 말을 빼도 전달 |
| `partial` | 일부만 또는 흐리게 말함 (나열 항목 일부, 수치를 `많이` · `넘게` 로, 이름만 말하고 내용 빠짐) |
| `missing` | 이 문장의 정보를 하나도 말하지 않음. 같은 주제나 앞뒤 문장의 내용만 말한 것도 `missing` |
| `contradicted` | 다른 값 · 반대 내용. 틀렸다가 바로 고친 것은 고친 말 기준 |

보조 필드: `dropped_values`(빠뜨린 값), `changed_values`(실제로 틀리게 말한 값), `asr_errors`(맞게 말했지만 인식이 다르게 적은 값 — 문장은 `verbatim` 등), `approximated_values`, `additions`, `note`.
**라벨은 채점과 같은 기준으로 적어야** 정확도가 의미 있습니다. 새 데이터의 라벨도 이 기준을 따르세요.

---

## 트러블슈팅

| 증상 | 원인 · 해결 |
|---|---|
| `RuntimeError: .env 에 OPENAI_API_KEY / OPENAI_MODEL 이 없다` | `ai/.env` 를 만들었는지, 키와 모델이 채워져 있는지 확인. 메시지에 읽은 파일 경로가 나옵니다 |
| LLM 호출이 400 으로 실패 | 엔드포인트가 허용하지 않는 모델 이름. `OPENAI_MODEL` 확인 |
| 실행했더니 API 가 많이 불림 | 프롬프트 · 스키마 · 모델 이름이 캐시를 만든 때와 다르거나 캐시(`outputs/rubrics.sqlite`)가 없습니다. 의도한 게 아니면 되돌리거나 아카이브 캐시를 복사 |
| `sqlite3.OperationalError: table … has N columns` | 표 컬럼을 바꿨는데 예전 표가 남아 있음. 해당 표를 지우고 다시 실행 (`similar_confirmations` 는 지우지 말 것) |
| STT 쪽에서 평가 기준이 없다는 오류 | 평가 기준이 먼저 있어야 합니다. `01_build_rubrics.py` 를 먼저 실행 |
| 콘솔에 한글이 깨짐 (Windows) | `PYTHONIOENCODING=utf-8` |
| `pytest.exe` · `jupyter-*.exe` 가 실행되지 않음 (Windows 앱 제어) | `uv run python -m pytest`, `uv run python -m nbconvert` 처럼 모듈로 실행 |
| 반복 측정(`06_stability.py` · `03_rubric_consistency.py`)이 오래 걸리고 호출이 많음 | 검증용입니다. `STT_CONSISTENCY_SAMPLES=1`, `RUBRIC_CONSISTENCY_SAMPLES=1` 로 끄세요 |

---

## 원본

- 구조를 바꾸기 전 노트북: `ai/archive/workspaces/jewon-kim/script-coverage-evaluation/v1/local/` (동결본, 수정 · import 금지).
  전체 기술 레퍼런스는 같은 폴더 위의 `v1/README.md`입니다.
- 개편 직전 상태 그대로의 원본: git 태그 `ai-workspaces-final`
- 아카이브 규칙과 목차: [../../archive/README.md](../../archive/README.md)
- 폴더 규칙: [../README.md](../README.md), [../../README.md](../../README.md)
