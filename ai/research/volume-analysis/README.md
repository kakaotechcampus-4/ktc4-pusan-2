# volume-analysis (소리 크기 · 침묵)

발표 중 1초마다 **평소 목소리보다 작게 말하는지(`VOLUME`)**와 **말이 오래 끊겼는지(`PAUSE`)**를 따로 판정하는 모듈입니다.
절대 음량(dBFS)이 아니라 같은 사람의 평소 목소리(기준 음량)와의 차이로 봅니다. 마이크 · 방마다 숫자가 달라서입니다.
기준 음량이 없으면 Take 첫 발화로 잡는 함수(`speaking_levels` · `baseline`)도 이 모듈이 갖습니다.

| | |
|---|---|
| 판단 방식 | 규칙. LLM · 네트워크를 쓰지 않는다 |
| 부르는 쪽 | 코치(`/coach/evaluate` · `/coach/finalize` · `replay`). 자기 HTTP 엔드포인트는 없다 |
| 기능 버전 | `volume-1.0` (`src/volume/version.py`), `criteria_version` = `volume-1.0+<config 해시 12자리>` |
| 상태 | 진입 함수 5개 + 테스트. **기준값은 옛 평가기 값이고 실제 녹음으로 검증하지 않았다** |
| 설계 | 목적 · 입출력 계약 · 처리 · 배포 → [`docs/ai/features/volume.md`](../../../docs/ai/features/volume.md) |

## 1. 진입 함수

| 함수 | 하는 일 | 언제 |
|---|---|---|
| `judge(inputs, t_ms, config)` | 최근 30초 음량 1초 기록 → `VOLUME` · `PAUSE` 판정 결과 두 개(이 순서) | 요청마다 |
| `speaking_levels(records, config)` | 새 1초 기록 → 그중 말한 1초의 `level_db` (기준 음량 표본) | 기준 음량이 없는 동안 요청마다 |
| `baseline(levels, config)` | 표본 → 기준 음량(앞 `baseline_samples`개의 중앙값). 모자라면 `None` | 기준 음량이 없는 동안 요청마다 |
| `summarize(tally, config)` | 장 · Take의 `tally` 합계 → `VOLUME` · `PAUSE` 지표 | 장 끝 · Take 끝 |
| `criteria(config)` | `VOLUME_LOW` · `LONG_SILENCE`의 판정 기준 | Take 끝 |

```
FE (마이크 → 음량 1초 기록) → BE (30초 창) → 코치
                                              ├─ speaking_levels → baseline   기준 음량이 없을 때
                                              ├─ judge                        VOLUME · PAUSE, tally
                                              └─ summarize · criteria         Take 결과
```

모듈은 상태를 갖지 않습니다. 기준 음량 표본 · 값과 `tally` 합계는 코치가 `coach_state`에 들고 다닙니다.

## 2. 폴더와 파일

```
volume-analysis/
├─ README.md
├─ pyproject.toml · uv.lock    Python 3.12, 의존성은 pydantic 하나
├─ src/volume/                 판정 모듈 → 서비스 features/volume/로 폴더째 옮긴다
│   ├─ schemas.py              입력 · 출력 pydantic 모델 = 계약
│   ├─ core.py                 순수 함수 (judge · summarize · criteria · speaking_levels · baseline)
│   ├─ config.py               기준값. 해시가 criteria_version 에 들어간다
│   ├─ version.py              기능 버전
│   └─ examples/               judge(판정 창 전체) · summarize · criteria · speaking_levels · baseline 입력 · 출력 예시
├─ data/                       평가 데이터 (아직 없음)
├─ tools/                      녹음 → 1초 기록 · 평가 스크립트 (아직 없음)
├─ tests/                      판정 규칙 · 기준 음량 · summarize · 예시 계약
└─ reports/results/            평가 결과 JSON (아직 없음)
```

코어 규칙: 파일 · DB · 네트워크 · 환경변수 · 시계를 쓰지 않고(시간은 입력의 `t_ms`뿐), 모듈 안은 상대 import만 씁니다.
같은 입력에는 같은 결과가 나옵니다.

## 3. 실행

```bash
cd ai/research/volume-analysis
uv sync                                  # 처음 한 번 (.venv 는 git 에 없다)
uv run python -m pytest                  # 테스트
uv run ruff check . && uv run ruff format --check .
```

```python
from volume.core import judge, summarize, criteria, speaking_levels, baseline

volume, pause = judge(
    {"records": [...], "base_level_db": -29.5, "since_ms": 40000},  # records: 최근 30초 1초 기록
    t_ms=41000,
)
volume.state, volume.metrics.voice_diff_db, pause.metrics.silence_ms
```

- `judge`의 결과는 pydantic 모델입니다. 코치에 dict로 넘길 때는 `.model_dump(mode="json")`.
- `summarize` · `criteria`의 결과는 dict입니다(architecture.md 7절). 안에서 `Summary` · `Criteria` 모델로 검증한 뒤 dict로 돌려줍니다.
- `config.py`의 값을 바꾸면 `criteria_version`이 바뀌므로 `examples/`의 출력도 다시 만들어야 계약 테스트가 통과합니다.

## 4. 테스트

| 파일 | 보는 것 |
|---|---|
| `tests/test_judge.py` | 작음 · 기준과 같을 때, 기준 음량 없음 · Take 안에서 생김, 오디오 멈춤 · 말한 1초 부족, 에너지 Leq(dB 평균이 아님), 침묵 자르기 · 창 안에 끊김이 없을 때, 긴 침묵 한 번만, 같은 1초 두 번, 마지막 조각(시간 · 에너지 · 커서), 커서 · `tally`, 끊긴 1초는 `tally`에 시간만 |
| `tests/test_baseline.py` | 말한 1초 고르기, `baseline` 표본 부족 · 충분 · 튀는 값 |
| `tests/test_summarize.py` | Take 지표 계산, 분모가 0일 때, `criteria`가 config를 따르는지, 둘의 결과가 안쪽까지 dict인지 |
| `tests/test_examples.py` | `examples/`가 구현 · 계약 모델과 같은지 |

## 5. 실험 결과

아직 없습니다. 평가 데이터(같은 사람이 평소 · 작게 · 크게 읽은 녹음, 마이크 여러 종)와
`tools/evaluate.py`가 생기면 `VOLUME_LOW` 정밀도 · 재현율, 침묵 감지 지연, 기준 음량 오차를
`reports/results/`에 남깁니다(`volume.md` 13절).

## 6. 연구용 대역(`volume-0.2`)과 달라진 점

코치 연구 폴더의 대역 `coach_lab/judges/volume.py`와 출력 모양은 같고 규칙이 다릅니다.

| | 대역 `volume-0.2` | 이 모듈 `volume-1.0` |
|---|---|---|
| 짧은 창의 음량 | dB 값 평균 | 에너지 평균(Leq), `voiced_ms`로 가중 |
| 말한 1초 | `silence_ms` < 300 · 오디오 흐름 · `level_db` 있음 | `voiced_ms` ≥ `voiced_min_ms` · `audio_live` (시선과 같은 기준) |
| 침묵 자르기 시작점 | 다시 흐른 첫 1초의 끝 | 다시 흐른 첫 1초의 시작 |
| `VOLUME_LOW`의 `persistence_sec` | 늘 0 | 마지막 1초부터 거꾸로 작음이 이어진 시간 |
| 기준 음량 표본 고르기 | 없음 (`baseline`만, 표본은 코치가 고름) | `speaking_levels`가 말한 1초의 `level_db`를 고름. 코치가 판정 기준값을 따로 두지 않는다 |
| 말한 소리가 없는 Take의 `VOLUME.measured_ratio` | null | 0 (코치가 측정 비율 기준 미만으로 본다) |
| 끊긴 1초(`audio_live: false`)의 `tally` | 발화 · 에너지도 더함 | `total_ms`만 (말한 1초 · 기준 음량 표본과 같은 기준) |

기준값(작음 −6 dB · 아주 작음 −15 dB · 평활 창 5초 · 말한 1초 3개 · 표본 15개 · 긴 침묵 5초 · 아주 긴 침묵 15초 · 판정 지연 폭)은 대역과 같습니다.
`voiced_min_ms`(300)는 대역 `volume-0.2`에 없던 값이라, 시선 대역(`coach_lab/judges/gaze.py`의 `speaking_min_voiced_ms`)과 같게 두었습니다.
