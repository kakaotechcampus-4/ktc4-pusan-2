# v1 / local — 개발·실험 리그

volume-analysis **v1 모델을 노트북에서 돌리고 확인하는 리그**입니다.
예시 캘리브레이션 값과 현재 음량으로 음량 지표를 계산하고 절대/상대 음량을 판정합니다.

v1의 범위와 출력 계약은 [../README.md](../README.md)를 보세요.

> 이 문서는 **작업용 README**입니다 — 개발할 때 필요한 것만 담았습니다.
> 계산 방식, 출력 계약, 알려진 한계는 **[../README.md](../README.md)** 에 있습니다.

> ⚠️ **모든 명령은 이 디렉터리(`v1/local/`)에서 실행하세요.**

---

## 한눈에 보기

```
Calibration(noise_db, baseline_voice_db) ──▶ 예시 값 (45 dB, 65 dB)
                         │
current_db (예시 58 dB) ─┴──▶ measure_volume() ──▶ VolumeMeasurement
                                                          │
                     ┌────────────────────────────────────┤
                     ▼                                    ▼
      judge_absolute_volume(current_db)     judge_relative_volume(relative_db)
        60 / 75 기준 low·normal·high           10 기준 low·normal

test_values [50..80] ──▶ calculate_volume_features() ──▶ 세 지표 표로 출력(print)
```

| 항목 | 값 |
|---|---|
| 소스 | `volume_analysis.ipynb` (노트북 하나) |
| 테스트 데이터 | 노트북 셀에 직접 적힌 예시 캘리브레이션 1개 + 현재 음량 `58`, `[50, 55, 60, 65, 70, 75, 80]` |
| 필요한 패키지 | `pydantic`, `jupyter` |
| 외부 의존성 | **없음** — LLM 호출도, `.env`도, 오디오 파일도 필요 없습니다 |

---

## 셋업

```bash
# 가상환경 (v1/local 에서)
python -m venv .venv
./.venv/Scripts/python.exe -m pip install pydantic jupyter
```

이 프로젝트는 이미 측정된 dB 값으로 순수 계산만 하므로 `OPENAI_*` 환경변수나 `.env`가
필요 없습니다. 노트북을 열자마자 바로 실행할 수 있습니다.

---

## 자주 쓰는 명령

```bash
PY=./.venv/Scripts/python.exe

# 노트북 실행 (Jupyter)
$PY -m jupyter notebook volume_analysis.ipynb

# 또는 CLI에서 전체 셀 실행
$PY -m jupyter nbconvert --to notebook --execute volume_analysis.ipynb
```

노트북 셀 순서 그대로 따라가면 됩니다:

1. **구조체 정의** — `Calibration`, `VolumeMeasurement` Pydantic 모델
2. **Calibration** — 예시 `noise_db=45.0`, `baseline_voice_db=65.0` 설정
3. **실시간 측정** — `measure_volume(current_db=58.0, ...)`로 지표 계산
4. **절대 음량 판단** — `ABSOLUTE_LOW/HIGH` 정의 후 `judge_absolute_volume()`
5. **상대 음량 판단** — `RELATIVE_LOW` 정의 후 `judge_relative_volume()`
6. **캘리브레이션 대비 변화** — `calculate_volume_features()` (판정에는 아직 미사용)
7. **테스트** — 50~80 dB 7개 값에 대해 세 지표 출력

---

## 모듈 지도

**"무엇이 어디 있나"** 기준입니다. 별도 소스 패키지 없이 노트북 하나가 전부입니다.

| 위치 | 무엇을 소유하는가 |
|---|---|
| `volume_analysis.ipynb` | 스키마 정의, 예시 캘리브레이션, 지표 계산, 임계값 상수, 판정 함수, 테스트 전부 |
| `../README.md` | v1 전체 기술 레퍼런스 (출력 계약, 계산 방식 상세) |

---

## 반드시 알아야 할 함정

| 함정 | 내용 |
|---|---|
| 지표 계산 함수가 두 개 | `measure_volume()`(`VolumeMeasurement` 반환)과 `calculate_volume_features()`(`dict` 반환)가 같은 식을 따로 계산합니다. 계산식을 바꿀 때는 두 함수를 함께 수정하거나 하나로 합치세요 |
| 두 판정이 다를 수 있음 | 같은 입력에서 절대 판정과 상대 판정이 다르게 나옵니다 (예시 58 dB → 절대 `low`, 상대 `normal`). 버그가 아니라 아직 결합 규칙이 없기 때문입니다 |
| 임계값은 셀마다 흩어져 있음 | `ABSOLUTE_LOW/HIGH`는 절대 음량 셀에, `RELATIVE_LOW`는 상대 음량 셀에 정의돼 있습니다. 값을 바꾼 뒤에는 해당 셀과 그 아래 판정 셀을 다시 실행하세요 |
| dB 스케일이 미정 | 예시 값은 dB SPL처럼 보이는 숫자지만, FE가 실제로 보내는 dB의 기준은 아직 정해지지 않았습니다. 실제 측정값을 넣을 때는 절대 임계값(60/75)이 맞는 스케일인지 먼저 확인하세요 |

---

## 현재 상태

| | |
|---|---|
| ✅ | 예시 캘리브레이션·음량 값에 대해 지표 계산·절대/상대 판정 동작 확인 |
| ⚠️ | **정답 라벨이 있는 평가셋 없음** — 임계값은 임시값, 판정 정확도는 미측정 |
| ⚠️ | CI 없음 · 자동화된 테스트 없음 |
| ⬜ | FE와 dB 수치 논의 · 최종 판단 로직 · 판정 결과 스키마 · 전송 계층 없음 |

가장 큰 공백은 **FE와의 dB 수치 합의**입니다. 노트북 마지막 메모대로, 이 논의 후에 추가 개발할 예정입니다.

자세한 기술 문서 · 출력 계약 · 알려진 한계 → **[../README.md](../README.md)**
