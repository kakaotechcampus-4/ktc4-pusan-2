# ai

**Pitch Coach(피치코치)** 의 AI 파트입니다. 발표 연습 영상 · 음성 · 대본을 읽어 코칭 신호로 바꾸는 코드를 모읍니다.

담당은 두 명입니다: `jewon-kim`, `seojin-lee`.

이 README는 폴더 구조와 모두가 지키는 규칙을 정리합니다. 자세한 배경은 Notion 문서 [피치코치 — AI 레포 구조 및 개발 규칙](https://app.notion.com/p/3f0c2c9a181681f495b1dd8900e7a24d)에 있지만, 일상적인 작업에는 이 문서만으로 충분합니다.

---

## 폴더 구조

```
ai/
├── service/      # 배포되는 AI 서버 하나 (Docker build context)
├── research/     # 기능별 실험. 배포하지 않는다
├── archive/      # 구조 개편 전 workspaces/ 를 그대로 옮겨 둔 동결본
├── .env.example
└── README.md
```

| 폴더 | 역할 |
|---|---|
| `service/` | 배포 대상 AI 서버. 기능 코어는 `src/pitch_coach_ai/features/<x>/`, HTTP 진입점은 `src/pitch_coach_ai/api/v1/`, 계약 파일은 `contracts/` |
| `research/` | 기능별 비교 실험 · 평가 · 보고용 노트북. service를 import해서 평가한다 ([research/README.md](research/README.md)) |
| `archive/` | 개편 전 `workspaces/`. 수정 · import 금지 ([archive/README.md](archive/README.md)) |

**지금은 `service/`와 `research/`가 비어 있습니다.** `service/`에는 `.gitkeep`만 있고, 기능을 `archive/`에서 하나씩 옮길 때마다 채워집니다. 아래 경로에 나오는 `features/<x>/`는 `service/src/pitch_coach_ai/features/<x>/`를 줄여 쓴 것입니다.

---

## 규칙

### 1. 코드는 `.py` 만

노트북은 `research/*/reports/` 아래의 **보고용 노트북**에만 둡니다. 코어를 import해서 결과를 보여 주기만 하고, 로직은 넣지 않습니다.

### 2. import 는 한 방향

`research` → `service` 방향만 허용합니다. service는 research를 import하지 않습니다. 어느 쪽도 `archive/`를 import하지 않습니다.

### 3. 배포 코드는 service 에서 고치고, 평가는 research 에서 돌린다

둘 사이에 **복사 단계가 없습니다.** 코어는 처음부터 `service/`에 있고, research는 그것을 import해서 평가합니다. 실험용 사본과 배포용 사본이 따로 생기지 않습니다.

### 4. 계약의 원본은 코드

- pydantic 스키마가 원본이고, 그로부터 `service/contracts/`가 생성됩니다 (OpenAPI, 이벤트 JSON Schema, 요청 · 응답 예시).
- 생성 파일은 **손으로 고치지 않습니다.**
- 계약을 바꿀 때는 **계약이 먼저**입니다. 스키마와 예시를 먼저 바꾸고, 그것을 쓰는 쪽(FE 또는 BE)이 검토합니다.

### 5. 버전은 두 종류

| | 무엇 | 어디에 |
|---|---|---|
| **서비스 버전** | 서버를 배포할 때 | git 태그 `ai-vX.Y.Z`, Docker 이미지 태그도 같은 값 |
| **기능(모델) 버전** | 기능의 **출력 의미**가 바뀔 때 | `features/<x>/version.py` |

기능 버전은 출력 의미가 바뀔 때만 올립니다. 응답 메타데이터에 실려 나가고, BE가 저장합니다.

### 6. 담당과 비밀

- 담당은 `CODEOWNERS`와 각 폴더 README에 적습니다.
- 비밀값(`.env`), 데이터, 가중치는 git에 올리지 않습니다.

---

## 새 서버 기능을 만드는 순서

1. `features/<x>/schemas.py` — 입출력 **계약**부터 정한다
2. `features/<x>/core.py` + 단위 테스트
3. `research/<x>/` — core를 import해서 평가한다
4. `api/v1/` 에 라우터를 추가하고 계약 예시를 만든다. 이 단계까지 끝나면 배포할 수 있다

core가 처음부터 service 안에 있으므로, 실험이 끝난 뒤 배포용으로 옮기는 단계가 따로 없습니다.

---

## 이전 현황

`archive/workspaces/` 안의 경로 기준입니다.

| 기능 | 담당 | 현재 위치 | 이전 목적지 | 상태 |
|---|---|---|---|---|
| gaze-tracking (시선: 카메라 · 화면 · 대본 · 기타 판정, v1.1 head-pose 엔진) | jewon-kim | `jewon-kim/gaze-tracking/` | 브라우저 엔진(TS)은 `frontend/` (FE와 협의 필요), 1초 기록 요약은 `features/gaze/`, 나머지는 `research/gaze-tracking/` | 이전 전 |
| coach-agent (규칙 기반 실시간 코치) | jewon-kim | `jewon-kim/coach-agent/` | `features/coach/` + `research/coach-agent/` | 이전 전 |
| script-coverage-evaluation (대본 기준 생성 · STT 전달도 채점) | jewon-kim | `jewon-kim/script-coverage-evaluation/` | `features/script_coverage/` + `research/script-coverage-evaluation/` | 이전 전 |
| script-parser (대본 슬라이드 분할 · 키워드 추출) | seojin-lee | `seojin-lee/script-parser/` | `features/script_parser/` + research | 이전 전 |
| evaluation-criteria (자유 서술 기준 판정 가능성 분류) | seojin-lee | `seojin-lee/evaluation-criteria/` | `features/evaluation_criteria/` + research | 이전 전 |
| stt-live (STT 단어 타임스탬프로 말 속도 CPM → 느림 · 보통 · 빠름) | seojin-lee | `seojin-lee/stt-live/` | `features/pace/` + research | 이전 전 |
| volume-analysis (캘리브레이션 대비 소리 크기 → 작음 · 보통 · 큼) | seojin-lee | `seojin-lee/volume-analysis/` | 판정 엔진은 `frontend/` (측정은 이미 FE에 있음, FE와 협의 필요), Take 요약은 `features/volume/` | 이전 전 |

---

## 환경변수

`ai/.env.example`을 `ai/.env`로 복사해서 씁니다. `ai/.env`는 gitignore 대상입니다.

`archive/` 아래 노트북도 `ai/.env`를 찾습니다. 현재 디렉터리부터 위로 올라가며 **처음 만나는 `.env`** 를 읽기 때문입니다. 이 키로 실제 LLM API가 호출되고 과금될 수 있으니 주의하세요.

---

## 더 읽을 것

- [archive/README.md](archive/README.md) — 동결본 사용법과 목차
- [research/README.md](research/README.md) — 연구 폴더 규칙과 기능별 구성
- Notion: [피치코치 — AI 레포 구조 및 개발 규칙](https://app.notion.com/p/3f0c2c9a181681f495b1dd8900e7a24d)
