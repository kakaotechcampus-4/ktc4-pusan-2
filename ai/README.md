# ai

**Pitch Coach(피치코치)** 의 AI 파트입니다. 발표 연습 영상 · 음성 · 대본을 읽어 코칭 신호로 바꿉니다.

담당: `jewon-kim`, `seojin-lee`

---

## 하는 일

| 시점 | 하는 일 | 기능 |
|---|---|---|
| 대본 등록 | 대본을 슬라이드로 나누고, 키워드와 평가 기준을 만든다 | 대본 파싱, 평가 기준 분류, 대본 전달도 |
| 연습 중 | 시선 · 소리 크기 · 말 속도를 보고 실시간으로 코칭한다 | 시선, 소리 크기, 말 속도, 실시간 코치 |
| 연습 후 | 대본을 얼마나 전달했는지 채점하고, 리뷰 근거를 만든다 | 대본 전달도, 실시간 코치 |

## 기능

| 기능 | 하는 일 | 상태 | 위치 | 담당 |
|---|---|---|---|---|
| 시선 | 웹캠으로 발표자가 카메라 · 화면 · 대본 · 기타 중 어디를 보는지 브라우저에서 판정하고, 1초 기록으로 코치 이슈 · 테이크 요약을 만든다 | v1.1 · 브라우저 엔진 + 서버 코어 + 테스트 | [`research/gaze-tracking/`](research/gaze-tracking/README.md) | jewon-kim |
| 실시간 코치 | 1초마다 말을 걸지, 무엇을 말할지 정하고 연습 뒤 리뷰 근거를 만든다 | v1 · 패키지 + 테스트 | [`research/coach-agent/`](research/coach-agent/README.md) | jewon-kim |
| 대본 전달도 | 대본으로 슬라이드별 평가 기준을 만들고, STT가 그 내용을 얼마나 전달했는지 채점 | v1 · 패키지 + 테스트 | [`research/script-coverage-evaluation/`](research/script-coverage-evaluation/README.md) | jewon-kim |
| 대본 파싱 | 대본을 슬라이드별로 나누고 키워드를 뽑는다 | v1 · 노트북 | `archive/…/script-parser/` | seojin-lee |
| 평가 기준 분류 | 자유 서술 평가 기준을 판정 가능 · 정보 부족 · 판정 불가로 나눈다 | v1 · 노트북 | `archive/…/evaluation-criteria/` | seojin-lee |
| 말 속도 | STT 단어 타임스탬프로 말 속도를 재서 느림 · 보통 · 빠름으로 판정 | v1 · 노트북 | `archive/…/stt-live/` | seojin-lee |
| 소리 크기 | 캘리브레이션 대비 소리 크기를 작음 · 보통 · 큼으로 판정 | v1 · 노트북 | `archive/…/volume-analysis/` | seojin-lee |

`archive/…/`는 `archive/workspaces/<담당>/`입니다. 실행법과 자세한 설명은 각 폴더의 README에 있습니다.
모두 로컬에서 검증한 단계이고, 실제 발표 데이터로는 아직 검증하지 않았습니다.

## 폴더

```
ai/
├── service/      # 배포되는 AI 서버 (준비 중)
├── research/     # 기능별 코드 · 실험 · 평가
├── archive/      # 구조를 바꾸기 전 코드 (동결)
└── .env.example
```

## 환경변수

LLM을 쓰는 기능(대본 전달도 · 대본 파싱 · 평가 기준 분류)은 `ai/.env`의 키로 실제 API를 부르고, 호출할 때마다 과금됩니다.
`ai/.env.example`을 `ai/.env`로 복사해 채우세요. `ai/.env`는 git에 올리지 않습니다.

## 더 읽을 것

- [research/README.md](research/README.md) — 옮겨 온 기능 목록
- [archive/README.md](archive/README.md) — 동결본 목차와 실행할 때 주의점
