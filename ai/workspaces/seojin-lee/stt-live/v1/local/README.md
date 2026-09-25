# v1 / local — 개발·실험 리그

stt-live **v1 모델을 노트북에서 돌리고 확인하는 리그**입니다.
STT로 얻은 단어별 타임스탬프 예시로 CPM(분당 발화 글자 수)을 계산하고 속도를 판정합니다.

v1의 범위와 출력 계약은 [../README.md](../README.md)를 보세요.

> 이 문서는 **작업용 README**입니다 — 개발할 때 필요한 것만 담았습니다.
> 계산 방식, 출력 계약, 알려진 한계는 **[../README.md](../README.md)** 에 있습니다.

> ⚠️ **모든 명령은 이 디렉터리(`v1/local/`)에서 실행하세요.**

---

## 한눈에 보기

```
SLOW_THRESHOLD / FAST_THRESHOLD (상수) ──▶ Word, SpeedResult (Pydantic 스키마)
                                                    │
예시 Word 리스트(손으로 작성) ──▶ words[a:b] 슬라이싱 ──▶ 글자수 합 · 발화시간 합
                                                    │
                                                    ▼
                                    cpm = 글자수 / 발화시간 * 60
                                                    │
                                                    ▼
                                       slow / normal / fast 판정(print)
```

| 항목 | 값 |
|---|---|
| 소스 | `stt_live_speed.ipynb` (노트북 하나) |
| 테스트 데이터 | 노트북 셀에 직접 적힌 예시 `Word` 시퀀스 1개 (약 33개 단어, 21초 분량) |
| 필요한 패키지 | `pydantic`, `jupyter` |
| 외부 의존성 | **없음** — LLM 호출도, `.env`도 필요 없습니다 (script-parser/evaluation-criteria와 다른 점) |

---

## 셋업

```bash
# 가상환경 (v1/local 에서)
python -m venv .venv
./.venv/Scripts/python.exe -m pip install pydantic jupyter
```

이 프로젝트는 STT 결과(단어 타임스탬프)를 순수 계산만 하므로 `OPENAI_*` 환경변수나 `.env`가
필요 없습니다. 노트북을 열자마자 바로 실행할 수 있습니다.

---

## 자주 쓰는 명령

```bash
PY=./.venv/Scripts/python.exe

# 노트북 실행 (Jupyter)
$PY -m jupyter notebook stt_live_speed.ipynb

# 또는 CLI에서 전체 셀 실행
$PY -m jupyter nbconvert --to notebook --execute stt_live_speed.ipynb
```

노트북 셀 순서 그대로 따라가면 됩니다:

1. **환경 설정** — `SLOW_THRESHOLD`, `FAST_THRESHOLD` 상수 정의
2. **구조체 정의** — `Word`, `SpeedResult` Pydantic 모델
3. **예시 입력** — `Word` 리스트 작성 (구간별 글자 수를 주석으로 표시)
4. **산정할 구간 설정** — `words_15s = words[0:29]`로 판정 구간 슬라이싱
5. **글자 수 계산** → **발화 구간 계산** → **CPM 계산** → **말하기 속도 판정**

---

## 모듈 지도

**"무엇이 어디 있나"** 기준입니다. 별도 소스 패키지 없이 노트북 하나가 전부입니다.

| 위치 | 무엇을 소유하는가 |
|---|---|
| `stt_live_speed.ipynb` | 임계값 상수, 스키마 정의, 예시 데이터, CPM 계산, 판정 로직 전부 |
| `../README.md` | v1 전체 기술 레퍼런스 (출력 계약, 계산 방식 상세) |

---

## 반드시 알아야 할 함정

| 함정 | 내용 |
|---|---|
| CPM 분모는 `speak_duration` | `total_duration`(구간 전체 시간, 침묵 포함)이 아니라 단어별 발화시간 합인 `speak_duration`(침묵 제외)을 분모로 씁니다. 잘못 바꾸면 값이 크게 달라집니다 (예시 기준 `total_duration`이면 360, `speak_duration`이면 428.57) |
| 구간 슬라이싱은 수동 | `words_15s = words[0:29]`는 손으로 고른 인덱스입니다. 예시 `Word` 리스트를 수정하면 인덱스도 같이 맞춰야 하며, 자동으로 15초를 찾아주지 않습니다 |
| `SpeedResult` 미사용 | 노트북은 판정 결과를 `print()`만 하고 `SpeedResult(...)` 인스턴스를 생성하지 않습니다. 실제 반환값이 필요하면 마지막 셀을 `SpeedResult(speed=cpm, level=...)` 형태로 바꿔야 합니다 |
| 임계값은 최상단 상수 | `SLOW_THRESHOLD=275`, `FAST_THRESHOLD=350`은 노트북 최상단에 고정돼 있습니다. 실험할 때는 이 값을 바꿔가며 판정이 어떻게 달라지는지 확인하세요 |

---

## 현재 상태

| | |
|---|---|
| ✅ | 예시 단어 시퀀스 1건에 대해 CPM 계산·판정 로직 동작 확인 |
| ⚠️ | **정답 라벨이 있는 평가셋 없음** — 판정 정확도는 사람이 눈으로 확인한 것이 전부 |
| ⚠️ | CI 없음 · 자동화된 테스트 없음 |
| ⬜ | 전송 계층 없음 · `SpeedResult` 미생성 |

가장 큰 공백은 **정량 평가셋**입니다. 그전까지 모든 판정 정확도 수치는 미측정입니다.

자세한 기술 문서 · 출력 계약 · 알려진 한계 → **[../README.md](../README.md)**
