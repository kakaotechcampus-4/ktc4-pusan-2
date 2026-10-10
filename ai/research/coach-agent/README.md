# coach-agent (실시간 코치)

발표 중 1초마다 시선 · 말 속도 · 음량 · 침묵 · 군더더기 · 시간(장별 계획 대비)을 보고
**지금 발표자에게 말을 걸지, 건다면 무엇 하나를 말할지** 정하는 기능입니다.
말한 뒤에는 실제로 행동이 바뀌었는지 보고 같은 방법을 유지할지, 다른 방법을 쓸지, 그만둘지를 고릅니다.
Take가 끝나면 판단 기록을 묶어 리뷰 에이전트가 쓸 근거(문제 순위 · 미션 판정 · 이전 Take 비교 · 다음 미션 후보)를 만듭니다.

| | |
|---|---|
| 담당 | jewon-kim |
| 판단 방식 | 규칙 + 되돌아보기. 1초 판단에는 LLM을 쓰지 않고, Take 시작 전 코칭 계획에만 LLM을 한 번 씁니다 |
| 기능 버전 | 판단 규칙 `coach-v1.2`, 요청 · 응답 스키마 `1.2`, `coach_state` 모양 `1` (`src/coach/version.py`) |
| 상태 | v1.2. **가상 발표자로만 확인했습니다.** 기준값은 실제 발표 데이터로 검증하지 않았습니다 |
| 배포 | 판단 코어(`src/coach/`)는 나중에 AI 서버로 그대로 옮깁니다 → [DEPLOY.md](DEPLOY.md) |

> 이 프로젝트는 `ai/archive/workspaces/jewon-kim/coach-agent/v1/local/`을 구조만 바꿔 옮긴 것입니다.
> 판단 로직과 기준값은 같고, 테스트 · 재생 시나리오 결과 · 리뷰 근거 실험 수치가 옮기기 전과 같습니다 ([옮긴 뒤 검증](#옮긴-뒤-검증)).

## 목차

1. [이 기능이 하는 일](#1-이-기능이-하는-일)
2. [코드 구조 한눈에 보기](#2-코드-구조-한눈에-보기)
3. [폴더와 파일](#3-폴더와-파일)
4. [실행 흐름 따라가기](#4-실행-흐름-따라가기)
5. [판단 규칙](#5-판단-규칙)
6. [실행법](#6-실행법)
7. [코드 읽는 순서와 자주 묻는 것](#7-코드-읽는-순서와-자주-묻는-것)
8. [입출력](#8-입출력)
9. [버전별 결과표](#9-버전별-결과표)
10. [배포](#10-배포)
11. [설계 결정](#11-설계-결정)
12. [알려진 한계](#12-알려진-한계)
13. [다음 버전](#13-다음-버전)
14. [원본](#14-원본)

---

## 1. 이 기능이 하는 일

| 단계 | 하는 일 | 언제 | 진입점 |
|---|---|---|---|
| 0 | 대본 · 미션 · 직전 리뷰를 읽고 **이번 Take 에서 먼저 챙길 것 · 봐줄 것**을 정한다 (LLM) | Take 시작 전 한 번 | `plan_coaching` |
| 1 | 지금 상황을 보고 **말할지, 무엇을 말할지** 정한다 | 발표 중 1초마다 | `decide` · `decide_safe` |
| 2 | 열린 문제 구간과 아직 재지 못한 개입 효과를 **닫는다** | Take 종료 직후 한 번 | `finalize` |
| 3 | 쌓인 판단 기록을 **리뷰 근거**로 묶는다 | Take 종료 분석 | `build_review_evidence` |
| 4 | 가상 발표로 재생하고, 리뷰 근거를 정답과 비교해 **채점**한다 | 연구할 때만 (배포에는 없음) | `coach_lab` |

```
Take 시작 전:  plan_coaching(장별 대본 · 미션 · 기억 · 직전 리뷰, LLM) ─▶ 코칭 계획이 든 첫 coach_state

FE  시선 기록 · 음량 · 슬라이드 ─┐
BE  최근 15초 STT 단어 · 계획 · 미션 ─┼─▶ decide() ─▶ WAIT / IGNORE / INTERVENE
지난 응답의 coach_state ───────────┘        + 이벤트 + 새 coach_state + 상태 표시

Take 종료:  finalize() ─▶ 남은 이벤트 ─▶ build_review_evidence(이벤트 + 계획 · 미션 · 기억) ─▶ 리뷰 에이전트
```

**하는 것**

- Take 시작 전 LLM 이 **코칭 계획**을 세웁니다: 먼저 챙길 영역(가중치), 장 단위로 봐줄 영역(예: 수치를 읽어야 하는 장의 시선), 개입 상한.
  LLM 이 낸 계획은 코드가 검증하고 자르며, 실패하면 계획 없이 판단합니다
- 1초마다 측정값과 지난 기억(`coach_state`)을 받아 **행동 하나, 또는 말하지 않음**을 정합니다
- 7개 영역을 봅니다: 시선 · 말 속도 · 음량 · 침묵 · 군더더기 · 시간(장별 계획 대비) · 핵심 키워드(기본 꺼짐)
- **한 번에 하나만** 말합니다. 메시지 사이 15초, 같은 지시는 60초 쿨다운, 말하는 도중이면 문장이 끝날 때까지 최대 3초 기다립니다
- 개입하고 몇 초 뒤 **효과를 재서** 셋 중 하나를 고릅니다: 유지 격려 / 다른 방법 / 그 범위에서 그만두기
- 실전(EXAM) 모드에서는 말하지 않고 **기록만** 남깁니다
- 판단 기록을 **이벤트**로 내보내고, Take가 끝나면 리뷰 근거로 묶습니다. 숫자 · 판정 · 순위는 모두 코드가 계산합니다
- 읽지 않아도 되는 **상태 표시**(`indicators`: 시간 진행 · 속도 · 시선 · 음량)를 함께 줍니다

**하지 않는 것**

- 측정: 시선 추론(FE), 음량 측정(FE), 음성 인식(BE · Deepgram)은 다른 모듈이 합니다. 코치는 그 결과를 읽기만 합니다
- 저장: 아무것도 저장하지 않습니다. 기억은 응답에 담겨 나가고 BE가 다음 요청에 돌려보냅니다
- 문장 생성: 화면 문구는 템플릿입니다. 리뷰 근거도 꼬리표 · 상태 · 순위 · 목표값까지만 주고 문장은 리뷰 에이전트가 씁니다
- 대본 내용 전달 판정: 실시간으로는 필수 키워드 언급만 봅니다(기본 꺼짐). 문장 단위 전달은 [대본 전달도](../script-coverage-evaluation/README.md)가 합니다
- HTTP API: 함수만 있습니다. 서버에 붙이는 방법은 [DEPLOY.md](DEPLOY.md)
- LLM 클라이언트 만들기: 코어는 LLM 과 캐시를 인자로 받기만 합니다. research 는 `coach_lab.llm`, 서버는 service 의 LLM 설정으로 만들어 넘깁니다

---

## 2. 코드 구조 한눈에 보기

코드는 두 층이고, import는 위에서 아래로만 합니다.

```
src/coach_lab/     재생 · 실험: 가상 발표자 · 정답 리뷰 · 채점 · 계획 실험(LLM)    → research 에만 남음
      │ import
src/coach/         판단 코어: 1초 판단 · Take 종료 · 리뷰 근거      → 나중에 AI 서버로 폴더째 복사
```

| 층 | 하는 일 | 배포 때 |
|---|---|---|
| 코어 `coach` | Take 시작 전 코칭 계획, 1초마다 판단, Take 종료 정리, 리뷰 근거 만들기 | AI 서버로 그대로 옮김 |
| 재생 · 실험 `coach_lab` | 가상 발표 시나리오 재생, 정답과 비교한 리뷰 근거 채점, 실제 LLM 으로 계획 실험, 결과 파일 쓰기 | 옮기지 않음 |

### 코어의 규칙

코어만 떼어 서버로 옮겨도 코드를 고칠 필요가 없고, 같은 요청에는 언제나 같은 응답이 나오도록 지키는 규칙입니다.
`tests/unit/test_core_boundary.py`가 코어 파일을 읽어 자동으로 검사하므로, 어기면 테스트가 실패합니다.

| 규칙 | 이유 |
|---|---|
| 패키지 안에서는 **상대 import만** (`from .state import …`) | 폴더 이름이 `pitch_coach_ai.features.coach`로 바뀌어도 고칠 import가 없다 |
| **파일 · DB · 네트워크 · 환경변수를 직접 쓰지 않음** | 서버는 상태를 두지 않는다. 저장은 BE, 설정 파일 읽기는 코어 밖(`coach_lab`)이 맡는다 |
| **시계 · 난수를 쓰지 않음.** 시간은 요청의 `t_ms`뿐 | 같은 요청에 같은 응답이 나와야 재생 결과와 배포 결과가 같다 |
| **기억은 `coach_state`로 주고받음** | 코치는 지난 기억을 응답에 담아 돌려주고, BE가 보관했다가 다음 요청에 그대로 붙인다. AI 서버를 재시작하거나 늘려도 판단이 같다 |
| 의존성은 **pydantic 하나**. 표준 모듈은 계산만 하는 것(허용 목록) | 서버로 옮길 때 가져갈 라이브러리가 하나뿐이다 |
| **LLM · 캐시는 인자로 받음** (`.invoke(messages)` · `get` / `put`) | 코어는 키 · 네트워크를 모른다. LLM 라이브러리(langchain-openai)는 `coach_lab` 만 쓴다 |
| 로그는 표준 `logging`으로 **남기기만** | 어디로 보낼지(형식 · request_id · 출력)는 service 공통 로깅이 정한다 |

---

## 3. 폴더와 파일

### 3-1. 전체 트리

```
coach-agent/
├── README.md                이 문서
├── INTERFACE.md             코어의 입출력 (API 를 만들 때 기준)
├── DEPLOY.md                배포 가이드
├── pyproject.toml · uv.lock 라이브러리 목록 · 버전 고정 (Python 3.12)
├── .python-version          uv 가 쓸 Python (3.12)
├── .gitignore               outputs/ 제외
│
├── src/coach/               ── 판단 코어 ──
│   ├── version.py · schemas.py · vocab.py · config.py · state.py
│   ├── engine.py            진입점: decide · decide_safe · finalize
│   ├── evaluators/          ② 측정값 → 문제: gaze · voice · speech · timing
│   ├── candidates.py · eligibility.py · priority.py · policy.py · renderer.py    ③ ~ ⑦
│   ├── reflection.py · episodes.py · slides.py · events.py                       되돌아보기 · 기록
│   ├── review.py            Take 종료 뒤 리뷰 근거
│   └── planner.py · prompts/plan.py    Take 시작 전 코칭 계획 (LLM 초안 → 검증)
│
├── src/coach_lab/           ── 재생 · 실험 도구 ──
│   ├── paths.py · simulator.py · truth.py
│   ├── llm.py · cache.py    코칭 계획용 LLM 클라이언트 (ai/.env) · 응답 캐시 (SQLite)
│   └── replay.py · evaluate.py · plan_eval.py    실행 CLI (python -m coach_lab.replay / .evaluate / .plan_eval)
│
├── scenarios/               재생 시나리오 16개: 가상 발표 하나 + 기대 결과 (JSON)
│   └── plan/                코칭 계획 시나리오 5개: 장별 대본 · 직전 리뷰 + 계획에 대한 기대
├── reports/results/         리뷰 근거 · 코칭 계획 실험 결과 JSON (버전별로 커밋)
├── outputs/                 재생 결과 · 임시 실험 결과 · LLM 응답 캐시 (git 제외)
└── tests/
    ├── unit/                코어 테스트. 코어와 함께 서버로 간다
    ├── lab/                 재생 · 실험 도구 테스트. research 에만 남는다
    └── live/                실제 LLM 을 부르는 테스트 (기본 실행에서 빠짐, `-m live`)
```

### 3-2. 판단 코어 `src/coach/`

**계약 · 설정 · 기억**

| 파일 | 역할 | 주요 이름 |
|---|---|---|
| `version.py` | 버전 값과 올리는 때 | `SCHEMA_VERSION` · `POLICY_VERSION` · `STATE_VERSION` |
| `schemas.py` | 요청 · 응답 · 이벤트 · 리뷰 근거의 모양(pydantic). **계약의 원본** | `CoachRequest`, `CoachResponse`, `CoachReviewEvidence` |
| `vocab.py` | enum: 영역(type) · 행동(instruction) · action · 문제 · 이유 코드 | `FeedbackType`, `Instruction`, `Issue`, `Reason` |
| `config.py` | 기준값 · 가중치 · 문구 템플릿 · 전략 사다리. **바꿀 만한 숫자는 전부 여기** (몇몇 고정 심각도는 평가기 · `review.py` 안에 있다) | `CoachConfig`, `load_config`, `config_hash()` |
| `state.py` | `coach_state`의 모양과 읽기 · 쓰기. 버전이 다르면 새로 시작 | `CoachState`, `load_state`, `dump_state` |

**1초 판단: `engine.py`의 `decide()`가 아래 순서로 부릅니다**

| 순서 | 파일 | 하는 일 |
|---|---|---|
| ① | `slides.py` · `evaluators/speech.py`(`ingest`) | 장 전환 기록, 새로 확정된 STT 단어를 장별 글자 수 · 군더더기 · 키워드에 누적 |
| ② | `evaluators/` gaze → voice → speech → timing | 측정값 → 문제(심각도 · 신뢰도 · 근거). 말할지는 정하지 않는다. timing 은 speech 의 CPM 을 쓰므로 순서가 중요하다 |
| | `slides.py`(`accumulate`) | 지금 장의 시선 · CPM · 음량 · 데이터 덮개 누적 |
| | `reflection.py` | 잴 때가 된 개입의 효과 판정 → 전략 사다리 이동 · 격려 후보 |
| | `episodes.py` | 문제 구간 열기 · 이어가기 · 닫기 → `EPISODE` 이벤트 |
| ③ | `candidates.py` | 문제 → 행동 후보 (사다리 몇 번째 칸인지) |
| ④ | `eligibility.py` | 지금 말하면 안 되는 후보를 `WAITING` · `IGNORED` 로 |
| ⑤ | `priority.py` | 남은 후보에 점수 (0~100) |
| ⑥ | `policy.py` | `RulePolicy`: WAIT · IGNORE · INTERVENE, 문장 끝 기다리기 |
| ⑦ | `renderer.py` | instruction + 사다리 칸 → 템플릿 → 화면 문장 |
| ⑧ | `events.py` · `engine.py` | 이벤트 번호 매기기, 최근 60초 기록, 상태 표시, 새 `coach_state` |

**Take 시작 전 (v1.2)**

| 파일 | 하는 일 |
|---|---|
| `planner.py`(`plan_coaching`) | 계획 · 장별 대본 · 미션 · 기억 · 직전 리뷰 → LLM 메시지 → 캐시 확인 → LLM 초안(`PlanDraft`) → 검증(`validate_draft`) → 계획이 든 첫 `coach_state`. 실전 모드 · LLM 없음 · LLM 실패면 기본 계획 |
| `prompts/plan.py` | 계획 지시문 (`PLAN_SYSTEM_PROMPT`). 지시문 · 출력 스키마 · 모델로 계획 해시(`planner_hash`)를 만들어 캐시 키로 쓴다 |

**Take 종료 뒤**

| 파일 | 하는 일 |
|---|---|
| `engine.py`(`finalize`) | 재지 못한 효과를 `NOT_MEASURED` 로, 열린 문제 구간과 지금 장을 닫아 이벤트로 |
| `review.py` | 이벤트 → 측정 정리(장별 누적 · 문제 구간 · 지연 보정 · 병합) → 판정 `assess()`(순위 · 미션 · 기억 비교 · 영역 상태 · 다음 미션) → `CoachReviewEvidence` |

### 3-3. 재생 · 실험 도구 `src/coach_lab/`

| 파일 | 역할 |
|---|---|
| `paths.py` | 폴더 위치 모음 (시나리오 · 출력 · 결과) |
| `simulator.py` | 시나리오 모델, 가상 발표자(BE 처럼 15초 STT 창을 만들고 `coach_state`를 왕복시키며 코치 말에 반응), 잡음 3단계(`NOISE_PRESETS`), 발표자의 실제 상태 기록, 기대 결과 확인 `check_expect`. `raw=True` 면 FE · BE 요약 대신 원자료(시선 1초 기록 · 음량 레벨 · 표시 없는 단어)를 보낸다 |
| `truth.py` | 정답: 실제 상태로 만든 문제 구간 · 장별 누적 · 효과, 그리고 코치와 **같은 판정**(`assess`)으로 만든 정답 리뷰 |
| `replay.py` | 재생 CLI: 콘솔 요약 + `outputs/replay/<시나리오>.json`. `--config` 설정 JSON 은 여기서 읽는다 |
| `evaluate.py` | 리뷰 근거 실험 CLI: 채점, 규칙 변형 비교, 격자(`--sweep`), 틀린 사례(`--explain`) |
| `llm.py` | `ai/.env`(대본 전달도와 같은 변수)로 `PlanDraft` 구조화 출력 LLM 을 만든다. 재시도 없이 한 번, 요청 제한 20초 |
| `cache.py` | 코어의 `PlanCache` 를 `outputs/llm_cache.sqlite` 로. 반복 번호(sample)마다 따로 저장해 같은 질문을 여러 번 물을 수 있다 |
| `plan_eval.py` | 코칭 계획 실험 CLI: 계획 시나리오마다 여러 번 계획을 세워 채점하고, 계획으로 재생해 계획 없는 재생과 비교 ([6-8](#6-8-코칭-계획-실험)) |

### 3-4. 나머지

| 폴더 | 내용 |
|---|---|
| `scenarios/` | 가상 발표 16개. 형식과 목록은 [6-5](#6-5-재생-시나리오). `plan/` 은 코칭 계획 시나리오 5개 ([6-8](#6-8-코칭-계획-실험)) |
| `reports/results/` | `review_evidence.json`: 리뷰 근거 실험 지표와 격자. `config_hash` · `policy_version` 이 함께 들어 있다. 같은 코드면 같은 결과가 나와 커밋해 두고, 규칙을 바꾸면 `git diff` 로 비교한다. `coaching_plan.json`: 실제 LLM 으로 낸 코칭 계획 실험 결과 (LLM 답이라 다시 부르면 달라질 수 있어, 처음 부른 결과를 커밋한다) |
| `outputs/` | 재생 결과와 임시 실험 결과. 실행할 때마다 달라지는 판단 시간이 들어 있어 git 에 올리지 않는다 |
| `tests/unit/` | 코어 테스트: 계약 · 평가기 · 판단 · 되돌아보기 · 측정 수정 · 리뷰 근거 규칙 · 예외 · 코어 경계. `conftest.py` 의 `make_request` · `Session` 이 요청과 `coach_state` 왕복을 줄여 준다 |
| `tests/lab/` | 재생(시나리오 기대 결과 · 재현성 · state 크기 · 판단 시간), 실험(잡음 재현 · 성능 하한선), 경로 · 설정 파일, 계획 실험(가짜 LLM) |
| `tests/live/` | 실제 LLM 으로 계획 한 번 (과금). `pytest -m live` 로만 돈다 |

---

## 4. 실행 흐름 따라가기

### 4-1. Take 시작 전: 코칭 계획 (`plan_coaching`)

`scenarios/plan/17_plan_numbers_slide.json`(3번 장이 수치 표)으로 따라가 보면:

1. **메시지**: 장마다 목표 시간 · 필수 키워드 · 대본(1,500자까지)과 대본 속 숫자 개수를 JSON 으로 만든다. 3번 장은 숫자 16개, 다른 장은 0개.
   미션 · 반복 문제 · 직전 리뷰 요약(순위 매긴 문제 · 영역 상태 · 개입 수 · 효과율)도 함께 넣는다
2. **캐시**: 메시지 해시와 계획 해시(지시문 · 출력 스키마 · 모델)가 같으면 저장된 답을 쓴다
3. **LLM**: 지시문 + 메시지 → `PlanDraft` 모양의 초안. 예: `relax: [{type: GAZE, slide_number: 3, why: "3장은 수치가 16개 …"}]`
4. **검증**: 장이 계획에 있나, 봐줄 수 있는 영역인가(시선 · 군더더기 · 키워드), 이번 미션 영역은 아닌가, 개수 · 가중치 · 개입 상한 범위 ([5-6](#5-6-코칭-계획-규칙-plannerpy)). 뺀 항목은 `dropped` 에 이유와 함께 남긴다
5. **응답**: 검증한 계획을 담은 첫 `coach_state`. BE 는 이것을 첫 `decide` 요청에 붙인다
6. **1초 판단에서**: 3번 장에서 시선 문제가 나와도 후보가 `IGNORED`(`PLAN_RELAXED`)가 되어 말하지 않는다. 다른 장의 시선은 그대로 지적한다

### 4-2. 1초 판단 (`decide`)

```
decide(request, config=None, policy=None)            engine.py
├─ load_state(coach_state)                           지난 기억. 이미 처리한 t_ms 면 WAIT · STALE_TICK 으로 끝
├─ _open_tick                                        장 추적, STT · 오디오 상태, 지난 틱과의 간격
├─ ① slides.switch · speech.ingest                   장을 떠났으면 SLIDE 이벤트, 새로 확정된 단어 누적
├─ ② run_all: gaze → voice → speech → timing         측정값 → 문제(Detection: 심각도 · 신뢰도 · 근거)
│     slides.accumulate                              지금 장의 시선 · CPM · 음량 누적
│     reflection.resolve                             잴 때가 된 개입의 효과 → OUTCOME · STRATEGY 이벤트
│     episodes.observe                               문제 구간 열기 · 이어가기 · 닫기 → EPISODE 이벤트
├─ ③ candidates.build                                문제 → 행동 후보 (사다리 칸)
├─ ④ eligibility.apply                               지금 말하면 안 되는 후보 → WAITING · IGNORED
├─ ⑤ priority.score                                  후보마다 점수
├─ ⑥ RulePolicy.select                               WAIT · IGNORE · INTERVENE
├─ ⑦ _intervene → render                             INTERVENE 면 문장을 만들고 INTERVENTION 이벤트
└─ ⑧ WAITING · IGNORED 후보 → SUPPRESSED 이벤트(같은 문제는 10초에 한 번), 최근 60초 기록, indicators, 새 coach_state
```

예를 들어 `scenarios/05_time_behind.json` 을 재생하면 52초에 처음 말합니다 ([INTERFACE.md](INTERFACE.md)의 요청 · 응답 예시가 이 틱입니다).

1. **기록 갱신**: 2번 장에 19초째 머무는 중이고, 확정된 단어가 장별 글자 수에 쌓입니다.
2. **평가기**: 시간 평가기가 남은 내용 134.6초 ÷ 남은 시간 128초 = r 1.05 를 계산해 늦는 중(`BEHIND_SCHEDULE`)으로 봅니다. 시선 · 음량 · 속도는 문제가 없습니다.
3. **후보**: 지금 말 속도 240 CPM × r 1.05 = 252 CPM 은 '빠름' 기준 350 을 넘지 않아, 속도로 따라잡을 수 있습니다. 그래서 사다리 첫 칸 `SPEED_UP` 입니다 (넘었으면 `CONDENSE` 부터).
4. **적격성 · 점수**: 걸리는 이유가 없고 점수는 52 입니다.
5. **행동 선택**: 40점을 넘고 문장 사이라 `INTERVENE` 입니다.
6. **문장**: "조금만 빠르게 — 남은 3장, 2분 8초". 15초 뒤 효과를 잴 예약을 하고, 응답의 `events` 에 `INTERVENTION` 이 실립니다.

### 4-3. Take 종료 → 리뷰 근거

```
finalize(request)                                    request = take_id · 종료 t_ms · 마지막 coach_state
├─ 재지 못한 개입 효과 → OUTCOME(NOT_MEASURED)
├─ 열린 문제 구간 → EPISODE(closed_by=TAKE_END). 개입도 없고 2초도 안 된 깜빡임은 버림
└─ 지금 장 → SLIDE

build_review_evidence(take_id, events, plan=, missions=, memory=)
├─ 측정 정리   SLIDE → 장별 누적,  EPISODE → 문제 구간
│             창 지연 되돌리기 → 같은 영역 · 장 · 신뢰도 구간 병합 → 깜빡임 빼기 → 믿을 수 없던 구간은 문제에서 뺌
├─ 판정 assess()   문제마다 부담 → 순위 · 이전 Take 비교 · 미션 판정 · 영역 상태 · 다음 미션 후보
└─ CoachReviewEvidence   + 장별 표 · 문제 구간 · 개입과 효과 · 강점 · 데이터 품질
```

판정 층(`assess`)은 순수 함수라, 실험에서 정답 데이터에 **같은 판정**을 돌립니다. 그래서 리뷰 근거가 정답과 다르면
그 차이가 측정(잡음 · 창 지연 · 구간 처리)에서 왔는지 판정 규칙에서 왔는지 나눠 볼 수 있습니다.

### 4-4. research: 재생과 실험

```
python -m coach_lab.replay
└─ 시나리오마다 simulator.run()
   ├─ 1초마다 Presenter.request(t, coach_state)   가상 발표자가 FE · BE 처럼 current 를 만든다
   ├─ decide()                                    → feedback 이 있으면 발표자가 반응(reactions)
   ├─ finalize() → build_review_evidence()
   └─ check_expect()                              시나리오의 expect 와 비교 → PASS / 어긋난 이유

python -m coach_lab.evaluate
└─ 시나리오 16 × (clean 1 + noisy · harsh 각 seed 5) = 176 Take 를 한 번씩 재생
   ├─ truth.py   발표자의 실제 상태 × 같은 판정 규칙 = 정답 리뷰
   └─ 리뷰 근거 규칙 변형 A ~ D 마다 근거만 다시 만들어 정답과 채점 → 표 + JSON
```

리뷰 설정(`config.review`)은 실시간 판단에 쓰이지 않으므로, 발표는 한 번만 재생하고 변형마다 리뷰 근거만 다시 만듭니다.

---

## 5. 판단 규칙

### 5-1. 문제와 행동

이 표가 v1 실시간 규칙의 전부입니다. 숫자는 `config.py` 의 기본값이고, 실제 발표 데이터로 검증하지 않은 잠정값입니다.

| type | 문제 | 조건 | 지속 | 행동 사다리 (효과 없으면 다음 칸) | 다 써 보면 |
|---|---|---|---|---|---|
| GAZE | `GAZE_SCRIPT` | 보인 시간 중 대본 응시 ≥ 70%, 또는 대본 연속 응시 ≥ 5초이면서 창 비율 ≥ 60% | 3초 | `LOOK_AT_CAMERA` "대본보다 청중을 조금 더 바라보세요" → `LOOK_AT_CAMERA` "문장을 시작할 때만이라도 고개를 들어 청중을 보세요" | 그 장에서 그만 |
| SPEED | `PACE_FAST` | 15초 CPM > 350 (말한 시간 4초 · 단어 5개 이상일 때만 잼) | 5초 | `SLOW_DOWN` "조금 천천히 말해 보세요" → `SLOW_DOWN` "문장 끝에서 한 박자 쉬고 이어가 보세요" | Take 에서 그만 |
| VOLUME | `VOLUME_LOW` | 말하는 동안 최근 5초 평균(표본 3개 이상)이 기준 대비 −6dB 미만 | 5초 | `SPEAK_LOUDER` "목소리를 조금 더 크게 내 보세요" → `SPEAK_LOUDER` "뒷자리 청중에게 말한다고 생각하고 소리를 키워 보세요" | Take 에서 그만 |
| PAUSE | `LONG_SILENCE` | 침묵 > 5초 (오디오가 되살아난 뒤부터만 셈) | — | `RESUME` "다음 문장으로 이어가 보세요" | 그만두지 않음 |
| FILLER | `FILLER_FREQUENT` | 최근 60초 군더더기 ≥ 6회 | — | `REDUCE_FILLER` "'음' 대신 잠시 호흡하고 이어가세요" → `REDUCE_FILLER` "말을 고를 땐 소리 내지 말고 잠깐 멈춰 보세요" | Take 에서 그만 |
| CONTENT | `KEYWORD_MISSING` (기본 꺼짐) | 이 장을 80% 말했는데 필수 키워드를 아직 안 말함 | — | `MENTION_KEYWORD` "이 슬라이드의 핵심인 '로컬 처리'를 언급해 보세요" | 장마다 1회 |
| TIME | `BEHIND_SCHEDULE` | r ≥ 1.05. CPM × r ≤ 350 이면 `SPEED_UP` 부터, 넘으면 `CONDENSE` 부터 | — | `SPEED_UP` "조금만 빠르게 — 남은 2장, 1분 5초" → `CONDENSE` "핵심만 말하고 넘어가세요 — 남은 2장, 1분 5초" → `WRAP_UP` "결론으로 넘어가 마무리하세요" | 마지막 칸에 머묾 |
| TIME | `AHEAD_OF_SCHEDULE` | 지금 속도로 예상한 종료 < 허용 최소(`min_ms`, 없으면 목표 × 0.85). 발표 20% 지난 뒤부터 | 10초 | `SLOW_DOWN` "시간 여유가 있어요. 천천히 말해도 돼요" | Take 에서 그만 |
| TIME | `SLIDE_OVER` | 이 장 체류 > 장 목표 × 1.5 (마지막 장 제외) | — | `MOVE_ON` "이 장은 목표보다 25초 넘었어요 — 정리하고 다음 장으로" → `MOVE_ON` "지금 다음 장으로 넘어가세요" | 그 장에서 그만 |
| TIME | `FINAL_MINUTE` | 0 < 남은 시간 ≤ 60초, 남은 장 ≥ 2(알 수 없으면 통과), **장별 계획이 없거나 실제로 늦을 때만** | — | `WRAP_UP` "남은 시간이 짧으니 결론으로 넘어가세요" | 1회 |
| TIME | `TIME_OVER` | 경과 > `max_ms` (없으면 목표) | — | `WRAP_UP` "제한 시간을 넘겼어요 — 한 문장으로 마무리하세요" | 1회 |
| (교정한 영역) | `IMPROVED_AFTER_FEEDBACK` | 시선 · 속도 · 음량 · 군더더기 교정이 효과 있음 | — | `CONTINUE` "좋아요, 지금처럼 이어가세요" — 심각도 0.45 로 낮게, 20초 안에만 | — |

- **r (필요 속도 비율)** = 남은 내용 시간 ÷ 남은 시간. 남은 내용 시간 = 이 장 목표 × (1 − 진행도) + 뒤 장 목표 합
- **진행도** = 이 장에서 말한 글자 수 ÷ 이 장 대본 글자 수. STT 가 나쁘거나 이 장에서 STT 가 끊긴 적이 있으면 이 장에 머문 시간 ÷ 장 목표
- **CPM** = 글자 수(공백 제외) ÷ 실제로 말한 시간(단어별 end − start 합) × 60초. 군더더기는 뺍니다
- **지속**은 센서를 믿을 수 있는 상태로 이어진 시간입니다. 센서가 나빴다가 좋아지면 처음부터 다시 셉니다
- 늦는 중에는 `SLOW_DOWN` 을 하지 않습니다 (`TIME_PRESSURE`). 시간을 더 모자라게 만들기 때문입니다
- 말이 느린 것 단독은 알리지 않습니다. 실제로 늦어질 때 TIME 으로 다룹니다
- `type`(원인 영역)과 `instruction`(하라는 행동)은 1:1 이 아닙니다. 같은 TIME 이라도 `SPEED_UP` · `CONDENSE` · `WRAP_UP` 이 나오고, 같은 `SLOW_DOWN` 이라도 원인이 SPEED 일 수도 TIME 일 수도 있습니다

### 5-2. 평가기 — 계산 세부

| 평가기 | 계산 | 센서를 믿을 수 없을 때 |
|---|---|---|
| 시선 `evaluators/gaze.py` | 1초 기록(`records`)이 오면 최근 10초 창의 라벨별 비율 · 지금 라벨 · 이어진 시간을 직접 계산한다 (FE 가 만든 요약도 받는다). 기록이 없는 시간과 `UNMEASURED` 는 UNCERTAIN 처럼 '측정 못 함', `SCREEN` · `OTHER` 는 '보였지만 대본이 아님'. `script_ratio` = 대본 라벨 비율 ÷ (1 − 측정 못 함). **보인 시간 중** 비율이라 얼굴이 자주 안 잡혀도 문제를 놓치지 않는다. 연속 응시만으로 잡을 때는 창 비율 ≥ 60% 도 요구한다 (라벨 흔들림으로 5초 연속이 우연히 생긴다). Take 시작 뒤 5초는 표본이 적어 지적하지 않는다 | 지금 UNCERTAIN 비율과 최근 10초 평균 중 **나쁜 쪽** > 50% → `SENSOR_UNUSABLE`, 지표 비움. 나빠질 때는 바로, 좋아질 때는 천천히 |
| 음량 · 침묵 `evaluators/voice.py` | 말하는 동안의 최근 5초 평균 dB (표본 3개 이상, 평소 목소리 대비). FE 가 레벨(`level_db`, dBFS)만 보내면 `baseline_db` 와의 차이를, 그것도 없으면 Take 첫 발화 15초의 중앙값을 기준으로 잡아 쓴다 (기준을 잡기 전에는 판단하지 않음). 침묵은 오디오가 되살아난 뒤부터만 센다 | 오디오 정지 → 소리 판단 전부 `SENSOR_UNUSABLE` |
| 말 `evaluators/speech.py` | 15초 CPM, 효과용 6초 CPM, 60초 · 30초 군더더기 수, 장별 말한 글자 수, 키워드 (확정 단어만 누적). 확정이 늦게 온 단어는 **말한 시각의 장**에 붙인다. 군더더기는 BE 표시를 쓰고, 표시가 없으면 소리뿐인 간투사(음 · 어 · 으 · 엄 · 흠 · 아 · 에)만 센다 | `stt_status` ≠ ok 또는 오디오 정지 → STT 판단 `SENSOR_UNUSABLE`, 그 장은 STT 가 끊긴 장으로 표시. **돌아온 뒤에는 돌아온 뒤의 단어로만** 잰다 |
| 시간 `evaluators/timing.py` | r, 예상 종료(지금까지 소화한 계획 시간 대비 실제 경과로 남은 내용을 환산), 장 예상 소요, 남은 장 수 | STT 가 끊긴 장은 진행도를 시간으로 재고 신뢰도를 낮춘다(0.9 → 0.6) |

**'빠름'은 r 이 아니라 예상 종료로 봅니다.** 끝으로 갈수록 r 의 분모(남은 시간)가 작아져, 몇 초 앞선 것만으로
r 이 크게 내려갑니다. 허용 범위 안에 끝날 발표자에게 "천천히"를 말하게 되므로, '지금 속도면 허용 최소보다 일찍 끝나나'를 봅니다.
'늦음'은 그대로 r 입니다. "몇 % 빨라져야 하나"가 곧 r 이라서입니다.

### 5-3. 적격성 · 우선순위 · 행동 선택

**적격성 필터** (`eligibility.py`): 점수와 상관없이 적용됩니다. 걸린 이유는 **전부** 남깁니다. 실전 모드에서 버린 후보도
다른 이유까지 남아야 리뷰가 "이때 이런 게 걸렸다"를 보여줄 수 있습니다.

| 결과 | 이유 | 규칙 |
|---|---|---|
| IGNORED (기다려도 소용없음) | `EXAM_MODE` | 실전 모드 |
| | `SENSOR_UNUSABLE` | 그 영역 센서를 믿을 수 없음 |
| | `LOW_CONFIDENCE` | 신뢰도 < 0.6 |
| | `TIME_PRESSURE` | 늦는 중에 `SLOW_DOWN` |
| | `STRATEGY_EXHAUSTED` | 그 범위에서 사다리를 다 써 봄 |
| | `ALREADY_DELIVERED` | 1회만 하는 안내를 이미 함 |
| | `PLAN_RELAXED` · `BUDGET_EXHAUSTED` | 코칭 계획이 참으라고 함 · 개입 횟수 상한 ([5-6](#5-6-코칭-계획-규칙-plannerpy)) |
| WAITING (나중에 다시) | `NOT_PERSISTENT` | 지속시간 미달 |
| | `MIN_GAP` | 직전 메시지 뒤 15초 안 |
| | `COOLDOWN` | 같은 instruction 을 한 지 60초 안 |

**우선순위** (`priority.py`)

```
priority = round( min(1, 심각도 × 신뢰도 × 지속 × 미션 × 반복 × 계획 × 시간 × 악화 × 새로움) × 100 )
```

| 가중치 | 값 | 붙는 이유 코드 |
|---|---|---|
| 심각도 | 기준값에서 0.5, '아주 나쁨'에서 1.0 으로 선형 | — |
| 지속 | 요구 지속시간을 넘긴 만큼 최대 ×1.3 (15초에서 최대) | 5초 이상 넘기면 `PERSISTENT` |
| 미션 | 같은 type(· 슬라이드) ×1.3, 지금 값이 미션 target 을 벗어나면 ×1.5 | `MISSION_RELEVANT`, `MISSION_AT_RISK` |
| 반복 | 이전 Take 기억에 같은 type(· 슬라이드) ×1.2 | `RECURRING` |
| 계획 | 코칭 계획 focus 의 weight (0.5~2.0 으로 자름) | 1 보다 크면 `PLAN_FOCUS` |
| 시간 | TIME 후보만, 발표가 진행될수록 최대 ×1.5 | 남은 시간 ≤ 60초면 `TIME_CRITICAL` |
| 악화 | 30초 전보다 나빠짐 ×1.1 (대본 응시 +0.15, CPM +30, dB −3, r +0.1) | `WORSENING` |
| 새로움 | 같은 문제로 이미 n 번 말했으면 × max(0.55, 1 − 0.15n) | — |

**행동 선택** (`policy.py` 의 `RulePolicy`)

| 상황 | action | reason_codes |
|---|---|---|
| 후보 없음 | `WAIT` | `NO_CANDIDATE` |
| 적격 후보가 있고 1등 ≥ 40점, 발표자가 말이 끊긴 틈 | `INTERVENE` | 문제 코드 + 1등의 가중치 이유 |
| 위와 같지만 말하는 중 | `WAIT` (최대 3초) | `WAITING_FOR_PAUSE` → 3초가 지나면 `INTERVENE` + `PAUSE_TIMEOUT` |
| 적격 후보가 있지만 1등 < 40점 | `IGNORE` | `LOW_PRIORITY` |
| 적격 후보 없음, WAITING 후보 있음 | `WAIT` | 그 후보의 기다림 이유 |
| 모든 후보가 IGNORED | `IGNORE` | 1등 후보의 버림 이유 |

'말이 끊긴 틈' = 침묵 ≥ 300ms, 또는 문장 끝 신호(`utterance_end_ms`)가 1초 안. 판단할 신호가 없으면 틈으로 봅니다.
1등이 아닌 적격 후보는 `OUTRANKED` 로 남고 15초 뒤 다시 경쟁합니다. **한 번에 지시는 하나뿐입니다.**

후보 하나 = 문제 하나이고, 행동은 `사다리[max(전략 단계, 최소 단계)]` 입니다. 전략 단계는 효과가 없을 때마다 올라가고,
최소 단계는 평가기가 정합니다. 늦었는데 지금 속도에 r 을 곱한 값이 '빠름' 기준을 넘으면 `SPEED_UP` 을 건너뛰고 `CONDENSE` 부터 씁니다
(`SPEED_LIMIT_EXCEEDED`). 전략은 범위마다 따로입니다. 시선 · 장 초과 · 키워드는 **슬라이드마다**, 나머지는 Take 전체입니다.

### 5-4. 되돌아보기

개입마다 효과를 잴 시점을 예약하고(`reflection.py`), 그 시점에 같은 지표를 다시 봅니다. 시선 · 속도 · 음량 · 시간 지표의 전후 값은 순간값이 아니라 **최근 3초 평균**입니다.

| 문제 | 언제 | 효과 있음 |
|---|---|---|
| `GAZE_SCRIPT` | 12초 뒤 | 대본 응시 < 60% (탐지 기준 70% 보다 낮게 내려옴) |
| `PACE_FAST` | 10초 뒤 | 6초 CPM ≤ 330, 또는 10% 이상 줄어듦 |
| `VOLUME_LOW` | 8초 뒤 | 기준 이상으로 돌아옴, 또는 3dB 이상 커짐 |
| `FILLER_FREQUENT` | 30초 뒤 | 뒤 30초 군더더기 ≤ 앞 30초의 절반 |
| `LONG_SILENCE` | 5초 뒤 | 다시 말함 (지금 침묵이 개입 뒤에 시작됐으면 그 사이 말을 한 것) |
| `BEHIND_SCHEDULE` | 15초 뒤 | r < 1.05, 또는 0.05 이상 줄어듦 |
| `AHEAD_OF_SCHEDULE` | 10초 뒤 | 6초 CPM 이 10% 이상 줄었거나 예상 종료가 허용 범위로 돌아옴 |
| `SLIDE_OVER` | 15초 뒤 | 다음 장으로 넘어감 |
| `KEYWORD_MISSING` | 15초 뒤 | 그 키워드를 말함 (다음 장으로 넘어가서 말해도 인정) |
| `FINAL_MINUTE` · `TIME_OVER` | — | 재지 않음 |

- **효과 있음** → 같은 방법 유지 + `CONTINUE` 후보 (20초 안에, 그 문제가 다시 나타나지 않았을 때만)
- **효과 없음** → 사다리 다음 칸 (`STRATEGY` 이벤트 `ESCALATED`). 마지막 칸에서도 없으면 그 범위에서 그만 (`GAVE_UP`)
- **잴 수 없음**(센서가 나빠짐) → 전략을 바꾸지 않습니다. 모르는 것으로 멀쩡한 방법을 포기하지 않으려는 것입니다

시선은 '얼마나 줄었나'를 쓰지 않고 60% 아래로 내려왔는지만 봅니다. 개입은 측정값이 잡음으로 높게 튄 순간에 일어나기 쉬워
그 뒤엔 저절로 내려오기 때문입니다(평균으로의 회귀). 속도는 6초 CPM 으로 잽니다. 15초 창에는 개입 전의 빠른 단어가 남아 효과를 못 봅니다.

**문제 구간** (`episodes.py`): 같은 문제가 이어지는 동안을 한 구간으로 묶고, 마지막으로 잡힌 뒤 3초가 넘도록 안 보이면 닫습니다(1~2초 깜빡임으로 끊기지 않게).
닫히면 `EPISODE` 이벤트가 됩니다. 개입하지 못한 구간도 남기고(2초 미만이고 아무 일도 없던 깜빡임만 뺍니다), 센서를 믿을 수 있던 시간 ·
없던 시간과 평균 심각도를 함께 남깁니다. 리뷰가 근거 없는 지적을 거르고 부담을 재는 데 씁니다.

### 5-5. 리뷰 근거 규칙 (`review.py`)

**① 문제 구간 정리**: 값은 실험의 격자([9](#9-버전별-결과표))로 골랐습니다.

| 단계 | 규칙 | 기본값 |
|---|---|---|
| 신뢰도 | 센서를 믿을 수 있던 시간이 50% 미만인 구간은 `UNRELIABLE`: 문제 · 순위 · 미션에서 뺀다 | 50% |
| 창 지연 보정 | 평가기의 창 때문에 늦게 잡힌 만큼 되돌린다. 시선: 시작 − 0.7 × 창, 끝 − 0.3 × 창. 속도 · 음량 · 군더더기는 시작과 끝을 각각 7.5초(15초 창의 절반) · 2.5초 · 30초 앞당긴다. 침묵은 시작만 5초 앞당긴다 | ×1.0 |
| 병합 | 같은 영역 · 같은 장 · 같은 신뢰도 분류의 구간이 10초 안에 다시 시작되면 하나로 | 10초 |
| 깜빡임 | 실제로 잡힌 시간(보정 전)이 3초 미만이고 개입도 없던 구간은 뺀다 | 3초 |

**② 부담과 순위**

- **부담** = 구간 길이(보정 후) × 평균 심각도. 최고 심각도가 아니라 평균인 것은 잡음 많은 지표(CPM)의 최고값이 부풀어 순위를 흔들었기 때문입니다
- **시간**은 구간이 아니라 장별 실제 시간에서 잽니다. 장 체류가 목표 × 1.1 을 넘으면 문제이고, 부담은 (체류 − 장 목표) 초 × 심각도입니다.
  Take 전체는 `max_ms`(없으면 목표)를 넘은 초 × 1.0, `min_ms` 보다 일찍 끝난 초 × 0.75 입니다
- **키워드**는 빠뜨린 키워드 하나 = 10초
- 어느 영역이든 부담 3 미만은 문제로 보지 않습니다
- **순위 점수** = 부담 × (이전 Take 에도 있었으면 1.5) × (포기했으면 1.3) × (그 영역 미션이 실패 · 부분 달성이면 1.2)
- 순위는 **영역 합계가 먼저**이고, 같은 영역 안에서는 점수가 큰 장이 먼저입니다(같으면 장 번호 순). 시선처럼 장마다 나뉘는 문제도 '다음에 먼저 고칠 영역'이 흔들리지 않게

**③ 영역 상태** (위에서 먼저 걸리는 것)

| 상태 | 조건 |
|---|---|
| `NOT_EVALUABLE` | 그 영역 데이터가 Take 의 50% 미만 (시선 · STT · 오디오 덮개, 음량은 말한 시간 중 음량을 잰 비율도, 키워드는 필수 키워드가 있어야) |
| `PRIORITY` | 영역 점수 상위 2개 |
| `IMPROVED` | 이전 Take 기억의 문제가 이번엔 없음(`RESOLVED`), 또는 그 영역 미션을 달성하고 이번 문제도 없음 |
| `STRENGTH` | 문제 없음 |
| `STABLE` | 그 밖 (작은 문제는 있지만 우선은 아님) |

**④ 미션 판정**: 목표를 만족하면 `ACHIEVED`, 허용 오차 안으로 놓치면 `PARTIAL`, 그보다 많이 놓치면 `FAILED`, 판단할 수 없으면 `NOT_EVALUABLE` 과 이유입니다.
지표별 범위 · 허용 오차와 이유 코드는 [INTERFACE.md](INTERFACE.md)에 있습니다.

**⑤ 이전 Take 비교**: 기억 한 줄마다 같은 영역(· 장)에 문제가 있으면 `RECURRING`, 없고 판단할 수 있으면 `RESOLVED`,
판단할 수 없으면(데이터 부족 · 그 장에 가지 못함) `UNKNOWN`. 이번 문제는 기억에 있으면 `RECURRING`, 없으면 `NEW`.

**⑥ 다음 미션 후보**: 순위대로 영역당 하나, 최대 3개, 판단할 수 없는 영역은 뺍니다. 그 영역 부담의 60% 이상이 한 장에 몰려 있으면
장 단위, 아니면 Take 단위입니다. 키워드와 장 시간 초과는 늘 장 단위이고, Take 전체 시간 초과 · 미달은 Take 단위입니다. 목표는 **한 번에 도달할 만큼만** 잡습니다.

| 영역 | 목표 |
|---|---|
| 시선 | `script_ratio` ≤ max(0.3, 이번 값 − 0.2). 0.9 였으면 0.7 부터 |
| 속도 | `cpm` ≤ 350 |
| 음량 | `relative_db` ≥ −6 |
| 군더더기 | `filler_per_min` ≤ max(2, 이번 값 ÷ 2) |
| 침묵 | `long_silence_count` ≤ 0 |
| 키워드 | `keyword_coverage` ≥ 1.0 |
| 시간 | 장: `slide_duration_ms` ≤ 장 목표 × 1.1 · Take: `duration_ms` ≤ `max_ms` (일찍 끝났으면 ≥ `min_ms`) |

### 5-6. 코칭 계획 규칙 (`planner.py`)

LLM 은 초안만 냅니다. 코치가 쓸 계획은 아래 규칙으로 검증하고 자른 것입니다 (`config.planner` · `config.policy`).

| 항목 | 규칙 | 어기면 |
|---|---|---|
| 집중 (focus) | 계획에 있는 장이거나 Take 전체, 같은 (영역, 장) 한 번, 3개까지. 가중치는 0.5 ~ 2.0 으로 자른다 | 빼고 `dropped` 에 이유 |
| 봐주기 (relax) | 장을 지정해야 하고, 시선 · 군더더기 · 키워드만 (시간 · 속도 · 음량 · 침묵은 늘 챙긴다). 이번 미션 영역 · 집중 영역과 겹치면 안 된다. 3개까지 | 빼고 `dropped` 에 이유 |
| 개입 상한 | 직전 Take 보다 적게 말하게 할 때만. 5 보다 작으면 5 로 올리고, 그 값이 직전 Take 개입 수 이상이거나 직전 리뷰가 없으면 상한을 두지 않는다 | 빼고 `dropped` 에 이유 |
| 이유 (why) | 200자까지 (`coach_state` 에 실려 매초 오간다) | 자른다 |
| 실패 | 실전 모드(`EXAM_MODE`), LLM 없음(`NO_LLM`), LLM 예외 · 출력 형식 오류(`LLM_ERROR`) | 기본 계획 + `fallback_reason` |

계획은 1초 판단에 두 곳으로만 들어갑니다: 우선순위의 '계획' 가중치(`PLAN_FOCUS`, [5-3](#5-3-적격성--우선순위--행동-선택))와
적격성의 참을 이유(`PLAN_RELAXED` · `BUDGET_EXHAUSTED`). 계획이 없으면(기본 계획) 판단은 coach-v1.1 과 같습니다.

---

## 6. 실행법

### 6-1. 준비

```bash
cd ai/research/coach-agent
uv sync                          # Python 3.12, 라이브러리 + 개발 도구(pytest · ruff) 설치
```

- `.venv`는 git에 없습니다. 클론하거나 폴더를 옮긴 뒤에는 `uv sync`로 다시 만듭니다.
- 1초 판단 · 재생 · 리뷰 근거 실험은 LLM을 쓰지 않아 `ai/.env`가 필요 없습니다. 코칭 계획 실험([6-8](#6-8-코칭-계획-실험))과 live 테스트만
  `ai/.env`의 `OPENAI_API_KEY` · `OPENAI_MODEL` · `OPENAI_BASE_URL`(대본 전달도와 같은 변수)을 씁니다.
- **Windows**: 이 PC에서는 `pytest.exe` 같은 실행 파일이 앱 제어로 막혀 있어 `uv run python -m …` 형태로 부릅니다.
  콘솔 한글이 깨지면 `PYTHONIOENCODING=utf-8`을 설정하세요.

### 6-2. 테스트 · 린트

```bash
uv run python -m pytest                 # 전부 (약 10초)
uv run python -m pytest tests/unit      # 코어만 (코어 경계 검사 포함)
uv run python -m pytest tests/lab       # 재생 · 실험 도구 (실험 하한선 포함)
uv run python -m pytest -m live         # 실제 LLM 으로 계획 한 번 (과금, 기본 실행에서 빠짐)
uv run ruff check . && uv run ruff format --check .   # backend 와 같은 규칙
```

### 6-3. 재생

```bash
uv run python -m coach_lab.replay                                   # scenarios/*.json 전부
uv run python -m coach_lab.replay scenarios/05_time_behind.json -v  # 하나, WAIT 아닌 판단까지 전부
uv run python -m coach_lab.replay --config my_override.json         # 설정을 바꿔 다시 재생
uv run python -m coach_lab.replay --raw                             # FE · BE 요약 대신 원자료를 입력으로
```

재생 출력 예 (판단 시간은 실행할 때마다 조금씩 다릅니다):

```
━━ 03_gaze_gave_up  [PASS]
   2번 장 내내 대본만 봄, 지적해도 안 바뀜 → 다른 방법 → 그 장에서 포기 → 리뷰로. 사다리를 3분 안에 다 보려고 쿨다운을 20초로 줄였다
       39초 장2  LOOK_AT_CAMERA  대본보다 청중을 조금 더 바라보세요
                 └ GAZE_SCRIPT, WORSENING
     1분 2초 장2  LOOK_AT_CAMERA  문장을 시작할 때만이라도 고개를 들어 청중을 보세요
                 └ GAZE_SCRIPT, ESCALATED, PERSISTENT, WORSENING, PAUSE_TIMEOUT
       51초 전략  ESCALATED GAZE_SCRIPT (장2) LOOK_AT_CAMERA.default → LOOK_AT_CAMERA.sentence_start
    1분 14초 전략  GAVE_UP GAZE_SCRIPT (장2) LOOK_AT_CAMERA.sentence_start
   ── 개입 2 (격려 0) · 효과 0/2 (0%) · 포기 1 · 문제 구간 1 (미대응 0)
   ── 172틱 · decide p50 0.22ms / p95 0.329ms · coach_state 13364B
   ── 참은 이유 {'COOLDOWN': 4, 'MIN_GAP': 3, 'NOT_PERSISTENT': 2, 'STRATEGY_EXHAUSTED': 2}
```

`outputs/replay/<시나리오>.json` 에 이벤트 전체와 리뷰 근거(`review_evidence`)가 남습니다. 리뷰 에이전트 입력이 어떻게 생겼는지 볼 때 여기를 보세요.

### 6-4. 리뷰 근거 실험

```bash
uv run python -m coach_lab.evaluate                       # clean 1회 + noisy · harsh 각 seed 5, 변형 A ~ D 비교 (약 15초)
uv run python -m coach_lab.evaluate --seeds 3 --explain D # 변형 D 의 틀린 사례를 하나씩 출력
uv run python -m coach_lab.evaluate --noise noisy scenarios/14_recurring_persists.json

# 커밋하는 결과는 이 두 명령으로만 다시 만든다 (약 35초 · 15초). 바뀌었으면 git diff 로 확인
uv run python -m coach_lab.evaluate --sweep --out reports/results/review_evidence.json  # FE 요약 입력, 격자 포함
uv run python -m coach_lab.evaluate --raw --out reports/results/review_evidence_raw.json # 원자료 입력
```

| 지표 | 뜻 |
|---|---|
| 구간 재현율 · 정밀도 · F1 | 센서가 볼 수 있던 실제 문제 구간을 리뷰 근거가 문제로 말했나 / 리뷰가 말한 구간이 실제 문제였나 |
| 근거 없는 지적 / Take | 실제 문제가 아니었거나 센서를 믿을 수 없던 곳을 문제로 말한 수 |
| 구간 IoU · 시작 오차 · 조각남 | 맞춘 구간의 시간 겹침, "몇 분 몇 초부터"의 오차, 실제 문제 하나를 몇 조각으로 말했나 |
| 미션 · 영역 상태 · 기억 비교 정확도 | 정답 판정과 같은 비율 |
| 1순위 영역 · 미션 일치 | 가장 먼저 고칠 영역 · 첫 다음 미션이 정답과 같은 비율 |
| 거짓 강점 / Take | 실제 문제가 있던 영역을 '문제 없음'이라고 한 수 |
| 효과 판정 정확도 · 불필요한 개입률 | 실시간 지표: 개입 효과 판정이 발표자의 실제 변화와 같은 비율, 최근 20초 안에 실제 문제가 없던 개입 비율 |

실시간 설정(평가기 · 판단 · 되돌아보기)을 바꾼 효과를 보려면 시나리오의 `config` 나 `--config` 로 바꾼 뒤 다시 재생하세요.

### 6-5. 재생 시나리오

시나리오 하나 = 가상 발표 하나 + 기대 결과입니다. 모양은 `coach_lab/simulator.py` 의 `Scenario` 가 원본입니다.
새 상황을 확인하고 싶으면 JSON 을 하나 더 만들면 됩니다. `tests/lab/test_replay.py` 가 `scenarios/*.json` 을 전부 돌립니다.

| 필드 | 뜻 |
|---|---|
| `plan`, `missions`, `memory`, `mode` | 코치 요청에 그대로 들어간다 |
| `baseline` | 발표자의 기본 상태: `cpm`, `script_ratio`(보인 시간 중), `uncertain_ratio`, `relative_db`, `filler_per_min`, `speaking`, `stt_status`, `audio_live`, `completion` |
| `segments[]` | `from_ms` ~ `to_ms` 동안 상태를 바꾼다 |
| `reactions{instruction: …}` | 코치가 그 말을 하면 `delay_ms` 뒤 `duration_ms` 동안 상태를 바꾼다(`set`), 다음 장으로 넘어간다(`advance_slide`), 말을 한다(`say`) |
| `config` | 코치 설정 덮어쓰기 (예: 쿨다운을 줄여 3분 안에 사다리를 다 보기) |
| `noise` · `seed` | 재생할 때의 잡음(기본 없음)과 seed. 실험은 이 값을 덮어써 잡음 3단계로 돌린다 |
| `name` · `description` · `take_id` · `duration_ms` · `tick_ms` · `end_on_finish` | 이름 · 설명, 요청의 Take ID, 재생 길이(필수) · 틱 간격(기본 1초), 마지막 장을 다 말하면 끝낼지 |
| `utterances[]` | `at_ms` 에 `text` 를 말한다 (키워드 확인용) |
| `expect` | 실시간: `interventions{min,max}`, `types{TYPE:{min,max}}`, `instructions_include` · `_exclude`, `strategy_changes_include`, `outcomes_include`, `suppressed_reasons_include` · 리뷰 근거: `segment_hints_include`, `type_status{TYPE:상태}`, `mission_status{id:상태}`, `memory_labels[]`, `top_issue{type,slide_number}`, `next_mission_types_include`, `no_claims[]`(문제로 말하면 안 되는 영역), `strengths_include[]` |

가상 발표자는 3글자 단어를 `cpm` 속도로 말하고, 6단어마다 0.6초 쉬고(문장 끝 신호), 대본 글자 수 × `completion` 만큼 말하면 다음 장으로 넘어갑니다.
확정 결과는 말한 뒤 1.2초에 옵니다. 오디오가 멈추면(`audio_live: false`) 계속 말하지만 STT 에 전달되지 않고 FE 침묵은 계속 늘어납니다.
시선은 FE 처럼 최근 10초의 1초 라벨 비율이고, 침묵은 소리 기준(단어를 말하는 도중이면 0)입니다.
`--raw` 면 원자료를 보냅니다: 시선은 그 1초 라벨을 기록으로, 음량은 평소 목소리 −24 dBFS 를 더한 레벨(기준은 보내지 않음), 단어는 군더더기 표시 없이.

| 시나리오 | 확인하는 것 |
|---|---|
| `01_baseline_good` | 문제없는 발표자에게 한마디도 하지 않는다 |
| `02_gaze_effective` | 시선 지적 → 고개를 듦 → 효과 있음 → 유지 격려 |
| `03_gaze_gave_up` | 안 바뀜 → 다른 방법 → 그 장에서 포기 → 리뷰에 `GAVE_UP` |
| `04_gaze_sensor_unusable` | 얼굴이 자주 안 잡힐 때 시선 지적 없음 |
| `05_time_behind` | 늦어지면 `SPEED_UP`, '천천히'는 나오지 않음 |
| `06_pace_fast` | 빨라지면 `SLOW_DOWN` → 효과 있음 |
| `07_exam_mode` | 실전 모드: 말하지 않고 문제 구간만 리뷰로 |
| `08_filler_frequent` | 군더더기 지적 → 줄어듦 |
| `09_long_silence` | 5초 넘게 멈추면 `RESUME` |
| `10_audio_dead` | 오디오가 멈춰도 침묵 · 늦음 오탐 없음 |
| `11_keyword_missing` | (켜면) 필수 키워드 안내 → 말함 |
| `12_no_slide_plan` | 장별 계획이 없으면 남은 시간만으로 마지막 1분 · 시간 초과 |
| `13_recurring_resolved` | 이전 Take 의 시선 문제가 사라짐 → 기억 `RESOLVED`, 미션 `ACHIEVED`, 시선 `IMPROVED`, 강점 |
| `14_recurring_persists` | 이전 Take 의 시선 문제가 그대로 → `RECURRING`, 미션 `FAILED`, 1순위 · 다음 미션이 그 장 시선 |
| `15_mixed_burdens` | 문제가 여럿일 때 1순위는 가장 크고 오래간 것(속도) |
| `16_noisy_sensors` | 센서가 나쁜 동안의 시선 · 속도는 문제로 말하지 않고, 멀쩡한 오디오의 작은 목소리만 |

### 6-6. 설정 바꾸기

기본값은 `config.py` 에만 있습니다. 코드를 고치지 말고 **바꾸고 싶은 값만** JSON 으로 덮어쓰세요.

```json
{"policy": {"cooldown_ms": 45000}, "gaze": {"script_ratio": 0.75}, "features": {"keyword_missing": true}}
```

```bash
uv run python -m coach_lab.replay --config my_override.json
```

`--config` 는 시나리오에 들어 있는 `config` 대신 쓰입니다. 응답의 `config_hash` 가 바뀌므로 어떤 설정으로 판단했는지 추적됩니다.
코어에서는 `load_config(policy={"cooldown_ms": 45_000})` 처럼 키워드로 넘깁니다 (코어는 파일을 읽지 않습니다).

### 6-7. 파이썬에서 직접

```python
from coach import build_review_evidence, decide, finalize, plan_coaching

# Take 시작 전 (선택). llm 없이 부르면 기본 계획 — 첫 coach_state 를 None 으로 시작하는 것과 같다
plan = plan_coaching({"take_id": "t1", "plan": {...}, "scripts": [...]}, llm=llm, model=model)
state, events = plan.coach_state, []
for t in range(0, 180_001, 1000):
    resp = decide(
        {"take_id": "t1", "t_ms": t, "plan": {...}, "current": {...}, "coach_state": state}
    )
    state = resp.coach_state  # BE 가 할 일: 그대로 보관
    events += resp.events  # BE 가 할 일: 그대로 쌓기
    if resp.feedback:
        print(resp.feedback.message)  # BE 가 할 일: FE 로 전달
events += finalize({"take_id": "t1", "t_ms": 180_000, "coach_state": state}).events
evidence = build_review_evidence("t1", events)  # → 리뷰 에이전트
```

`llm` 은 `.invoke(messages)` 가 `PlanDraft` 를 돌려주는 객체입니다. research 에서는
`coach_lab.llm.plan_llm(load_settings())` 로 만들고, 캐시는 `coach_lab.cache.SqlitePlanCache()` 를 `cache=` 로 넘깁니다.

### 6-8. 코칭 계획 실험

```bash
uv run python -m coach_lab.plan_eval              # scenarios/plan/*.json, 시나리오마다 5번 (실제 LLM, 캐시에 없는 것만 부른다)
uv run python -m coach_lab.plan_eval --samples 1  # 한 번씩만
uv run python -m coach_lab.plan_eval --out reports/results/coaching_plan.json   # 커밋하는 결과
```

응답은 `outputs/llm_cache.sqlite` 에 저장돼 같은 지시문 · 모델 · 입력이면 다시 부르지 않습니다(지연은 실제로 부른 호출만 잽니다).
지시문이나 출력 스키마를 바꾸면 계획 해시가 바뀌어 새로 부릅니다.

| 지표 | 뜻 |
|---|---|
| 유효 | LLM 이 답했고 기본 계획으로 돌아가지 않았다 |
| 기대 통과 | 검증을 거친 계획이 시나리오의 `plan_expect` 를 만족한다 |
| 검증에서 뺌 | LLM 초안에 범위 밖 · 근거 없는 항목이 있어 코치가 뺀 계획의 비율 |
| 일관성 | 같은 입력을 여러 번 물었을 때 집중 · 봐주기 (영역, 장) 묶음이 첫 답과 같은 비율 |
| 재생 효과 | 첫 답의 계획으로 재생한 영역 · 장별 개입 수를 계획 없이 재생한 것과 비교 |
| 지연 | 실제로 부른 호출의 시간 |

계획 시나리오는 재생 시나리오에 세 필드를 더한 것입니다: `scripts`(장별 대본), `previous_review`(직전 리뷰 근거),
`plan_expect`(`relax_includes` · `relax_excludes_types` · `relax_empty` · `focus_includes_types` · `no_budget` · `budget_set` · `with_plan_no_interventions`).
계획 없이 재생한 행동은 일반 시나리오처럼 `expect` 로 고정합니다 (`tests/lab/test_plan_eval.py` 가 검사).

| 시나리오 | 확인하는 것 |
|---|---|
| `17_plan_numbers_slide` | 수치 표를 읽는 3번 장의 시선을 봐준다 → 계획으로 재생하면 그 장의 시선 지적이 없다 |
| `18_plan_mission_focus` | 이번 미션(3번 장 시간)을 집중에 넣고 봐주지 않는다. 코칭이 잦았다는 근거가 없으니 개입 상한을 두지 않는다 |
| `19_plan_nothing_special` | 근거가 없으면 봐주기도 개입 상한도 두지 않는다 |
| `20_plan_overcoached` | 직전 Take 에서 14번 말했는데 효과는 2번뿐 → 개입 상한을 둔다 |
| `21_plan_quoted_notice` | 숫자 없이 고지 원문을 그대로 읽는 3번 장의 시선을 봐준다 (지시문을 고친 뒤 처음 잰 시나리오) |

---

## 7. 코드 읽는 순서와 자주 묻는 것

### 처음 읽는다면 이 순서로

1. `src/coach/schemas.py`: 요청 · 응답 · 이벤트의 모양. 모든 것의 중심입니다
2. `src/coach/engine.py`: `decide` 한 함수에 1초 판단 흐름이 다 보입니다
3. `src/coach/config.py`: 기준값과 전략 사다리(`_default_issue_rules`) · 문구 템플릿
4. `src/coach/evaluators/gaze.py`: 평가기 하나가 어떻게 문제를 만드는지 (가장 짧음)
5. `src/coach/policy.py` · `reflection.py`: 말할지 정하기, 효과를 보고 방법 바꾸기
6. `src/coach/review.py`: `build_review_evidence` → `assess`
7. `src/coach_lab/simulator.py` · `scenarios/02_gaze_effective.json`: 가상 발표가 어떻게 요청이 되는지
8. `src/coach/planner.py` · `prompts/plan.py`: Take 시작 전 계획 (LLM 초안 → 검증)

### 자주 묻는 것

**왜 LLM 이 아니라 규칙인가요?**
매초 LLM 을 부르면 지연(수백 ms ~ 수 초) · 비용(10분에 600회) · 흔들림 문제가 있습니다. 규칙 코치가 에이전트답지 않은 이유는
규칙이어서가 아니라 결과를 보고 행동을 바꾸지 않아서라, 개입 효과를 재고 방법을 바꾸는 되돌아보기로 그 부분을 채웠습니다. LLM 은 Take 시작 전 코칭 계획(v1.2)에 한 번 쓰고, 애매할 때 고르는 선택기는 v2 입니다 ([13](#13-다음-버전)).

**`coach_state` 는 왜 BE 가 들고 있나요?**
AI 서버가 저장하지 않아야 재시작 · 재배포 · worker 증설에도 발표 중 기억이 사라지지 않고, 같은 요청에 같은 응답이 나옵니다.
BE 는 내용을 몰라도 되고, 받은 그대로 다음 요청에 붙이면 됩니다.

**재생 시나리오가 통과하면 기준값이 맞는 건가요?**
아닙니다. "로직이 의도대로 돈다"는 뜻입니다. 기준값이 실제 발표자에게 맞는지는 실제 연습 데이터가 있어야 압니다.

**무엇을 고칠 때 어디를 보나요?**

| 하려는 일 | 위치 | 주의 |
|---|---|---|
| 기준값 · 가중치 | `config.py` | 바꾸기 전 재생 · 실험 결과를 기록해 두고 비교. `config_hash` 가 바뀐다 |
| 화면 문구 | `config.py` 의 `_TEMPLATES` | 모든 사다리 칸에 템플릿이 있어야 한다 (`test_every_ladder_step_has_a_template`) |
| 새 문제 · 행동 | `vocab.py` + `evaluators/` + `config.py` 의 사다리 · 템플릿 | 출력 의미가 바뀌므로 `POLICY_VERSION` 을 올린다 |
| 말할지 정하는 규칙 | `eligibility.py` · `priority.py` · `policy.py` | 재생 · 실험으로 확인 |
| 리뷰 근거 규칙 | `review.py` · `config.review` | 실험 결과 JSON 을 다시 만들어 `git diff` 로 비교 |
| 요청 · 응답 · 이벤트 필드 | `schemas.py` | INTERFACE.md 도 같이 고친다. 모양이 바뀌면 `SCHEMA_VERSION` |
| `coach_state` 모양 | `state.py` | 기본값 없는 필드를 더하거나 바꾸면 `STATE_VERSION` |
| 새 상황 확인 | `scenarios/*.json` | 기대 결과(`expect`)까지 적는다 |

### 반드시 알아야 할 함정

- **`speech` 가 없으면 STT 가 없는 것입니다.** 요청에 `current.speech` 가 없으면 말 속도 판단을 끄고 장 진행도를 시간으로 잽니다.
  테스트에서 진행도를 글자 수로 재게 하려면 `speech={"words": []}` 라도 넣어야 합니다. 오디오가 멈춘 동안(`audio_live: false`)도 마찬가지이고, 그 장은 끝까지 시간으로 잽니다.
- **`coach_state` 는 그대로 왕복시키세요.** null 필드는 보내지 않지만(`exclude_none`) 기본값 필드는 보냅니다. 기본값인 `v` 까지 빠지면 버전 확인이 깨집니다.
  다른 버전이나 깨진 state 가 오면 새로 시작하고 `reason_codes` 에 `STATE_RESET` 을 남깁니다. 이때 열린 문제 구간 · 재지 못한 효과가 사라지고 이벤트 번호도 처음부터 다시 매겨집니다.
- **instruction 이나 사다리 칸을 더하면 문구도 더하세요.** 템플릿의 `{이름}` 은 평가기가 `params` 로 넘겨야 하고, `*_time_ms` 는 "1분 5초" 로 바뀌어 `{*_time}` 에 들어갑니다.
- **시간 규칙이 다른 테스트를 방해할 수 있습니다.** `make_request` 는 장 체류 시간을 안 주면 Take 시작부터 그 장이었던 것으로 봅니다.
  시선만 보려는 긴 테스트는 `plan={}` 로 시간 계획을 빼야 '시간 여유' · '다음 장으로' 같은 시간 후보가 끼지 않습니다.
- **리뷰 설정과 실시간 설정은 실험 방식이 다릅니다.** `config.review` 는 Take 종료 뒤에만 쓰여 같은 재생 결과로 변형을 비교합니다.
  그 밖의 설정을 바꾸면 발표가 달라지므로 다시 재생해야 합니다. 실험 하한선(`tests/lab/test_experiment.py`)은 noisy seed 2개로 돕니다.
- **ruff 는 한글을 2칸으로 셉니다.** 한글 주석이 100자 안이어도 E501 이 날 수 있습니다.

---

## 8. 입출력

코어가 무엇을 받아 무엇을 돌려주는지는 **[INTERFACE.md](INTERFACE.md)**에 정리했습니다. API 요청 · 응답을 정할 때 기준이 되는 문서입니다.
필드의 원본은 `schemas.py` 의 pydantic 모델이고, 문서와 코드가 다르면 코드가 맞습니다.

| 내용 | INTERFACE.md |
|---|---|
| 진입점 5개의 시그니처 · 실패 처리, 붙일 API 경로(`/coach/evaluate` · `/coach/finalize`) | 1절 |
| 요청 `CoachRequest`: 입력이 어디서 오는지, 필드 표, 실제 예시 | 2절 |
| 응답 `CoachResponse`: 필드 표, action · 후보 상태, `type` 과 `instruction` 을 나눈 이유, 실제 예시 | 3절 |
| 이벤트 6종의 필드 · 예시, `event_id` 와 중복 방지 | 4절 |
| 리뷰 근거 `CoachReviewEvidence`: 필드 표, `hint`, 리뷰 에이전트 프롬프트 규칙, 실제 예시 | 5절 |
| 공통 지표 이름, 미션 판정이 지원하는 지표 | 6절 |
| `coach_state`: 내용 · 크기 · 버전 · 왜 BE 가 들고 있나 | 7절 |
| enum 값, 버전과 호환 규칙 | 8 · 9절 |

BE · 리뷰와 맞닿는 곳은 넷입니다: 1초 요청 · 응답, Take 종료(`finalize`), 쌓아 둘 이벤트, 리뷰 근거.

---

## 9. 버전별 결과표

> **모두 가상 발표자 기준입니다.** 리뷰 근거가 '정답에 가깝게' 만들어지는 것은 확인했지만, 정답은 시뮬레이터가 아는 발표자 상태이고
> 잡음 모델은 실제 FE · Deepgram 의 오차와 다를 수 있습니다. **기준값(70% · 350 CPM · −6dB 등)과 코칭의 실제 효과는 검증하지 않았습니다.**

### v1.2 (판단 규칙 `coach-v1.2`, `config_hash` `d96ea5e7cecf`)

Take 시작 전 LLM 코칭 계획을 더했습니다. 계획이 없으면 판단은 v1.1 과 같습니다.
커밋된 결과는 `reports/results/coaching_plan.json`(모델 `openai/gpt-5.6-luna`, 계획 해시 `84aecb2e2a2a`)입니다.

| 비교 | 결과 |
|---|---|
| 계획 없이 재생 16개 (요약 · 원자료 입력) | 결과 JSON 이 v1.1 과 같다 (`config_hash` · 버전만 다름). 리뷰 근거 실험 지표도 같다 |
| 코칭 계획 (시나리오 5개 × 5번) | 유효 25/25, 기대 통과 24/25 (0.96), 검증에서 뺌 0, 일관성 0.90, 지연 중앙값 2.6초 · 평균 3.1초 · 최대 8.1초 |
| 계획으로 재생 | 17 · 21: 3번 장 시선 지적 1번 → 0번 (`PLAN_RELAXED` 로 참은 기록 5번). 18: 집중한 3번 장 시간 문제가 나오기 전에 2번 장에서 이미 말해 개입이 같다. 20: 개입 상한 6 ~ 8 (직전 14번) |
| 틀린 것 | 21 의 5번째 답이 3번 장 봐주기를 빠뜨림. 빠뜨리면 계획 없는 판단과 같다 (그 장 시선을 지적) |

지시문 · 입력 · 개입 상한 규칙을 고치기 전과 비교 (시나리오 17 ~ 20 × 5번, 같은 모델):

| | 기대 통과 | 검증에서 뺌 | 일관성 | 틀린 것 |
|---|---|---|---|---|
| 고치기 전 | 14/20 (0.70) | 0.25 | 0.94 | 18: 근거 없는 개입 상한 2 ~ 3 (하한 5 로 올려 그대로 씀) 4번, 17: 수치 장 봐주기 빠뜨림 1번, 20: 개입 상한 없음 1번 |
| 고친 뒤 | 20/20 (1.00) | 0 | 0.94 | — |

고친 것: 개입 상한은 직전 Take 보다 적게 말하게 할 때만 둔다(코드), 장별 숫자 개수를 입력에 넣는다, 지시문에 입력 설명과 개입 상한의 근거를 적는다.
같은 시나리오로 고치고 쟀으므로, 고친 뒤 처음 잰 `21_plan_quoted_notice`(숫자 없는 원문 고지 장)를 따로 봤습니다: 5번 중 4번 봐줌.

### v1.1 (판단 규칙 `coach-v1.1`, `config_hash` `dbe162df2a38`)

판단 규칙과 기준값은 v1 과 같고, 입력 처리만 바뀌었습니다: 원자료 입력(시선 1초 기록 · 음량 레벨과 기준 · 표시 없는 단어의 군더더기),
측정하지 못한 1초를 센서 판단에 넣기, Take 시작 뒤 5초 시선 지적 안 함.
커밋된 결과는 `reports/results/review_evidence.json`(FE · BE 요약 입력)과 `review_evidence_raw.json`(원자료 입력)입니다.

| 비교 | 결과 |
|---|---|
| v1 과 같은 입력(요약)으로 재생 16개 | 결과 JSON 이 v1 과 같다 (`config_hash` · 버전과, 상태 칸이 늘어난 `coach_state` 크기만 다름) |
| v1 과 같은 입력으로 리뷰 근거 실험 | 아래 v1 표의 지표 15개가 그대로다. 장별 대본 응시 오차만 harsh 0.1068 → 0.1066 (측정하지 못한 1초를 센서 판단에 넣은 영향) |
| 원자료 입력으로 재생 16개 | 16/16 통과, 개입 시점 · 내용이 요약 입력과 같다 (`tests/lab/test_replay.py` 가 검사). 첫 장의 데이터 덮개 · 대본 응시 비율 · 음량 표본만 Take 시작 차이(시선 첫 몇 초, 음량 기준을 잡는 첫 발화 15초)로 다르다. 군더더기 수는 BE 표시가 있을 때와 같다 |
| 원자료 입력으로 리뷰 근거 실험 (D) | 지표 15개 중 13개가 요약 입력과 같다. 다른 것: 구간 IoU 0.870 / 0.832 / 0.803 → 0.870 / 0.831 / 0.802, 시작 오차 1.580 / 2.530 / 2.860 → 1.580 / 2.540 / 2.880초 (잡음 섞인 첫 발화로 잡은 음량 기준이 실제와 조금 다름). 장별 대본 응시 오차 0.034 / 0.061 / 0.107 → 0.033 / 0.065 / 0.103 |

### v1 (판단 규칙 `coach-v1`, `config_hash` `0f4bc4102193`)

| 항목 | 값 |
|---|---|
| 재생 시나리오 | 16/16 통과: 01 ~ 12 실시간 행동, 13 ~ 16 리뷰 근거 (이전 Take 기억 · 미션 · 순위 · 센서 불량) |
| 판단 1회 | 시나리오별 p50 0.4ms · p95 1ms 이하 (재생 16개, 개발 PC · Python 3.12. PC 부하에 따라 달라진다) |
| `coach_state` | 재생 시나리오 16개에서 응답마다 잰 최대 약 15KB. 1초마다의 기록은 60초만 남기고, 10분짜리 재생의 끝 상태가 20KB 미만인 것을 테스트로 확인 |

#### 리뷰 근거 실험 방법

- **가상 발표**: 시나리오 16개를 잡음 3단계로 재생합니다 (clean 1회, noisy · harsh 각 seed 5 → 176 Take)
- **정답**: 시뮬레이터는 발표자가 **실제로** 어땠는지(잡음 전 상태)를 매초 기록합니다. 정답 리뷰 = 그 실제 상태 × 코치와 **같은 판정 규칙**.
  그래서 코치 리뷰 근거가 정답과 다르면 그 차이는 측정(잡음 · 창 지연 · 구간 처리)에서 온 것입니다
- **원칙**: 센서가 볼 수 없던 문제는 정답도 말하지 않습니다. 이상적인 리뷰는 '그랬을 것 같다'가 아니라 '믿을 수 있는 데이터로 봤다'만 말해야 합니다
- **잡음** (`simulator.py` 의 `NOISE_PRESETS`): 시선은 FE 처럼 10초 창의 1초 라벨 비율로 만듭니다

| 잡음 | 시선 라벨 뒤집힘 | UNCERTAIN 끼어듦 | 말 속도 흔들림 | 음량 σ | STT 단어 누락 | 확정 지연 흔들림 |
|---|---|---|---|---|---|---|
| clean | 0 | 0 | 0 | 0 | 0 | 0 |
| noisy | 8%/초 | 4%/초 | ±12% | 1.5 dB | 3% | 0~0.8초 |
| harsh | 15%/초 | 10%/초 | ±20% | 3 dB | 8% | 0~1.5초 |

- **변형**: 리뷰 근거 규칙만 바꿉니다

| 변형 | 신뢰도 필터 | 구간 병합 | 창 지연 보정 |
|---|---|---|---|
| A 기준선 (첫 구현의 리뷰 근거 규칙) | ✗ | ✗ | ✗ |
| B | ✓ | ✗ | ✗ |
| C | ✓ | 10초 | ✗ |
| D (지금 기본값) | ✓ | 10초 | ×1.0 |

#### 결과 (seed 5)

숫자는 `reports/results/review_evidence.json` 입니다. ↑ 클수록 좋음, ↓ 작을수록 좋음. 칸은 clean / noisy / harsh.

| 지표 | A 기준선 | D 지금 |
|---|---|---|
| 구간 재현율 ↑ | 1.000 / 0.934 / 0.952 | 1.000 / 0.934 / 0.952 |
| 구간 정밀도 ↑ | 0.667 / 0.623 / 0.587 | **1.000 / 0.984 / 0.873** |
| 구간 F1 ↑ | 0.800 / 0.747 / 0.726 | **1.000 / 0.959 / 0.911** |
| 근거 없는 지적 / Take ↓ | 0.375 / 0.500 / 0.625 | **0.000 / 0.013 / 0.113** |
| 구간 IoU ↑ | 0.590 / 0.575 / 0.503 | **0.870 / 0.832 / 0.803** |
| 시작 오차 (초) ↓ | 7.250 / 8.510 / 9.470 | **1.580 / 2.530 / 2.860** |
| 조각남 ↓ | 1.000 / 1.158 / 1.203 | 1.083 / 1.158 / 1.136 |
| 미션 판정 정확도 ↑ | 1.000 / 1.000 / 0.867 | 1.000 / 1.000 / 0.867 |
| 영역 상태 정확도 ↑ | 0.938 / 0.938 / 0.936 | **1.000 / 0.989 / 0.977** |
| 이전 Take 비교 정확도 ↑ | 1.000 / 1.000 / 1.000 | 1.000 / 1.000 / 1.000 |
| 1순위 영역 일치 ↑ | 0.750 / 0.713 / 0.700 | **1.000 / 0.912 / 0.912** |
| 1순위 다음 미션 일치 ↑ | 0.750 / 0.713 / 0.700 | **1.000 / 0.912 / 0.912** |
| 거짓 강점 / Take ↓ | 0.125 / 0.125 / 0.125 | **0.000 / 0.062 / 0.050** |
| 효과 판정 정확도 ↑ (실시간, 변형과 무관) | 1.000 / 0.980 / 0.936 | 같음 |
| 불필요한 개입률 ↓ (실시간, 변형과 무관) | 0.000 / 0.021 / 0.042 | 같음 |

장별 표의 평균 절대 오차(D): 대본 응시 0.034 / 0.061 / 0.107, CPM 1.618 / 3.686 / 6.408, 장 시간 0.431 / 0.523 / 0.533초.

변형별 기여 (noisy): 신뢰도 필터(B)가 정밀도를 0.623 → 0.930, 근거 없는 지적을 0.500 → 0.062 로 줄였고,
구간 병합(C)이 조각남을 1.158 → 1.000 으로, 창 지연 보정(D)이 IoU 를 0.594 → 0.832, 시작 오차를 8.51 → 2.53초로 줄였습니다.

#### 격자로 고른 값 (`--sweep`)

| 값 | 고른 것 | 근거 |
|---|---|---|
| 창 지연 보정 정도 | ×1.0 | IoU 0.832 (noisy, 병합 10초). ×0.5 는 0.712, ×1.5 는 0.756. 정밀도는 보정이 있으면 거의 같다 |
| 병합 간격 | 10초 | 5초 이상이면 F1 · IoU 차이가 거의 없다 (지연 보정 ×1.0 에서 F1 0.959, IoU 0.830 ~ 0.832). 조각남은 1.246(0초) → 1.158(10초) |
| 리뷰에 넘길 최소 구간 (보정 전 길이) | 3초 | 3초 이하면 clean 재현율 1.000, 4초 이상이면 clean 의 짧은 실제 문제 하나를 놓친다(0.917). 6초면 harsh 의 근거 없는 지적이 0.113 → 0.062 로 줄어든다. **실제 데이터로 다시 고를 값** |

clean 값은 `--sweep` 격자에 없어서(격자는 noisy · harsh) 같은 격자를 clean 으로 따로 돌려 확인했습니다.

#### 실험으로 찾아 고친 측정 문제 (`--explain D`)

틀린 사례를 하나씩 따라가 원인을 찾았습니다. 회귀 테스트는 측정 쪽이 `tests/unit/test_measurement.py`, 리뷰 근거 쪽(장 정보가 없는 발표, 영역 순위 등)이 `tests/unit/test_review_evidence.py` 에 있습니다.

| 문제 | 사례 | 고친 것 |
|---|---|---|
| 센서가 나쁜 시간도 지속시간에 들어가, 잠깐 괜찮아진 1초에 시선 지적 | 04 | 지속시간은 믿을 수 있는 시간만 센다 |
| 10초 창의 UNCERTAIN 이 잡음으로 잠깐 50% 아래로 내려감 | 04 · 16 | 지금 값과 최근 10초 평균 중 나쁜 쪽 |
| STT 가 불량이던 동안의 단어로 '빠르다' | 16 | 불량에서 돌아오면 돌아온 뒤의 단어로만 |
| 장 정보가 없는 발표에서 null 장 누적이 매 틱 state 를 초기화 (개입 140회) | 12 | null 이 될 수 있는 필드는 모두 기본값 |
| 확정이 늦게 온 단어를 다음 장에 붙여 키워드를 못 찾음 | 11 | 최근 장 전환 기록으로 말한 시각의 장에 붙인다 |
| 시선 효과를 평균으로의 회귀로 '줄었다'고 오판 | 03 · 14 | 60% 아래로 내려온 것만 인정, 12초 뒤, 3초 평균 |
| 15초 CPM 창에 개입 전 단어가 남아 속도 효과를 못 봄 | 06 | 효과는 6초 CPM |
| 단어 사이 틈을 침묵으로 보고 '다시 말함'을 못 봄 | 09 | 침묵이 개입 뒤에 다시 시작됐는지 |
| 라벨 흔들림으로 5초 연속 응시가 우연히 생김 | harsh 06 · 08 · 09 | 연속 응시는 창 비율 ≥ 0.6 일 때만 |
| 음량 표본 1~2개의 평균 | harsh | 표본 3개 이상 |
| 부담을 최고 심각도로 재 잡음 많은 지표가 부풀어 순위가 흔들림 | 07 | 평균 심각도 |
| 시선은 장마다 나뉘어 영역 1순위가 흔들림 | 07 | 순위는 영역 합계가 먼저 |

#### 남은 오류

- **1순위가 근소한 차이로 갈리는 Take** (07): 실제로도 시선과 속도 중 어느 쪽이 먼저인지 애매합니다
- **빠뜨린 키워드(10초로 셈)가 짧은 시선 문제보다 앞서는 경우** (02): 키워드 하나의 무게는 근거 없이 정한 값입니다
- **10초 미만의 짧은 대본 응시를 놓침** (15): FE 10초 창의 한계입니다
- **센서 편향** (harsh 13): 시선 라벨이 15% 뒤집히면 실제 0.20 이 0.31 로 측정돼, 목표 0.3 미션이 `ACHIEVED` 대신 `PARTIAL` 이 됩니다
- **harsh 의 근거 없는 지적 0.113 / Take**: 대부분 시선 잡음입니다

#### 옮긴 뒤 검증

archive 코드를 Python 3.12 에서 그대로 돌린 결과를 기준으로 비교했습니다.

| 항목 | 결과 |
|---|---|
| 판단 코어 · 시나리오 | 가져올 때 archive 와 git 트리 해시가 같은 복사본. 그 뒤로 바꾼 것은 설정 파일 읽기 위치 · 버전 값 위치 · 주석뿐 |
| pytest | archive 테스트 120개가 새 위치와 1:1 로 대응해 모두 통과 (+ 경로 · 설정 파일 · 코어 경계 테스트) |
| 재생 16개 | 시나리오별 결과 JSON(이벤트 전체 · 리뷰 근거 · 타임라인)이 archive 와 같음. 실행마다 달라지는 판단 지연 값만 빼고 비교 |
| 리뷰 근거 실험 | `--sweep` 결과 JSON 이 archive 의 결과와 바이트 단위로 같음. 그 뒤 `policy_version` 한 줄만 더했다 |

코어를 고친 뒤에도 같은 방식(재생 결과 비교 + 실험 결과 `git diff`)으로 판단이 의도 밖으로 바뀌지 않았는지 확인할 수 있습니다.

#### v1 을 "완료"라고 부르려면

1. 실제 연습 Take 를 시나리오 형식으로 기록하고, 사람이 문제 구간 · 미션 판정 · 1순위를 라벨링해 실험의 정답 자리에 넣는다
2. 같은 지표와 실시간 지표(불필요한 개입률, 피드백 뒤 행동 변화율, 판단 지연)를 실제 데이터로 잰다
3. 결과로 기준값과 리뷰 근거 규칙 값(최소 구간 길이 등)을 다시 고른다 (`config_hash` 로 추적)
4. FE · BE 와 입력 형식을 확정하고 AI 서버로 옮긴다 ([DEPLOY.md](DEPLOY.md))

### 새 버전을 올릴 때

판단 규칙의 의미가 바뀌어 `POLICY_VERSION` 을 올렸다면 위 표 아래에 그 버전의 표를 추가하고, 실험 결과 JSON 을 다시 만들어 커밋합니다.
실제 연습 데이터로 측정하면 가상 발표 결과와 분리해서 적습니다.

---

## 10. 배포

AI 서버에 올리는 데 필요한 작업(서버 · BE · FE)과 순서, API, 비용, 결정해야 할 것은 [DEPLOY.md](DEPLOY.md)에 있습니다.

---

## 11. 설계 결정

| 결정 | 이유 |
|---|---|
| **AI 는 아무것도 저장하지 않고 `coach_state` 를 왕복시킨다** | 재시작 · 재배포 · worker 증설에도 판단이 같다. 같은 요청 → 같은 응답이라 재생 테스트가 배포 결과와 같다. BE 에는 이미 Take 마다 연결보다 오래 사는 객체(`TakeStream`)가 있다 |
| **BE 가 1초마다 부른다 (AI 가 가져가지 않는다)** | AI 는 BE 주소를 모르는 구조다 (대본 파싱과 같다) |
| **판단은 규칙, 에이전트성은 되돌아보기로** | 매초 LLM 은 지연 · 비용 · 흔들림 문제가 있다. 결과를 보고 행동을 바꾸는 부분을 되돌아보기로 채웠다 |
| **지시는 한 번에 하나** | 말하면서 읽는 건 같은 언어 채널이라 두 개면 둘 다 못 읽거나 말을 멈춘다. 지시끼리 충돌한다(천천히 + 핵심만 빨리). 여러 개를 동시에 말하면 무엇이 효과였는지 알 수 없어 되돌아보기가 망가진다. 대신 상태 표시(`indicators`)는 여러 개를 함께 준다 |
| **문구는 템플릿** | 발표 중 화면 문장이 매번 달라지거나 엉뚱한 말이 뜨면 안 된다 |
| **type(원인)과 instruction(행동)을 나눈다** | 원인과 행동이 1:1 이 아니다. type 은 리뷰 · 미션과 같은 enum 이라 미션 가중치와 리뷰 연결이 그대로 된다 |
| **문장이 끝날 때 말한다 (최대 3초)** | 말하는 도중 끼어들지 않아야 사람 코치처럼 느껴진다. 3초 상한은 끝없이 미루지 않기 위해서 |
| **효과를 못 재면 전략을 바꾸지 않는다** | 센서가 나빠 '효과 없음'으로 오판하면 멀쩡한 방법을 포기한다 |
| **`script_ratio` 는 보인 시간 기준** | 비율이 UNCERTAIN 까지 합쳐 1 이라, 전체 기준이면 '대본 70%'와 'UNCERTAIN 50% 초과'가 동시에 성립할 수 없어 센서 필터가 무의미해진다 |
| **'빠름'은 예상 종료, '늦음'은 r** | r 은 끝으로 갈수록 몇 초 차이에도 크게 흔들린다 ([5-2](#5-2-평가기--계산-세부)) |
| **FINAL_MINUTE 는 계획이 없거나 늦을 때만** | 계획상 마지막 1분에 3번 장이 정상일 수 있다. FE 코치의 '1분 남았어요'는 계획이 없을 때의 대체 판단으로 남긴다 |
| **오디오가 멈추면 STT 도 믿지 않는다** | 못 들은 말 때문에 진행도가 낮게 잡혀 '늦다'고 오판한다. 그 장은 시간으로 잰다 |
| **이벤트를 내고, 리뷰 근거는 Take 종료 때 묶는다** | 리뷰가 코치를 직접 부르지 않는다. 중간에 끊겨도 이미 쌓인 이벤트는 남는다 |
| **리뷰 근거의 숫자 · 판정은 코드가 한다** | LLM 이 수치를 만들면 근거 없는 수치가 생긴다. 리뷰는 인용 · 문장만 |
| **판정 층(`assess`)을 순수 함수로 나눈다** | 정답 데이터에 같은 판정을 돌려, 측정 오차와 규칙 오류를 나눠 평가할 수 있다 |
| **믿을 수 없던 구간은 문제로 말하지 않는다** | 근거 없는 지적이 noisy 0.500 → 0.062 / Take (변형 B) |
| **창 지연을 되돌린 구간을 따로 준다** | 시작 오차 8.51초 → 2.53초 (noisy). 실제로 잡힌 구간(start/end)도 함께 남겨 둘 다 볼 수 있다 |
| **부담은 평균 심각도, 순위는 영역 합계 먼저** | 최고값은 잡음에 부풀고, 시선은 장마다 나뉘어 순위가 흔들렸다 |
| **다음 미션 목표는 한 번에 도달할 만큼** | 0.9 → 0.3 처럼 큰 목표는 다음 Take 에서도 FAILED 가 반복된다 |
| **센서 판단은 지금 값과 최근 평균 중 나쁜 쪽** | 평균만 쓰면 나빠지기 시작할 때 몇 초를 더 믿고, 지금 값만 쓰면 잡음으로 잠깐 믿게 된다 |
| **효과는 '탐지 기준보다 낮게 내려옴' · 짧은 창 · 3초 평균으로** | 평균으로의 회귀와 창 지연이 효과 판정을 흔들었다 ([5-4](#5-4-되돌아보기)) |
| **코어(`coach`)와 재생 · 실험(`coach_lab`)을 패키지로 나눈다** | 코어만 서버로 폴더째 옮기고, 실험 도구(시뮬레이터 · 정답 · 채점)는 research 에 남긴다. 대본 전달도(`script_coverage` / `coverage_lab`)와 같은 방식이다 |
| **설정 파일은 코어 밖에서 읽는다** | 코어는 파일을 읽지 않는다. research 에서는 `coach_lab.replay --config`, 서버에서는 service 설정이 dict 로 넘긴다 |
| **시선은 1초 기록을 받아 코치가 요약한다** | 요약(최근 창 비율 · 지금 라벨)을 만드는 곳이 FE · BE 어디에도 없었다. 가공 규칙이 AI 소유이고, FE 는 이미 만드는 1초 판정을 그대로 보내면 된다. FE 요약 입력도 계속 받는다 (기존 계약 유지) |
| **기록이 없는 시간은 측정 못 함으로 센다** | 카메라 · 전송이 끊겼을 때 마지막 상태로 계속 지적하지 않는다. 시선 모듈의 `UNMEASURED` 와 같은 뜻이다 |
| **음량 기준이 없으면 첫 발화로 잡는다** | FE 에는 개인 캘리브레이션이 없고 절대 레벨(dBFS)은 마이크마다 다르다. '평소보다 작아짐'은 첫 발화 중앙값만으로도 잴 수 있다. 기준이 오면(`baseline_db`) 그것을 쓴다 |
| **표시가 없을 때 군더더기는 소리뿐인 말만 센다** | 음 · 어 같은 소리는 늘 군더더기지만 '그' · '이제'는 뜻이 있을 수 있어 문맥이 필요하다. 오탐보다 놓침이 낫다고 보고, 문맥 판단은 BE 표시에 맡긴다 |
| **Take 시작 뒤 5초는 시선을 지적하지 않는다** | 1초 기록 입력으로 재생해 보니 첫 1~3초의 표본으로 근거 없는 시선 구간이 생겼다 (harsh 근거 없는 지적 0.113 → 0.300). FE 코치도 표본 5개 미만이면 비율을 내지 않는다 |
| **LLM 은 Take 시작 전 한 번, 계획에만** | 매초 부르면 지연 · 비용 · 흔들림 문제가 있다. 계획은 이미 있던 자리(우선순위 가중치 · 참을 이유)로만 들어가 1초 판단 · 문장은 그대로 규칙이다 |
| **LLM 초안은 코드가 검증하고 자른다** | LLM 이 시간 · 음량처럼 늘 챙겨야 할 것을 봐주거나, 이번 미션을 봐주거나, 없는 장을 말하면 안 된다. 뺀 항목은 이유와 함께 응답에 남겨 실험에서 센다 |
| **개입 상한은 직전 Take 보다 적게 말하게 할 때만** | 실험에서 LLM 이 코칭이 잦았다는 근거 없이 상한 2 ~ 3 을 냈다 (시나리오 18, 5번 중 4번). 지시문만으로 막지 않고 규칙으로 막는다 |
| **장별 숫자 개수를 함께 준다** | 수치 표 장의 시선 봐주기를 5번 중 1번 빠뜨렸다. 센 값을 주면 대본을 읽다 놓치지 않는다. 숫자 없는 원문 고지 장(21)으로 숫자만 보고 봐주는 것은 아닌지 확인했다 |
| **계획을 못 세우면 기본 계획** | 계획이 없다고 코칭이 멈추면 안 된다. 실전 모드는 말하지 않으니 부르지 않는다 |
| **LLM · 캐시는 인자로 받는다** | 코어 규칙(파일 · 네트워크 없음)을 지키고, 가짜 LLM 으로 테스트한다. 대본 전달도 코어와 같은 방식이다 |
| **실험 결과 JSON 은 커밋하고 재생 결과는 커밋하지 않는다** | 실험은 같은 코드면 바이트 단위로 같아 `git diff` 로 비교할 수 있다. 재생 결과에는 실행마다 달라지는 판단 시간이 들어 있다 |

---

## 12. 알려진 한계

- **코칭 계획은 시나리오 5개로만 확인**: 17 ~ 20 으로 지시문을 고쳤고 21 만 고친 뒤 처음 쟀습니다. 계획이 실제 발표자의 개선을 돕는지는 재지 않았습니다
- **LLM 이 가끔 봐주기를 빠뜨림**: 25번 중 1번. 빠뜨리면 계획 없는 판단과 같아 지적이 늘 뿐, 늘 챙길 것을 놓치지는 않습니다
- **개입 상한은 재생에서 걸린 적이 없음**: 시나리오의 개입이 많아야 2번이라 하한 5 에 닿지 않습니다. 상한으로 참는 동작은 단위 테스트로만 확인했습니다
- **계획에 2.5 ~ 8초**: Take 시작 전 기다림입니다. LLM 은 재시도 없이 한 번 부르고(요청 제한 20초), 실패하면 기본 계획으로 시작합니다.
  요청 제한은 연결 · 읽기마다 걸리는 값이라 전체 시간의 상한은 부르는 쪽(BE) 타임아웃이 맡습니다
- **실제 데이터 없음**: 가상 발표자로만 확인했습니다. 실제 Deepgram 출력의 확정 지연, 실제 FE 시선 판정의 오차, 실제 발표자의 반응은 다를 수 있습니다
- **Take 시작 뒤 5초는 시선을 지적하지 않음**: 표본이 적은 비율로 근거 없는 지적이 나지 않게 한 대가입니다
- **음량 기준을 첫 발화로 잡으면 처음부터 작게 말한 발표자는 놓침**: 기준도 낮게 잡히기 때문입니다. 평소 목소리를 캘리브레이션해 `baseline_db` 로 보내면 해결됩니다. 기준을 잡는 첫 발화 15초 동안은 음량을 판단하지 않습니다
- **효과 측정이 짧은 창의 실시간 지표**: 5~30초 뒤 한 번 봅니다. 우연히 좋아졌거나 나빠진 것과 구분하지 못합니다. 리뷰가 확정 데이터로 다시 봐야 합니다
- **진행도는 글자 수 비례**: 대본에 없는 말(즉흥 설명)도 진행으로 셉니다. 대본을 건너뛰면 늦지 않았는데 늦은 것으로 봅니다
- **키워드는 문자열 일치**: STT 가 고유명사를 잘못 적으면(`노쇼` → `노조`) 말했는데 못 찾습니다. 그래서 기본으로 꺼 두었습니다
- **표시가 없으면 소리뿐인 군더더기만 셈**: '그' · '이제'처럼 문맥이 필요한 말은 BE 가 `filler` 로 표시해야 셉니다
- **시뮬레이터 기반 실험**: 잡음 모델은 실제 FE 시선 분류기 · Deepgram 의 오차 분포와 다를 수 있습니다. 리뷰 근거 규칙의 값은 실제 데이터로 다시 골라야 합니다
- **짧은 대본 응시(10초 미만)는 놓칠 수 있음**: FE 10초 창의 한계입니다
- **센서 편향은 되돌리지 않음**: 시선 분류기가 대칭으로 틀리면 비율이 0.5 쪽으로 끌려, 목표값 근처의 미션 판정이 흔들립니다
- **키워드 하나의 무게(10초)는 근거 없는 값**: 짧은 시선 문제와 순위가 바뀔 수 있습니다
- **우선순위가 100 에서 잘림**: 여러 가중치가 겹치면 100 으로 포화됩니다. 순서는 자르기 전 점수로 정하므로 선택에는 영향이 없지만, 응답의 숫자만 보면 구분되지 않습니다

---

## 13. 다음 버전

| 버전 | 무엇 | LLM |
|---|---|---|
| v1 | 규칙 실행 + 되돌아보기 + 문장 끝 기다리기 + 상태 표시 + 리뷰 근거 | 없음 |
| v1.1 | v1 + FE · BE 원자료 입력(시선 1초 기록 · 음량 레벨과 기준 · 군더더기 판단) | 없음 |
| **v1.2 (지금)** | **코칭 계획**: Take 시작 전 LLM 이 미션 · 이전 리뷰 · 장별 대본을 읽고 `CoachingPlan`(focus · relax · 개입 상한)을 만든다. 예: "3번 장은 수치가 많아 대본을 봐도 괜찮다". 코드가 검증하고 자름, 실패하면 기본 계획 | Take 당 1회 |
| v2 | **선택기**: 적격 후보가 2개 이상이고 점수 차가 작을 때만 LLM 이 고른다. 후보 밖은 못 고르고, 1초 안에 답이 없으면 `RulePolicy` 결과. 그림자 모드(기록만)로 시작해 재생 평가에서 나을 때 켠다 | 애매할 때만 |
| 이후 | 쌓인 `OUTCOME` 으로 사람마다 잘 통하는 방법을 고르는 학습형 정책 | — |

v2 를 위한 자리도 이미 있습니다. 선택기는 `policy.Policy` 인터페이스(`select(tick, candidates)`)로 `RulePolicy` 를 바꿔 끼웁니다.

---

## 14. 원본

- 구조를 바꾸기 전 코드: `ai/archive/workspaces/jewon-kim/coach-agent/v1/local/` (동결본, 수정 · import 금지).
  같은 폴더 위의 `v1/README.md` 가 옮기기 전의 기술 레퍼런스입니다
- 개편 직전 상태 그대로의 원본: git 태그 `ai-workspaces-final`
- 아카이브 규칙과 목차: [../../archive/README.md](../../archive/README.md)
- 폴더 규칙: [../README.md](../README.md), [../../README.md](../../README.md)
