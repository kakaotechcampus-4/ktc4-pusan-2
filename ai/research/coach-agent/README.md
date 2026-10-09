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
| 기능 버전 | `coach-1.2`, 요청 · 응답 스키마 `1.0`, `coach_state` 모양 `2` (`src/coach/version.py`) |
| 상태 | 판정 모듈 계약 · 패키지 + 테스트. **가상 발표자와 대역 판정 모듈로만 확인했습니다.** 기준값은 실제 발표 데이터로 검증하지 않았습니다 |
| 배포 | 판단 코어(`src/coach/`)는 나중에 AI 서버로 그대로 옮깁니다 → [DEPLOY.md](DEPLOY.md) |
| 입출력 | BE ↔ 코치 계약 → [INTERFACE.md](INTERFACE.md) |

## 1. 이 기능이 하는 일

| 단계 | 하는 일 | 언제 | 진입점 |
|---|---|---|---|
| 0 | 대본 · 미션 · 직전 리뷰를 읽고 이번 Take에서 먼저 챙길 것 · 봐줄 것을 정한다 (LLM) | Take 시작 전 한 번 | `plan_coaching` |
| 1 | 판정 모듈 결과로 **말할지, 무엇을 말할지** 정한다 | 발표 중 1초마다 | `decide` · `decide_safe` |
| 2 | 마지막 창을 판정하고 열린 것을 닫아 **Take 결과**를 만든다. 합계를 못 믿으면 원자료로 다시 판정한다 | Take 종료 때 한 번 | `finalize` |
| 3 | 가상 발표로 재생하고 Take 결과를 정답과 비교해 채점한다 | 연구할 때만 (배포에는 없음) | `coach_lab` |

```
Take 시작 전:  plan_coaching(대본 · 미션 · 기억 · 직전 리뷰, LLM) ─▶ 코칭 계획

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
src/coach/         판단 코어: 1초 판단 · Take 종료 · 코칭 계획                          → 나중에 AI 서버로 폴더째 복사
```

코어의 규칙 (`tests/unit/test_core_boundary.py`가 코어 파일을 읽어 검사하므로 어기면 테스트가 실패합니다)

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
├─ README.md · INTERFACE.md · DEPLOY.md
├─ pyproject.toml              패키지 두 개: coach(코어) · coach_lab(실험)
├─ src/coach/                  판단 코어 (3-1)
│   ├─ examples/               BE 개발자용 실행 가능한 예시 (evaluate · finalize · timing)
│   └─ prompts/plan.py         코칭 계획 LLM 프롬프트
├─ src/coach_lab/              재생 · 실험 도구 (3-2)
├─ scenarios/                  가상 발표 시나리오 16개 (+ plan/ 코칭 계획 실험용)
├─ tests/{unit,lab,live}/      코어 · 실험 도구 · 실제 LLM
├─ reports/results/            커밋하는 실험 결과 (take_result · recovery · coaching_plan)
└─ outputs/                    재생 결과 · 캐시 (git 제외)
```

### 3-1. 판단 코어 `src/coach/`

| 파일 | 하는 일 |
|---|---|
| `core.py` | 유일한 진입점: `decide` · `decide_safe` · `finalize` · `ReplayRequired`. 아래 흐름의 순서를 정한다 |
| `judges.py` | `Judges` 묶음과 한 번의 판정 라운드(`run`): filler → pace → gaze → volume → timing 호출, 커서 · 합계 누적, 모듈 예외 처리, 기준 음량, `skip` |
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
| `recovery.py` | replay: Take 전체 원자료로 1초마다의 요청을 다시 만든다 |
| `timing.py` | 계획 대비 시간 판정. 다른 판정 모듈과 같은 결과 모양으로 낸다 |
| `planner.py` | Take 시작 전 코칭 계획 (LLM은 인자) |
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
| `judges/` | **대역 판정 모듈** (gaze · pace · volume · filler). 옛 평가기의 규칙 · 기준값을 #152~#155 계약 모양으로 옮긴 것. `lab_judges()`가 `Judges`로 묶는다. 기능 모듈이 나오면 이것 대신 그 모듈을 쓴다 |
| `replay.py` | 시나리오 재생과 `expect` 검사. 결과는 `outputs/replay/`에 |
| `truth.py` | 정답: 잡음 없는 실제 상태 × 코치와 같은 기준값. 센서가 볼 수 없던 문제는 정답에서도 뺀다 |
| `evaluate.py` | Take 결과 채점 (구간 재현율 · 정밀도 · 근거 없는 지적 · IoU · 시작 오차 · 조각남 · 장별 오차 · 효과 판정 정확도). 병합 간격 × 최소 구간 격자(`--sweep`) |
| `recovery_eval.py` | 복구 실험: 정상 / 요청 누락 / replay로 돌린 Take 결과를 서로 비교 |
| `examples.py` | `src/coach/examples/`의 evaluate · finalize 예시 파일을 만든다 (다시 돌려도 바이트까지 같다) |
| `plan_eval.py` · `llm.py` · `cache.py` | 코칭 계획 실험: 실제 LLM으로 계획을 세우고 유효성 · 일관성 · 재생 효과를 잰다. 응답은 `outputs/llm_cache.sqlite`에 캐시 |
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

- `t_ms`가 이미 처리한 시각 이하면 아무것도 바꾸지 않고 `WAIT` + `STALE_TICK`.
- `decide_safe`는 코치 안의 예외를 `WAIT` + `INTERNAL_ERROR`로 바꾸고 그 시간을 잴 수 없던 것으로 넘긴다. 요청 형식 오류(`ValidationError`)는 그대로 올린다.
- 판정 모듈 하나가 실패하면 그 영역만 잴 수 없음으로 두고 계속한다 ([INTERFACE.md](INTERFACE.md) 2-3).

### 4-2. Take 종료 (`finalize`)

마지막 창 판정(Take 끝을 문장 끝으로 본다) → 열린 효과 · 문제 구간 · 장 방문 닫기 → 센 구간이 Take를 덮는지 확인(못 덮으면 `ReplayRequired`) →
합계를 모듈의 `summarize`에 넣어 `areas` → 이벤트에서 문제 구간 · 개입 · 포기.
`replay`(Take 전체 원자료)가 있으면 `recovery.py`가 1초마다의 요청을 다시 만들어 `EXAM` 모드로 처음부터 판정하고, 지표 · 문제 구간은 그 결과에서, 개입 · 효과는 받은 이벤트에서 가져온다.

### 4-3. Take 시작 전 (`plan_coaching`)

LLM이 `focus`(가중치) · `relax`(장 단위로 봐줄 영역) · `max_interventions`를 낸다. 코치는 그것을 `config.planner` 범위로 검증하고 자른다. 실전 모드 · LLM 없음 · LLM 오류는 기본 계획 + `fallback_reason`이고, 계획 없이도 코칭은 그대로 돈다.
입출력은 후속 작업(#162)에서 Take 결과 · 리뷰 출력에 맞춰 바뀝니다.

## 5. 판단 규칙

### 5-1. 문제별 사다리

효과가 없으면 사다리를 한 칸씩 내려가고, 끝까지 가면 그 범위에서 그만둡니다(`GAVE_UP`). 값은 `config.py`의 `_default_issue_rules`가 원본입니다.

| 문제 | 사다리 (행동 · 문구 종류) | 말하는 조건 | 효과를 재는 시점 |
|---|---|---|---|
| `GAZE_ON_SCRIPT` `GAZE_AWAY` `GAZE_LOW_EYE_CONTACT` | `LOOK_AT_CAMERA` → `LOOK_AT_CAMERA`(문장 시작) | 3초 이어짐 | 12초 뒤 |
| `PACE_FAST` | `SLOW_DOWN` → `SLOW_DOWN`(문장 끝 쉬기) | 5초 이어짐 | 10초 뒤 |
| `VOLUME_LOW` | `SPEAK_LOUDER` → `SPEAK_LOUDER`(멀리) | 5초 이어짐 | 8초 뒤 |
| `LONG_SILENCE` | `RESUME` | | 5초 뒤 |
| `FILLER_FREQUENT` | `REDUCE_FILLER` → `REDUCE_FILLER`(호흡) | | 30초 뒤 |
| `BEHIND_SCHEDULE` | `SPEED_UP` → `CONDENSE` → `WRAP_UP` | | 15초 뒤 |
| `AHEAD_OF_SCHEDULE` | `SLOW_DOWN`(시간 여유) | 10초 이어짐 | 10초 뒤 |
| `SLIDE_OVER` | `MOVE_ON` → `MOVE_ON`(단호) | | 15초 뒤 |
| `FINAL_MINUTE` `TIME_OVER` | `WRAP_UP` (각 1번만) | | |
| `GAZE_ON_SCREEN` `GAZE_UNMEASURABLE` `PACE_SLOW` | 말하지 않고 문제 구간에만 남긴다 | | |

효과가 있으면 `CONTINUE`("지금처럼")로 격려합니다(시선 · 속도 · 음량 · 군더더기). 시선 문제 셋은 장마다 사다리 하나를 같이 씁니다.

### 5-2. 말하지 않는 이유 (적격성)

하나라도 `IGNORE` 이유면 버리고(`IGNORED`), 아니고 `WAIT` 이유가 있으면 보류(`WAITING`)합니다. 걸린 이유는 전부 `SUPPRESSED` 이벤트와 `EPISODE.suppressed_reasons`에 남습니다.
- 버림: 실전 모드, 센서 불량, 낮은 신뢰도, 대본 사용 허용, 늦는 중에 "천천히", 사다리 소진, 이미 전했음, 계획이 봐주기로 함, 개입 상한, 낮은 점수.
- 보류: 지속시간 미달, 직전 개입과의 간격, 같은 행동의 쿨다운, 말하는 도중이라 문장 끝 대기.

### 5-3. Take 결과 규칙

문제 구간의 신뢰도 · 합치기 · 보정 규칙과 `areas`의 비움 규칙은 [INTERFACE.md](INTERFACE.md) 3-3 · 3-5에 있습니다. 설정값은 `config.take_result`에 있고 실시간 판단에는 쓰이지 않습니다.

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
| 테스트 | `uv run python -m pytest` (코어만: `tests/unit`, 실험 도구: `tests/lab`) |
| 실제 LLM으로 계획 한 번 (과금) | `uv run python -m pytest -m live` |
| 린트 | `uv run ruff check . && uv run ruff format --check .` |
| 시나리오 재생 | `uv run python -m coach_lab.replay` (하나만: `… replay scenarios/05_time_behind.json -v`, 설정 덮어쓰기: `--config my.json`) |
| Take 결과 채점 | `uv run python -m coach_lab.evaluate --sweep --out reports/results/take_result.json` (`--seeds`, `--noise clean,noisy,harsh`, `--explain`) |
| 복구 실험 | `uv run python -m coach_lab.recovery_eval` (결과: `reports/results/recovery.json`, `--seeds`) |
| 예시 파일 다시 만들기 | `uv run python -m coach_lab.examples` (`src/coach/examples/`) |
| 코칭 계획 실험 (과금) | `uv run python -m coach_lab.plan_eval --out reports/results/coaching_plan.json` |

설정은 `config.py`에만 있습니다. 코드를 고치지 말고 바꿀 값만 JSON으로 덮어쓰세요(`--config`). 코어에서는 `load_config(policy={"cooldown_ms": 45_000})`처럼 키워드로 넘깁니다(코어는 파일을 읽지 않습니다).
설정을 바꾸면 응답 `meta.criteria_versions.coach`의 해시가 달라져 어떤 설정으로 판단했는지 추적됩니다.

파이썬에서 직접

```python
from coach import decide_safe, finalize
from coach_lab.judges import lab_judges        # 대역 판정 모듈. 배포에서는 기능 모듈로 만든 Judges

judges = lab_judges()
resp = decide_safe(request_dict, judges)       # 1초마다. resp.coach_state 를 다음 요청에 붙인다
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

커밋한 결과 파일에서 가져온 값입니다 (`config_hash` `9d82f5b1d7bb`, 기능 버전 `coach-1.2`, 16 시나리오). **가상 발표자 · 대역 판정 모듈 위의 값이며 실제 성능이 아닙니다.**

### Take 결과 채점 (`reports/results/take_result.json`)

정답은 시뮬레이터가 기록한 실제 상태 × 코치와 같은 기준값입니다. 잡음 수준 `clean`은 1회, `noisy` · `harsh`는 시나리오마다 5 seed(80 Take)입니다. 기본 설정(병합 10초 · 최소 3초)입니다.

| 지표 | clean | noisy | harsh |
|---|---|---|---|
| 구간 재현율 | 1.000 | 0.934 | 0.952 |
| 구간 정밀도 | 1.000 | 0.919 | 0.875 |
| 구간 F1 | 1.000 | 0.927 | 0.912 |
| 근거 없는 지적 (Take 당) | 0 | 0.063 | 0.113 |
| 구간 IoU | 0.819 | 0.772 | 0.730 |
| 시작 오차 (초) | 0.87 | 1.98 | 2.38 |
| 조각남 (문제 하나당 조각 수) | 1.08 | 1.09 | 1.15 |
| 효과 판정 정확도 | 1.000 | 0.977 | 0.912 |
| 불필요한 개입률 | 0 | 0.021 | 0.021 |

- 장별 평균 절대 오차 (clean / noisy / harsh): CPM 4.3 / 5.5 / 6.9, 장 시간 0.34 / 0.37 / 0.37초, 대본 응시 비율 0.032 / 0.060 / 0.104.
- 격자(`--sweep`): 최소 구간을 6초로 올리면 noisy F1이 0.927 → 0.950, harsh가 0.912 → 0.930으로 오릅니다(정밀도가 오르고 재현율은 거의 그대로 — harsh는 0.952 → 0.936). 병합 간격은 조각남에만 영향을 줍니다. 기본값은 아직 바꾸지 않았습니다.

### 복구 실험 (`reports/results/recovery.json`)

같은 Take를 정상 / 요청 누락 / replay로 돌려 Take 결과를 비교합니다. clean 16 Take, noisy 32 Take(2 seed).
`short_drop`은 60~80초 요청을 보내지 않은 경우(창 30초보다 짧아 다음 요청이 메움), `long_drop`은 60~100초를 보내지 않은 경우(창보다 길어 409 → replay).

| 경우 | 재판정 필요 | 정상 구간 중 일치 (clean / noisy) | 경우 구간 중 정상과 일치 (clean / noisy) | 지표 차이 |
|---|---|---|---|---|
| replay | — | 1.000 / 1.000 | 1.000 / 1.000 | 영역 지표 모두 0 |
| short_drop | 0% | 0.944 / 0.896 | 0.954 / 0.913 | 영역 지표 모두 0 |
| long_drop | 100% | 0.969 / 0.938 | 1.000 / 1.000 | 영역 지표 모두 0 |

한쪽에만 값이 있는 지표(`values_missing`)는 모든 경우 0입니다. 영역 지표(`take`)는 세 경우 모두 정상과 같고, 달라지는 것은 문제 구간 일부입니다.

### 코칭 계획 실험 (`reports/results/coaching_plan.json`)

실제 LLM(`openai/gpt-5.6-luna`)으로 시나리오 5개 × 5번(25 계획): 유효 100%, 기대 통과 96%, 검증에서 뺀 비율 0%, 일관성 0.90, 지연 중앙값 2.6초 · 최대 8.1초.

## 8. 알려진 한계

- **가상 발표자로만 확인했습니다.** 시선 · 속도 · 음량 · 군더더기 판정은 연구용 대역이고, 기준값(대본 응시 5초 · 350 CPM 등)은 실제 연습 데이터로 다시 골라야 합니다.
- 판정 모듈(#152~#155)이 나오면 대역 대신 그 모듈로 `Judges`를 만들어 같은 실험을 다시 돌려야 합니다. 판정 모듈의 `since_ms` · `counted_until_ms` 계약은 코치의 커서와 맞물려 있습니다.
- 개입 규칙이 아직 `Tick`으로 옮긴 지표를 읽습니다(`measure.py`). 판정 결과를 직접 읽도록 줄이는 일은 #150의 개입 규칙 작업에 남아 있습니다.
- `/coach/plan`의 입출력은 Take 결과 · 리뷰 출력과 아직 맞지 않습니다 (#162).
- 요청이 30초보다 길게 빠지면 합계로는 Take 결과를 만들 수 없고 replay가 필요합니다. replay는 Take 전체 원자료를 BE가 보관해야 쓸 수 있습니다.
- 효과 판정은 실시간 지표(짧은 창)로 잽니다. 개입이 잡음으로 튄 순간에 일어나기 쉬워, 시선은 "기준선으로 돌아옴"만 효과로 인정합니다.
- `GAZE_ON_SCREEN` · `GAZE_UNMEASURABLE` · `PACE_SLOW`는 정상 행동과 가릴 근거가 없어 말하지 않고 문제 구간에만 남깁니다.
