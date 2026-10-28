# coach-agent (실시간 코치)

발표 중 1초마다 판정 모듈(시선 · 말 속도 · 음량 · 군더더기)의 결과와 시간(장별 계획 대비)을 보고
**지금 발표자에게 말을 걸지, 건다면 무엇 하나를 말할지** 정하는 기능입니다.
말한 뒤에는 실제로 행동이 바뀌었는지 보고 같은 방법을 유지할지, 다른 방법을 쓸지, 그만둘지를 고릅니다.
Take가 끝나면 **Take 결과**(영역별 지표 · 문제 구간 · 개입과 효과 · 판정 기준)를 사실만으로 만듭니다.
Take 시작 전에는 LLM이 이번 Take의 **코칭 계획**(먼저 챙길 것 · 봐줄 것)을 세웁니다.

| | |
|---|---|
| 담당 | jewon-kim |
| 판단 방식 | 규칙 + 되돌아보기. 1초 판단에는 LLM을 쓰지 않고, Take 시작 전 코칭 계획에만 LLM을 한 번 씁니다 |
| 판정 | 시선 · 속도 · 음량 · 군더더기 판정은 코치가 하지 않습니다. 판정 모듈(#152~#155)이 하고 코치는 결과를 받습니다 |
| 기능 버전 | `coach-1.3`(시간 판정 `timing-1.1`), 요청 · 응답 스키마 `1.0`, `coach_state` 모양 `2` (서비스 `features/coach/version.py`) |
| 상태 | 코어와 `POST /coach/plan`이 AI 서비스에 있습니다(`evaluate` · `finalize`는 아직 열지 않음). **가상 발표자로만 확인했습니다**: 재생 · 실험은 서비스의 실제 판정 모듈 넷(시선 · 군더더기 · 말 속도 · 소리 크기)으로 돌립니다. 기준값은 실제 발표 데이터로 검증하지 않았습니다 |
| 설계 | 목적 · BE ↔ 코치 계약 · 처리 · 배포 → [`docs/ai/features/coach-agent.md`](../../../docs/ai/features/coach-agent.md) |

## 1. 이 기능이 하는 일

| 단계 | 하는 일 | 언제 | 진입점 |
|---|---|---|---|
| 0 | 대본 · 미션 · 직전 Take 결과 · 직전 리뷰를 읽고 이번 Take에서 먼저 챙길 것 · 봐줄 것을 정한다 (LLM) | Take 시작 전 한 번 | `plan_coaching` |
| 1 | 판정 모듈 결과로 **말할지, 무엇을 말할지** 정한다 | 발표 중 1초마다 | `decide` · `decide_safe` |
| 2 | 마지막 창을 판정하고 열린 것을 닫아 **Take 결과**를 만든다. 합계를 못 믿으면 원자료로 다시 판정한다 | Take 종료 때 한 번 | `finalize` |
| 3 | 가상 발표로 재생하고 Take 결과를 정답과 비교해 채점한다 | 연구할 때만 (배포에는 없음) | `coach_lab` |

```
Take 시작 전:  plan_coaching(대본 · 미션 · 반복 문제 · 직전 Take 결과 · 직전 리뷰, LLM) ─▶ 코칭 계획

FE/BE  시선 · 음량 1초 기록 · STT 단어 · 장 ─┐   Judges (filler → pace → gaze → volume) + timing
계획 · 미션 · 코칭 계획 · 지난 coach_state ──┼─▶ decide() ─▶ WAIT / IGNORE / INTERVENE
                                              │            + 이벤트 + 새 coach_state + 상태 표시

Take 종료:  finalize(마지막 창 · 이벤트 · coach_state [· replay]) ─▶ Take 결과 (+ 닫은 이벤트)
```

## 2. 코드 구조

코드는 두 층이고, import는 위에서 아래로만 합니다.

```
src/coach_lab/     재생 · 실험: 가상 발표자 · 대역 판정 모듈 · 정답 · 채점 · 예시 생성    → research에만 남음
      │ import
pitch_coach_ai.features.coach  판단 코어: 1초 판단 · Take 종료 · 코칭 계획              → AI 서비스(ai/service)에 있음
```

코어의 규칙 (서비스 `tests/unit/coach/test_core_boundary.py`가 코어 파일을 읽어 검사하므로 어기면 테스트가 실패합니다)

| 규칙 | 이유 |
|---|---|
| 패키지 안에서는 **상대 import만** | 폴더 이름이 바뀌어도 고칠 import가 없다 |
| **파일 · DB · 네트워크 · 환경변수를 쓰지 않음** | 서버는 상태를 두지 않는다. 저장은 BE가 맡는다 |
| **시계 · 난수를 쓰지 않음.** 시간은 요청의 `t_ms`뿐 | 같은 요청에 같은 응답이 나와야 재생 결과와 배포 결과가 같다 |
| **기억은 `coach_state`로 주고받음** | 서버를 재시작하거나 늘려도 판단이 같다 |
| **판정 모듈을 import하지 않음.** `Judges` 묶음을 인자로 받음 | 모듈이 나오면 묶음만 바꿔 끼운다 |
| 의존성은 **pydantic 하나** | 서버로 옮길 때 가져갈 라이브러리가 하나뿐이다 |
| **LLM · 캐시는 인자로 받음** | 코어는 키 · 네트워크를 모른다. LLM 라이브러리는 `coach_lab`만 쓴다 |

## 3. 폴더와 파일

```
coach-agent/
├─ README.md
├─ pyproject.toml              패키지 하나: coach_lab(실험). 코어는 서비스를 editable로 설치해 씀
├─ src/coach_lab/              재생 · 실험 도구 (3-2)
├─ scenarios/                  가상 발표 시나리오 16개 (+ plan/ 코칭 계획 실험용)
├─ tests/{lab,live}/          실험 도구 · 실제 LLM (코어 단위 테스트는 서비스 tests/unit/coach/)
├─ reports/results/            커밋하는 실험 결과 (take_result · recovery · coaching_plan)
└─ outputs/                    재생 결과 · 캐시 (git 제외)
```

### 3-1. 판단 코어 (서비스 `ai/service/src/pitch_coach_ai/features/coach/`)

**코어는 AI 서비스로 옮겼습니다**(2026-10-26). 코어는 `ai/service/src/pitch_coach_ai/features/coach/`, 단위 테스트는 `ai/service/tests/unit/coach/`에 있습니다. 이 폴더는 서비스를 editable로 설치해(`pyproject.toml`의 `[tool.uv.sources]`) `coach_lab` · lab 테스트가 `pitch_coach_ai.features.coach`를 import합니다. 코어는 서비스에서 고칩니다. 예시는 서비스 `features/coach/examples/` 한 곳에 있고 BE 테스트(`backend/tests/coach/`)도 그 경로를 읽습니다. BE가 서비스 경로로 바꾼 뒤(#585) 이 폴더의 예시 사본(`src/coach/examples/`)과 사본 비교 테스트를 지웠습니다.

| 옮긴 뒤 확인 | 결과 |
|---|---|
| 옮긴 코어 · 예시 · 프롬프트 | 이름만 바뀜(100%) |
| 테스트 수 | 옮기기 전 605 passed = 옮긴 뒤 서비스 465 + 이 폴더 140 (+ 사본 검사 1). 그 뒤의 테스트 수는 기능 문서 10절 |
| `python -m coach_lab.replay` | 16/16 통과, 출력은 처리 시간(p50 · p95)만 다름 |
| `evaluate --sweep` · `recovery_eval` · `coach_lab.examples` | 다시 만든 `take_result.json` · `recovery.json` · 예시가 옮기기 전과 같음 |

파일마다의 역할은 아래 표에 둡니다(경로는 서비스 `features/coach/` 기준).

| 파일 | 하는 일 |
|---|---|
| `core.py` | 1초 판단 · Take 종료의 진입점: `decide` · `decide_safe` · `finalize` · `ReplayRequired`. 아래 흐름의 순서를 정한다(코칭 계획의 진입점은 `planner.py`의 `plan_coaching`) |
| `judges.py` | `Judges` 묶음과 한 번의 판정 라운드(`run`): filler → pace → gaze → volume → timing 호출, 커서 · 합계 누적, 모듈 예외 처리, 기준 음량(volume의 `speaking_levels` · `baseline`), 새로 시작한 상태의 커서 시작, `skip` |
| `tally.py` | 판정 결과의 `tally` 조각을 Take 합계 · 장 번호별 합계 · 장 방문에 더하고 센 구간을 표시 |
| `measure.py` | 판정 결과 → `Tick`(지표 · 문제 `Detection`). 상태 표시(`indicators`) |
| `tick.py` | 한 번의 판단이 들고 다니는 값(`Tick`)과 `Detection` |
| `candidates.py` | 문제 → 후보 행동. 전략 사다리의 몇 번째 칸인지에 따라 같은 문제도 다른 행동이 나온다 |
| `eligibility.py` | 지금 말하면 안 되는 후보를 거른다 (실전 모드 · 센서 불량 · 간격 · 쿨다운 · 계획 …). 걸린 이유는 전부 남긴다 |
| `priority.py` | 후보 점수 (심각도 × 신뢰도 × 지속 × 미션 × 반복 × 계획 × 시간 × 악화 × 새로움) |
| `policy.py` | WAIT / IGNORE / INTERVENE 선택. 말하는 도중에는 문장 끝을 최대 3초 기다린다 |
| `reflection.py` | 되돌아보기: 개입 몇 초 뒤 효과를 재고 같은 방법 유지 · 다음 칸 · 포기를 정한다 |
| `episodes.py` | 같은 문제가 이어지는 동안을 문제 구간 하나로 묶어 `EPISODE`로 남긴다 |
| `slides.py` | 장 방문 하나마다 `SLIDE`(영역별 합계)를 낸다. 단어 확정이 늦어도 말한 장에 붙는다 |
| `take_lists.py` | Take 결과의 문제 구간 · 개입 · 포기를 이벤트에서 만든다 |
| `recovery.py` | replay: Take 전체 원자료로 실시간 요청 시각(1초 기록이 끝난 시각)마다의 요청을 다시 만든다 |
| `timing.py` | 계획 대비 시간 판정. 다른 판정 모듈과 같은 결과 모양으로 낸다 |
| `planner.py` | Take 시작 전 코칭 계획 (LLM은 인자) |
| `prompts/plan.py` | 코칭 계획 LLM 프롬프트. 바꾸면 `planner_hash`가 바뀐다 |
| `renderer.py` | `instruction` + `variant` → 템플릿 문장 (LLM이 문장을 만들지 않는다) |
| `schemas.py` | 요청 · 응답 · 이벤트 · Take 결과 · 판정 결과 모양의 단일 진실 원천 (pydantic) |
| `config.py` | 모든 기준값 · 사다리 · 문구. `config_hash()`가 응답 `meta`에 실린다 |
| `vocab.py` | 고정 어휘 (영역 · 행동 · 문제 · 이유 코드). BE · FE와 공유하는 계약 |
| `events.py` | 이벤트 모으기와 `event_id` 규칙 |
| `state.py` | `coach_state` 모양과 읽기 · 쓰기. 버전이 다르면 버리고 새로 시작 |
| `version.py` | 스키마 · 기능 · state · 시간 판정 버전 |

### 3-2. 재생 · 실험 도구 `src/coach_lab/`

| 파일 | 하는 일 |
|---|---|
| `simulator.py` | 가상 발표자: 시나리오 JSON → 1초 단위 요청. BE가 할 일(창 모으기 · `coach_state` 붙이기 · 이벤트 쌓기 · finalize)을 흉내 내고, 측정 잡음(`clean` · `noisy` · `harsh`)과 정답 기록을 더한다 |
| `judges/` | **대역 판정 모듈** (gaze · pace · volume · filler). 옛 평가기의 규칙 · 기준값을 #152~#155 계약 모양으로 옮긴 것. `real_judges()`는 서비스의 실제 판정 모듈 넷을 묶어 재생 · 채점 · 복구 실험이 쓴다. `lab_judges()`는 대역 넷을 묶어 대역 시험이 쓴다 |
| `replay.py` | 시나리오 재생과 `expect` 검사. 결과는 `outputs/replay/`에 |
| `truth.py` | 정답: 잡음 없는 실제 상태 × 코치와 같은 기준값. 센서가 볼 수 없던 문제는 정답에서도 뺀다 |
| `evaluate.py` | Take 결과 채점 (구간 재현율 · 정밀도 · 근거 없는 지적 · IoU · 시작 오차 · 조각남 · 장별 오차 · 효과 판정 정확도). 병합 간격 × 최소 구간 격자(`--sweep`) |
| `recovery_eval.py` | 복구 실험: 정상 / 요청 누락 / replay로 돌린 Take 결과를 서로 비교 |
| `examples.py` | 서비스 `features/coach/examples/`의 evaluate · finalize 예시 파일을 실제 판정 모듈 넷(`real_judges()`)으로 만든다 (다시 돌려도 바이트까지 같다) |
| `plan_eval.py` · `llm.py` · `cache.py` | 코칭 계획 실험: 실제 LLM으로 계획을 세우고 유효성 · 일관성 · 재생 효과를 잰다. 응답은 `outputs/llm_cache.sqlite`에 캐시 |
| `usage.py` | LLM 호출마다 출력 토큰 수 · 끝난 이유를 모으고 역할 상한(잰 최대 × 2, 1,024 단위, 최소 2,048)을 계산한다. `plan_eval --usage`가 쓴다 |
| `paths.py` | 폴더 위치 |

## 4. 실행 흐름

### 4-1. 1초 판단 (`decide`)

```
① 판정 라운드   judges.run: filler → pace → gaze → volume → timing 을 부르고 tally 를 누적, 커서를 넘긴다
② 측정 정리     판정 결과 → Tick(지표 · 문제)
   장 이벤트    끝난 장 방문 중 단어가 다 확정된 것을 SLIDE 로
   되돌아보기   잴 때가 된 개입의 효과 → OUTCOME, 필요하면 STRATEGY
   문제 구간    열기 · 이어가기 · 닫기(EPISODE)
③ 후보 생성 → ④ 적격성 필터 → ⑤ 우선순위 → ⑥ 행동 선택
⑦ 문구 렌더링  (INTERVENE 일 때)            ⑧ 이벤트 · 참은 기록 · 기록 · coach_state
```

- `t_ms`가 이미 처리한 시각 이하면 아무것도 바꾸지 않고 `WAIT` + `STALE_TICK`. 이미 처리한 상태에서 지난 처리 시각보다 60초 넘게 앞서면 처리하지 않고 `WAIT` + `T_MS_JUMP`와 빈 `coach_state`를 돌려준다(상한 `MAX_T_MS`는 `schemas.py`). 새로 시작한 상태(Take 첫 요청 · `STATE_RESET` · `T_MS_JUMP` 뒤)의 첫 요청은 시각 커서를 `max(0, t_ms − 30초)`에서 시작한다.
- `decide_safe`는 코치 안의 예외를 `WAIT` + `INTERNAL_ERROR`로 바꾸고 그 시간을 잴 수 없던 것으로 넘긴다. 요청 형식 오류(`ValidationError`)는 그대로 올린다.
- 판정 모듈 하나가 실패하면 그 영역만 잴 수 없음으로 두고 계속한다 ([설계 문서](../../../docs/ai/features/coach-agent.md) 7절).

### 4-2. Take 종료 (`finalize`)

마지막 창 판정(Take 끝을 STT 모듈 입력 `take_end_ms`로 넘긴다) → 열린 효과 · 문제 구간 · 장 방문 닫기 → 센 구간이 Take를 덮는지 확인(못 덮으면 `ReplayRequired`) →
합계를 모듈의 `summarize`에 넣어 `areas` → 이벤트에서 문제 구간 · 개입 · 포기.
`replay`(Take 전체 원자료)가 있으면 `recovery.py`가 실시간 요청 시각(1초 기록이 끝난 시각, 기록이 없는 구간은 1초 간격)마다 요청을 다시 만들어 `EXAM` 모드로 처음부터 판정하고, 지표 · 문제 구간은 그 결과에서, 개입 · 효과는 받은 이벤트에서 가져온다. 마지막 창은 마지막 evaluate와 같은 시각이어도 부른다. 요청에 `plan`이 실렸으면 Take 결과 `plan`에 시간 계획을 남긴다.

### 4-3. Take 시작 전 (`plan_coaching`)

LLM이 `focus`(가중치) · `relax`(장 단위로 봐줄 영역) · `max_interventions`를 낸다. 코치는 그것을 `config.planner` 범위로 검증하고 자른다(`relax`는 시선 · 군더더기만). 요청에 모드가 없고, LLM 입력의 개입 수 · 효과 있던 비율 · 포기한 문제 · 문제 구간 길이는 직전 Take 결과에서 코치가 직접 센다. 개입 상한의 근거도 그 Take 결과의 개입 수(유지 격려 포함)다. LLM 없음은 `LLM_UNAVAILABLE`, 실패는 원인에 따라 `LLM_TIMEOUT` · `LLM_ERROR` · `LLM_INVALID_OUTPUT`인 기본 계획 + `fallback_reason`이고, 계획 없이도 코칭은 그대로 돈다. 응답은 요청의 `source_take_id` · `script_version_id`를 그대로 돌려주고 `meta`를 싣는다.

## 5. 판단 규칙

판단 규칙(문제별 사다리 · 말하지 않는 이유 · Take 결과 규칙)은 [설계 문서](../../../docs/ai/features/coach-agent.md) 6-1 · 6-3에 있습니다. 값의 원본은 서비스 `features/coach/config.py`(`_default_issue_rules` · `config.take_result`)입니다.

## 6. 실행법

```bash
cd ai/research/coach-agent
uv sync                                   # Python 3.12, 라이브러리 + 개발 도구 설치
```

- `.venv`는 git에 없습니다. 클론하거나 폴더를 옮긴 뒤에는 `uv sync`로 다시 만듭니다.
- 1초 판단 · 재생 · Take 결과 실험은 LLM을 쓰지 않아 `ai/.env`가 필요 없습니다. 코칭 계획 실험과 live 테스트만 `ai/.env`의 `OPENAI_API_KEY` · `OPENAI_MODEL` · `OPENAI_BASE_URL`을 씁니다.
- Windows에서는 `pytest.exe` 같은 실행 파일이 막힐 수 있어 `uv run python -m …` 형태로 부릅니다. 콘솔 한글이 깨지면 `PYTHONIOENCODING=utf-8`.

| 하는 일 | 명령 |
|---|---|
| 테스트 | `uv run python -m pytest` (실험 도구 `tests/lab`. 코어 단위 테스트는 `ai/service`에서 `uv run python -m pytest tests/unit/coach`) |
| 실제 LLM으로 계획 한 번 (과금) | `uv run python -m pytest -m live` |
| 린트 | `uv run ruff check . && uv run ruff format --check .` |
| 시나리오 재생 | `uv run python -m coach_lab.replay` (하나만: `… replay scenarios/05_time_behind.json -v`, 설정 덮어쓰기: `--config my.json`) |
| Take 결과 채점 | `uv run python -m coach_lab.evaluate --sweep --out reports/results/take_result.json` (`--seeds`, `--noise clean,noisy,harsh`, `--explain`) |
| 복구 실험 | `uv run python -m coach_lab.recovery_eval` (결과: `reports/results/recovery.json`, `--seeds`) |
| 예시 파일 다시 만들기 | `uv run python -m coach_lab.examples` (서비스 `features/coach/examples/`) |
| 코칭 계획 실험 (과금) | `uv run python -m coach_lab.plan_eval --out reports/results/coaching_plan.json` |
| 코칭 계획 출력 토큰 (과금, 25번) | `uv run python -m coach_lab.plan_eval --usage --out reports/results/plan_output_tokens.json` (스트리밍 · 캐시 없음, 결과의 `usage`) |

설정은 `config.py`에만 있습니다. 코드를 고치지 말고 바꿀 값만 JSON으로 덮어쓰세요(`--config`). 코어에서는 `load_config(policy={"cooldown_ms": 45_000})`처럼 키워드로 넘깁니다(코어는 파일을 읽지 않습니다).
설정을 바꾸면 응답 `meta.criteria_versions.coach`의 해시가 달라져 어떤 설정으로 판단했는지 추적됩니다.

파이썬에서 직접

```python
from pitch_coach_ai.features.coach import decide_safe, finalize
from coach_lab.judges import real_judges  # 서비스의 실제 판정 모듈 넷

judges = real_judges()
resp = decide_safe(request_dict, judges)  # 1초마다. resp.coach_state 를 다음 요청에 붙인다
result = finalize(finalize_request_dict, judges)
```

### 재생 시나리오

시나리오 하나 = 가상 발표 하나 + 기대 결과입니다. 모양은 `coach_lab/simulator.py`의 `Scenario`가 원본이고, `tests/lab/test_replay.py`가 `scenarios/*.json`을 전부 돌립니다.
가상 발표자는 3글자 단어를 `cpm` 속도로 말하고, 코치의 말에 `reactions`대로 반응할 수 있어 효과 판정과 사다리를 재생할 수 있습니다.
실제 데이터가 아니라, 기준값이 맞는지가 아니라 로직이 의도대로 도는지를 봅니다.

| 시나리오 | 확인하는 것 |
|---|---|
| `01_baseline_good` | 문제없는 발표자에게 한마디도 하지 않는다 |
| `02_gaze_effective` | 시선 지적 → 고개를 듦 → 효과 있음 → 유지 격려 |
| `03_gaze_gave_up` | 안 바뀜 → 다른 방법 → 그 장에서 포기 |
| `04_gaze_sensor_unusable` | 얼굴이 자주 안 잡힐 때 시선 지적 없음 |
| `05_time_behind` | 늦어지면 `SPEED_UP`, "천천히"는 나오지 않음 |
| `06_pace_fast` | 빨라지면 `SLOW_DOWN` → 효과 있음 |
| `07_exam_mode` | 실전 모드: 말하지 않고 문제 구간만 남김 |
| `08_filler_frequent` | 군더더기 지적 → 줄어듦 |
| `09_long_silence` | 5초 넘게 멈추면 `RESUME` |
| `10_audio_dead` | 오디오가 멈춰도 침묵 · 늦음 오탐 없음 |
| `11_keyword_missing` | 필수 키워드를 빠뜨려도 코치는 말하지 않는다 (내용 판정은 코치의 일이 아님) |
| `12_no_slide_plan` | 장별 계획이 없으면 남은 시간만으로 마지막 1분 · 시간 초과 |
| `13_recurring_resolved` · `14_recurring_persists` | 이전 Take의 문제가 사라짐 / 그대로 |
| `15_mixed_burdens` | 문제가 여럿일 때 1순위는 가장 크고 오래간 것 |
| `16_noisy_sensors` | 센서가 나쁜 동안의 시선 · 속도는 문제로 말하지 않음 |

## 7. 현재 결과

커밋한 결과 파일에서 가져온 값입니다 (`config_hash` `8b1e640e988a`, 기능 버전 `coach-1.3`, 16 시나리오). **가상 발표자 위의 값이며 실제 성능이 아닙니다.** 판정 모듈 넷은 서비스의 실제 모듈입니다(`coach_lab.judges.real_judges`, 시선 · 군더더기는 2026-10-26부터, 말 속도는 2026-10-28부터, 소리 크기는 2026-10-29부터).

**소리 크기를 대역에서 실제 모듈(`volume-1.1`)로 바꾸며 달라진 것** (2026-10-29, 재생 16/16 통과)
- 바뀐 시나리오는 `16_noisy_sensors` 하나입니다. 4번 장의 작은 목소리(`VOLUME_LOW`) 구간이 2분 20초 → 2분 22초에 시작합니다. 실제 모듈은 짧은 창의 음량을 dB 평균이 아니라 에너지 평균(Leq, `voiced_ms` 가중)으로 내고, '말한 1초'를 `voiced_ms` 기준으로 고릅니다(대역은 침묵 시간 기준). 이 두 차이가 작은 목소리를 잡는 시점에 영향을 줄 수 있지만, 어느 차이가 얼마나 영향을 줬는지는 따로 재지 않았습니다. 그 사이 시간 판정(`AHEAD_OF_SCHEDULE`)이 먼저 개입해 두 개입의 순서가 바뀌었고(2분 27초 `SLOW_DOWN` → 2분 42초 `SPEAK_LOUDER`), 효과가 없던 시간 문제를 포기로 남깁니다. 기대(오디오가 멀쩡한 4번 장의 작은 목소리만 문제로)는 그대로 통과합니다.
- 아래 표의 변화: 시작 오차 clean 1.27 → 1.36초 · noisy 2.25 → 2.31초, 구간 IoU 0.786 → 0.781 · 0.764 → 0.762 · 0.722 그대로(소수 셋째 자리), noisy 효과 판정 정확도 0.943 → 0.938. 재현율 · 정밀도 · F1 · 조각남 · 불필요한 개입과 장별 오차는 같습니다.
- 복구 실험(`recovery.json`)은 바뀌지 않았습니다.
- 대역 그대로 다시 돌린 결과가 커밋된 결과 파일과 같은지 먼저 확인한 뒤 바꿨습니다.

**말 속도를 대역에서 실제 모듈(`pace-1.0`)로 바꾸며 달라진 것** (2026-10-28, 재생 16/16 통과)
- 구간 · 개입 지표(재현율 · 정밀도 · F1 · IoU · 시작 오차 · 조각남 · 효과 판정 · 불필요한 개입)와 격자는 바뀌지 않았습니다. 말 속도 개입 횟수도 같습니다.
- 장별 CPM 평균 절대 오차가 줄었습니다: clean 4.2 → 0.2, noisy 5.5 → 2.5, harsh 6.9 → 4.9. CPM은 대역 · 실제 모듈 모두 `tally`의 글자 수 ÷ 발화 시간이라, 줄어든 것은 `tally`에 넣는 규칙이 달라서로 보입니다: 실제 모듈은 군더더기 단어의 발화 시간을 넣지 않고, 글자 수에서 문장부호도 빼며, 보류 · 겹친 단어를 다르게 다룹니다(연구 폴더 `speech-pace` README '코치 연구용 대역(`pace-0.2`)과 달라진 점'). 어느 규칙이 얼마나 줄였는지는 하나씩 되돌려 재지 않았습니다. 정답 CPM은 시뮬레이터 값이라 대역 기준값과 관계없습니다.
- 복구 실험: `short_drop`의 clean CPM 차이가 0.0187 → 0이 됐습니다(noisy는 0.028 그대로). 그 밖의 값은 같습니다.

**시선 · 군더더기를 대역에서 실제 모듈로 바꾸며 달라진 것** (2026-10-26, 재생 16/16 통과)
- 시뮬레이터 시선 1초 기록에 신뢰도(`confidence` · `reliability` · `frames`)를 넣었습니다. 없으면 실제 시선 모듈이 Take 내내 잴 수 없음으로 봅니다.
- 실제 시선 모듈은 대역이 내지 않던 `GAZE_LOW_EYE_CONTACT`를 `GAZE_ON_SCRIPT`와 겹쳐 냅니다. 그래서 `03_gaze_gave_up`의 둘째 개입과 포기 대상이 `GAZE_LOW_EYE_CONTACT`가 되어 기대를 맞췄고, 같은 문제를 여러 조각으로 짚어 조각남이 1.1 → 1.5~1.7로 늘었습니다. 겹친 지적 때문에 구간 IoU가 1을 넘던 채점 도구 버그를 고쳤습니다(겹친 지적을 합친 뒤 계산).
- 군더더기는 배포 기본값(T1만 셈)이라 대역과 같은 기준이고, `08_filler_frequent`는 그대로 통과합니다.
- 아래 표의 변화: clean 구간 재현율 1.000 → 0.917(정답 구간 하나를 놓침), noisy · harsh 정밀도 · 근거 없는 지적은 좋아짐, 시작 오차는 0.3~0.4초 늘어남.

### Take 결과 채점 (`reports/results/take_result.json`)

정답은 시뮬레이터가 기록한 실제 상태 × 코치와 같은 기준값입니다. 잡음 수준 `clean`은 1회, `noisy` · `harsh`는 시나리오마다 5 seed(80 Take)입니다. 기본 설정(병합 10초 · 최소 3초)입니다.

| 지표 | clean | noisy | harsh |
|---|---|---|---|
| 구간 재현율 | 0.917 | 0.952 | 0.934 |
| 구간 정밀도 | 1.000 | 0.979 | 0.924 |
| 구간 F1 | 0.957 | 0.965 | 0.929 |
| 근거 없는 지적 (Take 당) | 0 | 0.025 | 0.088 |
| 구간 IoU | 0.781 | 0.762 | 0.722 |
| 시작 오차 (초) | 1.36 | 2.31 | 2.71 |
| 조각남 (문제 하나당 조각 수) | 1.55 | 1.69 | 1.61 |
| 효과 판정 정확도 | 1.000 | 0.938 | 1.000 |
| 불필요한 개입률 | 0 | 0 | 0.022 |

- 장별 평균 절대 오차 (clean / noisy / harsh): CPM 0.2 / 2.5 / 4.9, 장 시간 0.34 / 0.37 / 0.37초, 대본 응시 비율 0.032 / 0.059 / 0.104.
- 격자(`--sweep`): 실제 시선 모듈로는 최소 구간을 6초로 올려도 noisy F1이 오르지 않습니다(0.965 → 0.958, 재현율 0.952 → 0.919). 병합 간격은 조각남뿐 아니라 정밀도 · F1 · IoU에도 조금 영향을 줍니다. 기본값은 그대로입니다.

### 복구 실험 (`reports/results/recovery.json`)

같은 Take를 정상 / 요청 누락 / replay로 돌려 Take 결과를 비교합니다. clean 16 Take, noisy 32 Take(2 seed).
`short_drop`은 60~80초 요청을 보내지 않은 경우(창 30초보다 짧아 다음 요청이 메움), `long_drop`은 60~100초를 보내지 않은 경우(창보다 길어 409 → replay).

| 경우 | 재판정 필요 | 정상 구간 중 일치 (clean / noisy) | 경우 구간 중 정상과 일치 (clean / noisy) | 지표 차이 |
|---|---|---|---|---|
| replay | — | 1.000 / 1.000 | 1.000 / 1.000 | 영역 지표 모두 0 |
| short_drop | 0% | 0.875 / 0.844 | 0.918 / 0.922 | CPM 0 / 0.03, 분당 군더더기 0.009 / 0.008, 측정 비율 0.004 |
| long_drop | 100% | 0.917 / 0.938 | 1.000 / 1.000 | 영역 지표 모두 0 |

한쪽에만 값이 있는 지표(`values_missing`)는 모든 경우 0입니다. replay · long_drop의 영역 지표(`take`)는 정상과 같고, short_drop은 빠진 20초의 단어 몫 때문에 말 속도 · 군더더기 지표가 아주 조금 다릅니다(이 차이는 대역 때 결과 파일에도 있었습니다). 달라지는 것은 주로 문제 구간 일부입니다.

### 코칭 계획 실험 (`reports/results/coaching_plan.json`)

실제 LLM(`openai/gpt-6-luna`, 기본 묶음)으로 시나리오 5개 × 5번(25 계획): 유효 100%, 기대 통과 100%, 검증에서 뺀 비율 0%, 일관성 0.95, 지연 중앙값 3.1초 · 최대 7.8초. 계획 입력을 직전 Take 결과 · 리뷰 모양으로 바꾼 프롬프트(`planner_hash` `56827d3cf73d`)로 잰 값이다.

코어를 서비스로 옮긴 뒤 다시 돌린 값(2026-10-26, 결과 파일은 그대로 두고 비교만): 유효 100%, 기대 통과 96%(25개 중 `19_plan_nothing_special` 한 번이 근거 없이 3번 장 시선을 봐줌), 검증에서 뺀 비율 0%, 일관성 0.95, 지연 중앙값 3.0초 · 최대 6.7초. 그 밖의 지표는 같습니다. 다시 돌린 결과는 커밋하지 않아(`outputs/`) 차이의 원인을 더 따지지 않았고, 같은 입력에서 다섯 번 중 한 번만 달라 LLM 답의 흔들림으로 보입니다.

## 8. 알려진 한계

- **가상 발표자로만 확인했습니다.** 판정 모듈 넷은 서비스의 실제 모듈이지만, 기준값(대본 응시 5초 · 350 CPM 등)은 실제 연습 데이터로 다시 골라야 합니다.
- 판정 모듈의 `since_ms` · `counted_until_ms` 계약은 코치의 커서와 맞물려 있습니다. 판정 모듈을 바꾸면 같은 실험을 다시 돌립니다.
- 개입 규칙이 아직 `Tick`으로 옮긴 지표를 읽습니다(`measure.py`). 판정 결과를 직접 읽도록 줄이는 일은 #150의 개입 규칙 작업에 남아 있습니다.
- 요청이 30초보다 길게 빠지면 합계로는 Take 결과를 만들 수 없고 replay가 필요합니다. replay는 Take 전체 원자료를 BE가 보관해야 쓸 수 있습니다.
- 효과 판정은 실시간 지표(짧은 창)로 잽니다. 개입이 잡음으로 튄 순간에 일어나기 쉬워, 시선은 "기준선으로 돌아옴"만 효과로 인정합니다.
- `GAZE_ON_SCREEN` · `GAZE_UNMEASURABLE` · `PACE_SLOW`는 정상 행동과 가릴 근거가 없어 말하지 않고 문제 구간에만 남깁니다.
