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
| [출시 카운트다운](launch-countdown.md) | #541 기기 시간 기준 자동 오픈·BE 역할·빌드 설정 | FE |
| [Pitch / Take 데이터 계약 (#570)](pitch-take-contract-570.md) | FE 불일치 처리와 BE 선행 조건 · 검증 결과 | FE |
| [화면 설계서](design/README.md) | 제품과 따로 도는 React 화면 시안 · 상태 · 흐름, 제품과의 차이 | FE |
| [로그인 인증 연동](auth.md) | 로그인 화면(P11) · 인증 흐름 | FE |
| [서비스 평점 · 의견 (#540)](service-review-540.md) | 홈 평점 · 의견 실 API 연동 계획과 계약 | FE |

문서를 추가하면 이 표에 한 줄 넣습니다. 폴더 규칙은 [frontend/FRONTEND_CONVENTION.md](../../frontend/FRONTEND_CONVENTION.md) 5절입니다.

## 코드 옆에 두는 FE 문서

그 폴더를 고칠 때 꼭 읽어야 해서 코드 옆에 둡니다.

| 문서 | 담는 것 |
|---|---|
| [frontend/src/vendor/gaze/README.md](../../frontend/src/vendor/gaze/README.md) | AI 시선 엔진 · Worker 복사본(여기서 고치지 않음)과 FE 원본 카메라 화면 |
| [frontend/src/shared/ui/README.md](../../frontend/src/shared/ui/README.md) | 공용 컴포넌트 목록과 옵션 |

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
