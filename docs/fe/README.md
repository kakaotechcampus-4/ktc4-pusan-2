# docs/fe

피치코치 FE 파트의 기술 문서입니다. 코드를 열지 않은 사람(BE · AI · 나중의 FE)이 FE가 무엇을 왜 그렇게 하는지, 다른 파트와 무엇을 주고받는지 알 수 있게 씁니다.

## 여기에 두는 것

- **구조**: 화면 흐름, 상태를 어디에 두는지(서버 · 로컬 · IndexedDB), 폴더 구성
- **다른 파트와의 계약**: FE가 BE · AI와 주고받는 데이터의 모양 · 시점 · 실패 처리 (예: 리허설 중 오디오 전송, 1초 시선 기록, 새로고침 복구)
- **기능별 설계**: 큰 기능(피치 생성 · 리허설 · 시선 엔진 등)의 목적과 처리 흐름

## 여기에 두지 않는 것

| 내용 | 위치 |
|---|---|
| 지켜야 할 결정 · 명령어 · 작업 순서 | [frontend/AGENTS.md](../../frontend/AGENTS.md) |
| 코드 스타일 · 네이밍 | [frontend/FRONTEND_CONVENTION.md](../../frontend/FRONTEND_CONVENTION.md) |
| 코드만 봐도 아는 컴포넌트 설명 | 코드 |

## 문서 지도

| 문서 | 담는 것 | 담당 |
|---|---|---|
| [FE 구조](architecture.md) | 화면 흐름(라우트), 상태를 어디에 두나(Query · Zustand · IndexedDB · 라우터 state), 폴더와 의존 방향, BE · AI와 주고받는 것, 시선 파이프라인 개요 | FE |
| [분석 대기 · 리포트 · Take 기록 (FE)](features/report.md) | 분석 대기 · 대본 확인 · 리포트 · Take 비교 · 설문, 리포트 조회를 멈추는 조건, 리뷰가 없을 때의 화면 | FE |
| [장치 점검 · 리허설 (FE)](features/rehearsal.md) | Take 만들기 · 카메라 · 마이크 · 시선 기준점, 발표 중 WS(PCM · `metrics` · `slide` · `stop`), 시선 호출 흐름, 끝내기 · 재시도 · 새로고침 이어 가기 | FE |
| [피치 만들기 · 수정 (FE)](features/pitch-create.md) | 발표 정보 · 슬라이드 · 대본 매핑 · 평가 기준 · 버전, 대본 ↔ 슬라이드 연결 규칙, 연습 조합, BE 경로 · 한도 | FE |
| [대화형 에이전트 (FE)](features/chat-agent.md) | 대화창 화면 흐름 · 상태 · 파일 구성 · BE 경로 · 조회 간격과 재시도 · 안내 문구 · 남은 한계 (기준은 AI chat-agent.md) | FE |
| [출시 카운트다운](launch-countdown.md) | #541 기기 시간 기준 자동 오픈·BE 역할·빌드 설정 | FE |
| [Pitch / Take 데이터 계약 (#570)](pitch-take-contract-570.md) | FE 불일치 처리와 BE 선행 조건 · 검증 결과 | FE |
| [화면 설계서](design/README.md) | 제품과 따로 도는 React 화면 시안 · 상태 · 흐름, 제품과의 차이 | FE |
| [화면 설계서 명명·분류 규칙](design/NAMING.md) | 화면 이름 · 그룹 · 파일명 · 검토 시안 관리 기준 | FE |

문서를 추가하면 이 표에 한 줄 넣습니다.

## 기능 문서의 틀

`features/`의 기능 문서는 모두 아래 틀을 씁니다. 다른 문서에서 "[rehearsal.md](features/rehearsal.md) 6-3"처럼 링크와 절 번호로 가리키므로, 절 번호는 바꾸지 않습니다. 쓸 내용이 없는 절도 제목은 남깁니다.

**맨 위 머리말** — 절 목록 앞에 세 줄을 둡니다.

| 줄 | 담는 것 |
|---|---|
| 기준 범위 | 이 문서가 기준인 화면 · 범위와, BE · AI 쪽 기준 문서 |
| 한 줄 요약 | 이 기능이 무엇을 하는지 한 문장 |
| 기준값 | 숫자 · 경로의 출처가 되는 코드 파일 · 상수. 문서의 숫자와 코드가 다르면 코드가 맞습니다 |

**절**

| 절 | 내용 |
|---|---|
| 0 | 한눈에 보기 — 핵심 몇 줄과 화면 주소 |
| 1 | 왜 필요한가 · 용어 |
| 2 | 화면 흐름 — 그림 · 구역 · 화면 사이 이동 |
| 3 | 상태를 어디에 두나 — Query · Zustand · IndexedDB · 라우터 state · 브라우저 저장소 |
| 4 | 폴더 · 주요 파일 |
| 5 | BE · AI 계약 — 경로 · 필드 · 기준 문서 |
| 6 | 처리 흐름 |
| 7 | 로딩 · 빈 · 오류 · 부분 실패 — 상황마다 화면에 보이는 것 |
| 8 | 시간 · 성능 제약 — 간격 · 제한 시간 · 한도 |
| 9 | 테스트 |
| 10 | 열린 질문 · 알려진 한계 |

- 전체 구조 문서([architecture.md](architecture.md))는 기능 문서가 아니라 이 틀을 따르지 않고, 자기 절 번호(0–8)를 지킵니다.
- 절 안의 작은 절은 `6-1`처럼 붙입니다.

## 지금 다른 곳에 있는 FE 문서

| 문서 | 담는 것 |
|---|---|
| [frontend/src/features/auth/README.md](../../frontend/src/features/auth/README.md) | 로그인 인증 연동 |
| [frontend/src/vendor/gaze/README.md](../../frontend/src/vendor/gaze/README.md) | AI 시선 엔진 · Worker 복사본(여기서 고치지 않음)과 FE 원본 카메라 화면 |
| [frontend/src/features/pitch/HOME_API.md](../../frontend/src/features/pitch/HOME_API.md) | 홈 화면이 쓰는 API |
| [frontend/src/shared/ui/README.md](../../frontend/src/shared/ui/README.md) | 공용 컴포넌트 |
| [frontend/public/models/README.md](../../frontend/public/models/README.md) | 시선 엔진 모델 자산(커밋하지 않고 빌드 때 받음) |
| [frontend/public/worklets/README.md](../../frontend/public/worklets/README.md) | AudioWorklet 파일(번들러를 거치지 않는 평범한 JS) |

## FE가 함께 읽는 다른 파트 문서

| 문서 | FE가 볼 곳 |
|---|---|
| [docs/ai/features/gaze-tracking.md](../ai/features/gaze-tracking.md) | 5-6 브라우저 엔진 API, 6-1 프레임 → 1초 기록 |
| [docs/ai/architecture.md](../ai/architecture.md) | 1-4 FE ↔ BE 메시지(WebSocket), 6-2 1초 원자료 모양 |

## 수정 규칙

| 변경 | 절차 |
|---|---|
| FE 안에서 끝남 (화면 흐름 · 상태 위치 · 내부 구조) | FE가 바로 고칩니다. 일반 PR 리뷰 |
| BE · AI와 주고받는 값이 바뀜 (요청 · 응답 필드, 보내는 시점, 실패 처리) | Issue에 바꾸기 전 · 후를 적고 그 파트 담당을 @멘션해 합의한 뒤 고칩니다. PR 리뷰어에 그 파트 담당을 넣습니다 |

- 다른 파트는 이 폴더를 직접 고치지 않고 고칠 곳을 Issue로 남깁니다.
- 여러 파트가 함께 지키는 계약은 [docs/](../README.md) 바로 아래에 두고 관련 파트가 함께 리뷰합니다.
- 같은 내용은 한 곳에만 씁니다. 다른 문서의 내용이 필요하면 복사하지 않고 링크합니다.
- 아직 못 쓴 내용은 `> **작성 필요** — <무엇> (#Issue)`로 표시합니다.
