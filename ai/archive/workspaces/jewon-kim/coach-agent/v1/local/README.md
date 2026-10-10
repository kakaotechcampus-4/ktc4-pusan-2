# v1 / local — 개발 · 재생 리그

coach-agent **v1 코치를 네트워크 없이 바로 돌려 보는 리그**입니다.
코치 코어(`ai/src/coach/`)를 함수로 부르고, 가상 발표 시나리오를 1초씩 흘려보내 판단을 확인하고,
리뷰 에이전트에 넘길 근거를 **정답과 비교해 채점**합니다(`tools/evaluate.py`).

v1 의 범위, 입출력 계약, 판단 규칙은 [../README.md](../README.md) 를 보세요.

> 이 문서는 **작업용 README** 입니다 — 돌리고 고치는 데 필요한 것만 담았습니다.

> ⚠️ **모든 명령은 이 디렉터리(`v1/local/`)에서 실행하세요.**

---

## 한눈에 보기

```
 scenarios/*.json ──▶ tools/simulator.py ──▶ coach.decide() ──▶ coach.finalize() ──▶ coach.build_review_evidence()
 (가상 발표자)        (BE 흉내: 15초 STT 창,     (1초마다)         (Take 종료)           (리뷰 에이전트 근거)
                      coach_state 왕복,
                      코치 말에 반응)
                                                                   tools/replay.py ──▶ 콘솔 요약 + reports/<시나리오>.json

 시나리오 × 잡음(clean · noisy · harsh) × seed ──▶ 위와 같이 재생 ──▶ 리뷰 근거
                                                  └▶ tools/truth.py: 발표자의 실제 상태 × 같은 판정 규칙 = 정답 리뷰
                                                     tools/evaluate.py: 둘을 비교해 채점, 규칙 변형 비교 ──▶ reports/evaluation.json
```

| 항목 | 값 |
|---|---|
| 코어 의존성 | pydantic 하나 (시계 · 파일 · 네트워크 없음) |
| 판단 1회 | p50 약 0.5ms, p95 1.3ms 이하 (재생 16개) |
| coach_state | 최대 약 14KB |
| 테스트 | 약 5초 (실험 하한선 포함). 개수는 [../README.md §9-1](../README.md#9-1-상태) |
| 재생 시나리오 | 16개 |
| 실험 | 176 Take (시나리오 16 × clean 1 + noisy · harsh 각 seed 5), 약 16초 |

---

## 셋업

```bash
# 0) 가상환경
python -m venv .venv

# 1) 의존성 (pydantic, pytest)
./.venv/Scripts/python.exe -m pip install -r requirements.txt

# 2) 패키지 (editable, src-layout) — import 이름은 coach
./.venv/Scripts/python.exe -m pip install -e .

# 3) 확인
./.venv/Scripts/python.exe -m pytest -q
```

Python 3.11 이상이 필요합니다 (`enum.StrEnum`). Docker 가이드의 3.12 에서도 그대로 돕니다.

---

## 자주 쓰는 명령

```bash
# ── 테스트 ─────────────────────────────────────────────────────────────
python -m pytest -q                       # 전부 (pyproject 가 ai/src 를 경로에 넣는다)
python -m pytest tests/test_policy.py -q  # 하나만

# ── 재생 (pip install -e . 를 안 했으면 PYTHONPATH=ai/src 를 앞에) ─────
python -m tools.replay                                   # scenarios/*.json 전부
python -m tools.replay scenarios/05_time_behind.json -v  # 하나, WAIT 아닌 판단까지 전부
python -m tools.replay --config my_override.json         # 설정을 바꿔 다시 재생

# ── 리뷰 근거 실험 ────────────────────────────────────────────────────
python -m tools.evaluate                    # clean 1회 + noisy · harsh 각 seed 5, 변형 A~D 비교
python -m tools.evaluate --seeds 3 --explain D   # 변형 D 의 틀린 사례를 하나씩 출력
python -m tools.evaluate --sweep            # 병합 간격 × 지연 보정, 최소 구간 길이 격자 (몇 분)
python -m tools.evaluate --noise noisy scenarios/14_recurring_persists.json

# ── 린트 (backend 와 같은 규칙) ───────────────────────────────────────
uvx ruff check ai tools tests
uvx ruff format ai tools tests
```

재생 출력 예:

```
━━ 03_gaze_gave_up  [PASS]
       33초 장2  LOOK_AT_CAMERA  대본보다 청중을 조금 더 바라보세요
                 └ GAZE_SCRIPT, WORSENING
       53초 장2  LOOK_AT_CAMERA  문장을 시작할 때만이라도 고개를 들어 청중을 보세요
                 └ GAZE_SCRIPT, ESCALATED, PERSISTENT, WORSENING
       41초 전략  ESCALATED GAZE_SCRIPT (장2) LOOK_AT_CAMERA.default → LOOK_AT_CAMERA.sentence_start
     1분 1초 전략  GAVE_UP GAZE_SCRIPT (장2) LOOK_AT_CAMERA.sentence_start
   ── 개입 2 (격려 0) · 효과 0/2 (0%) · 포기 1 · 문제 구간 1 (미대응 0)
   ── 172틱 · decide p50 0.238ms / p95 0.361ms · coach_state 9178B
```

`reports/` 는 git 에 올리지 않아 클론 직후에는 비어 있습니다. 먼저 `python -m tools.replay` 로 만드세요.
`reports/<시나리오>.json` 에 이벤트 전체와 리뷰 근거(`review_evidence`)가 남습니다. 리뷰 에이전트 입력이 어떻게 생겼는지 볼 때 여기를 보세요.

### 실험 출력 읽기

```
━━ 잡음: noisy  (80 Take)
   지표                         A 기준선(v1.0)       B +신뢰도 필터        C +구간 병합        D +지연 보정
   구간 정밀도 ↑                         0.623           0.930           0.919           0.984
   근거 없는 지적/Take ↓                  0.500           0.050           0.050           0.013
   구간 IoU ↑                         0.575           0.575           0.594           0.832
   ...
━━ 틀린 사례 — D +지연 보정 · noisy          (--explain D)
   1순위 07_exam_mode#noisy/1: 코치 SPEED · 정답 GAZE
   놓친 구간 sim-15_mixed_burdens: GAZE 장3 100~110s
```

| 지표 | 뜻 |
|---|---|
| 구간 재현율 · 정밀도 · F1 | 센서가 볼 수 있던 실제 문제 구간을 리뷰 근거가 문제로 말했나 / 리뷰가 말한 구간이 실제 문제였나 |
| 근거 없는 지적 / Take | 실제 문제가 아니었거나 센서를 믿을 수 없던 곳을 문제로 말한 수 |
| 구간 IoU · 시작 오차 · 조각남 | 맞춘 구간의 시간 겹침, "몇 분 몇 초부터"의 오차, 실제 문제 하나를 몇 조각으로 말했나 |
| 미션 · 영역 상태 · 이전 Take 비교 정확도 | 정답 판정과 같은 비율 |
| 1순위 영역 · 다음 미션 일치 | 가장 먼저 고칠 영역 · 첫 다음 미션이 정답과 같은 비율 |
| 거짓 강점 / Take | 실제 문제가 있던 영역을 '문제 없음'이라고 한 수 |
| 효과 판정 정확도 · 불필요한 개입률 | 실시간 지표 — 개입 효과 판정이 발표자의 실제 변화와 같은 비율, 최근 20초 안에 실제 문제가 없던 개입 비율 |

리뷰 설정(`config.review`)은 실시간 판단에 쓰이지 않으므로, 실험은 발표를 한 번만 재생하고 변형마다 리뷰 근거만 다시 만듭니다.
실시간 설정을 바꾼 효과를 보려면 시나리오의 `config` 나 `--config` 로 바꾼 뒤 다시 돌리세요.

### 파이썬에서 직접

```python
from coach import decide, finalize, build_review_evidence

state, events = None, []
for t in range(0, 180_001, 1000):
    resp = decide({"take_id": "t1", "t_ms": t, "plan": {...}, "current": {...}, "coach_state": state})
    state = resp.coach_state            # BE 가 할 일: 그대로 보관
    events += resp.events               # BE 가 할 일: 그대로 쌓기
    if resp.feedback:
        print(resp.feedback.message)    # BE 가 할 일: FE 로 전달
events += finalize({"take_id": "t1", "t_ms": 180_000, "coach_state": state}).events
evidence = build_review_evidence("t1", events)  # → 리뷰 에이전트
```

---

## 모듈 지도

### `ai/src/coach/` — 배포되는 코어 (그대로 `ai/releases/` 로 옮긴다)

| 파일 | 단계 | 하는 일 |
|---|---|---|
| `engine.py` | 진입점 | `decide()` · `decide_safe()` · `finalize()`. 아래 단계를 순서대로 부른다 |
| `schemas.py` | 계약 | 요청 · 응답 · 이벤트 · 리뷰 근거 (pydantic). **단일 진실 원천** |
| `vocab.py` | 계약 | enum — type · instruction · action · issue · 이유 코드 |
| `config.py` | 설정 | 기준값 · 가중치 · 문구 템플릿 · 전략 사다리. **숫자는 전부 여기** |
| `state.py` | 기억 | `coach_state` 모양, 읽기 · 쓰기 · 버전 |
| `evaluators/` | ① ② | `speech.ingest` 누적 → `gaze` · `voice` · `speech` · `timing` 평가 (순서 중요: timing 이 speech 의 cpm 을 쓴다) |
| `reflection.py` | 되돌아보기 | 효과 예약 · 판정 · 사다리 이동 · 격려 후보 |
| `episodes.py` | 문제 구간 | 열기 · 이어가기 · 닫기, 믿을 수 있던 시간 · 평균 심각도 → `EPISODE` 이벤트 |
| `slides.py` | 장별 누적 | 한 장에 머무는 동안 시선 · CPM · dB · 군더더기 · 데이터 덮개 → 장을 떠날 때 `SLIDE` 이벤트 |
| `candidates.py` | ③ | 문제 → 후보 (사다리 칸 선택) |
| `eligibility.py` | ④ | 하드 규칙 → WAITING / IGNORED |
| `priority.py` | ⑤ | 점수 · 가중치 이유 |
| `policy.py` | ⑥ | `RulePolicy` — WAIT / IGNORE / INTERVENE, 문장 끝 기다리기. v2 LLM 선택기가 끼울 자리 |
| `renderer.py` | ⑦ | 템플릿 → 문장 |
| `events.py` | ⑧ | 이벤트 번호 매기기 |
| `review.py` | 리뷰 연동 | 이벤트 → 장별 누적 · 문제 구간(측정 정리) → `assess()`(판정: 순위 · 미션 · 기억 비교 · 영역 상태 · 다음 미션) → `CoachReviewEvidence` |

### 그 외 (배포되지 않음)

| 경로 | 하는 일 |
|---|---|
| `tools/simulator.py` | 가상 발표자 + BE 흉내 + 잡음(`NOISE_PRESETS`) + 정답 기록 + `check_expect` |
| `tools/truth.py` | 정답 — 정답 구간 · 정답 장별 누적 · 정답 효과, 그리고 `assess()` 로 만든 정답 리뷰 |
| `tools/evaluate.py` | 리뷰 근거 실험 CLI — 채점, 변형 비교, 격자, 틀린 사례 |
| `tools/replay.py` | 시나리오 재생 CLI |
| `scenarios/*.json` | 재생 시나리오 16개 |
| `tests/` | pytest. `conftest.py` 의 `make_request` · `Session` 이 요청과 coach_state 왕복을 줄여 준다 |
| `reports/` | 재생 결과 (git 에 올리지 않음) |

---

## 시나리오

시나리오 하나 = 가상 발표 하나 + 기대 결과. 모양은 `tools/simulator.py` 의 `Scenario` 가 원천입니다.

| 필드 | 뜻 |
|---|---|
| `plan`, `missions`, `memory`, `mode` | 코치 요청에 그대로 들어간다 |
| `baseline` | 발표자의 기본 상태 — `cpm`, `script_ratio`(보인 시간 중), `uncertain_ratio`, `relative_db`, `filler_per_min`, `speaking`, `stt_status`, `audio_live`, `completion` |
| `segments[]` | `from_ms` ~ `to_ms` 동안 상태를 바꾼다 |
| `reactions{instruction: …}` | 코치가 그 말을 하면 `delay_ms` 뒤 `duration_ms` 동안 상태를 바꾼다 (`set`), 다음 장으로 넘어간다 (`advance_slide`), 말을 한다 (`say`) |
| `config` | 코치 설정 덮어쓰기 (예: 쿨다운을 줄여 3분 안에 사다리를 다 보기) |
| `noise` · `seed` | 재생할 때의 잡음(기본 없음)과 seed. 실험은 이 값을 덮어써 잡음 3단계로 돌린다 |
| `expect` | 실시간: `interventions{min,max}`, `types{TYPE:{min,max}}`, `instructions_include` · `_exclude`, `strategy_changes_include`, `outcomes_include`, `suppressed_reasons_include` · 리뷰 근거: `segment_hints_include`, `type_status{TYPE:상태}`, `mission_status{id:상태}`, `memory_labels[]`, `top_issue{type,slide_number}`, `next_mission_types_include`, `no_claims[]`(문제로 말하면 안 되는 영역), `strengths_include[]` |

가상 발표자는 3글자 단어를 `cpm` 속도로 말하고, 6단어마다 0.6초 쉬고(문장 끝 신호), 대본 글자 수 × `completion` 만큼 말하면 다음 장으로 넘어갑니다.
확정 결과는 말한 뒤 1.2초에 옵니다. 오디오가 멈추면(`audio_live: false`) 계속 말하지만 STT 에 전달되지 않고 FE 침묵은 계속 늘어납니다.
시선은 FE 처럼 최근 10초의 1초 라벨 비율이고, 침묵은 소리 기준(단어를 말하는 도중이면 0)입니다.

| 시나리오 | 확인하는 것 |
|---|---|
| `01_baseline_good` | 문제없는 발표자에게 한마디도 하지 않는다 |
| `02_gaze_effective` | 시선 지적 → 고개를 듦 → 효과 있음 → 유지 격려 |
| `03_gaze_gave_up` | 안 바뀜 → 다른 방법 → 그 장에서 포기 → 리뷰에 `GAVE_UP` |
| `04_gaze_sensor_unusable` | 얼굴이 자주 안 잡힐 때 시선 지적 없음 |
| `05_time_behind` | 늦어지면 `SPEED_UP`, '천천히'는 나오지 않음 |
| `06_pace_fast` | 빨라지면 `SLOW_DOWN` → 효과 있음 |
| `07_exam_mode` | 실전 모드 — 말하지 않고 문제 구간만 리뷰로 |
| `08_filler_frequent` | 군더더기 지적 → 줄어듦 |
| `09_long_silence` | 5초 넘게 멈추면 `RESUME` |
| `10_audio_dead` | 오디오가 멈춰도 침묵 · 늦음 오탐 없음 |
| `11_keyword_missing` | (켜면) 필수 키워드 안내 → 말함 |
| `12_no_slide_plan` | 장별 계획이 없으면 남은 시간만으로 마지막 1분 · 시간 초과 |
| `13_recurring_resolved` | 이전 Take 의 시선 문제가 사라짐 → 기억 `RESOLVED`, 미션 `ACHIEVED`, 시선 `IMPROVED`, 강점 |
| `14_recurring_persists` | 이전 Take 의 시선 문제가 그대로 → `RECURRING`, 미션 `FAILED`, 1순위 · 다음 미션이 그 장 시선 |
| `15_mixed_burdens` | 문제가 여럿일 때 1순위는 가장 크고 오래간 것(속도) |
| `16_noisy_sensors` | 센서가 나쁜 동안의 시선 · 속도는 문제로 말하지 않고, 멀쩡한 오디오의 작은 목소리만 |

새 상황을 확인하고 싶으면 시나리오 JSON 을 하나 더 만들면 됩니다 — `tests/test_replay.py` 가 `scenarios/*.json` 을 전부 돌립니다.

---

## 설정 바꾸기

기본값은 `config.py` 에만 있습니다. 코드를 고치지 말고 **바꾸고 싶은 값만** JSON 으로 덮어쓰세요.

```json
{"policy": {"cooldown_ms": 45000}, "gaze": {"script_ratio": 0.75}, "features": {"keyword_missing": true}}
```

```bash
python -m tools.replay --config my_override.json
```

응답의 `config_hash` 가 바뀌므로 어떤 설정으로 판단했는지 추적됩니다.

---

## 반드시 알아야 할 함정

### 코어에서 시계를 쓰지 마세요

`time.time()` · `datetime.now()` 를 부르면 같은 요청에 다른 응답이 나와 재생 결과와 배포 결과가 달라집니다.
시간은 요청의 `t_ms` 뿐입니다.

### `speech` 가 없으면 STT 가 없는 것입니다

요청에 `current.speech` 가 없으면 말 속도 판단을 끄고, 장 진행도를 **시간**으로 잽니다.
테스트에서 진행도를 글자 수로 재게 하려면 `speech={"words": []}` 라도 넣어야 합니다.
오디오가 멈춘 동안(`audio_live: false`)도 마찬가지이고, 그 장은 끝까지 시간으로 잽니다.

### coach_state 는 그대로 왕복시키세요

모양을 바꾸면 `state.py` 의 `STATE_VERSION` 을 올리세요. 다른 버전이 오면 새로 시작합니다(`STATE_RESET`).
null 필드는 보내지 않지만(`exclude_none`) 기본값 필드는 보냅니다 — 기본값인 `v` 까지 빠지면 버전 확인이 깨집니다.

### instruction 이나 사다리 칸을 더하면 문구도 더하세요

모든 사다리 칸(`INSTRUCTION.variant`)에 템플릿이 있어야 합니다. `test_every_ladder_step_has_a_template` 가 막아 줍니다.
template 의 `{이름}` 은 평가기가 `params` 로 넘겨야 하고, `*_time_ms` 는 "1분 5초" 로 바뀌어 `{*_time}` 에 들어갑니다.

### 시간 규칙이 다른 테스트를 방해할 수 있습니다

`make_request` 는 장 체류 시간을 안 주면 Take 시작부터 그 장이었던 것으로 봅니다. 시선만 보려는 긴 테스트는
`plan={}` 로 시간 계획을 빼야 '시간 여유' · '다음 장으로' 같은 시간 후보가 끼지 않습니다.

### Windows 콘솔의 한글

`tools.replay` 는 stdout 을 UTF-8 로 바꿉니다. 직접 쓴 스크립트에서 한글 · 기호가 깨지면 `PYTHONIOENCODING=utf-8` 을 붙이세요.

### 리뷰 설정과 실시간 설정은 실험 방식이 다릅니다

`config.review` 는 Take 종료 뒤에만 쓰여 `tools/evaluate.py` 가 같은 재생 결과로 변형을 비교합니다.
그 밖의 설정(평가기 · 판단 · 되돌아보기)을 바꾸면 발표가 달라지므로 다시 재생해야 합니다.
실험 하한선(`tests/test_experiment.py`)은 noisy seed 2개로 돕니다 — 규칙을 고쳐 리뷰 근거가 나빠지면 여기서 걸립니다.

### 문서 · 주석의 줄 길이

ruff 는 한글을 2칸으로 셉니다. 한글 주석이 100자 안이어도 E501 이 날 수 있습니다.

### 기준값은 잠정입니다

70% · 350 CPM · −6dB · 5초 · 15초 · 60초는 실제 발표로 검증하지 않았습니다. 시나리오가 통과한다는 것은
"로직이 의도대로 돈다"는 뜻이지 "기준값이 맞다"는 뜻이 아닙니다. 실험의 정답도 시뮬레이터가 아는 상태라,
잡음 모델이 실제 FE · Deepgram 오차와 다르면 결과도 달라집니다.
