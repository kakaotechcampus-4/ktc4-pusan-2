# 홈 API 연결

기준: [백엔드 PR #47](https://github.com/kakaotechcampus-4/ktc4-pusan-2/pull/47).

- `GET /api/pitches/`를 기존 인증 클라이언트로 호출합니다. 끝의 `/`를 유지합니다.
- 응답은 `{ pitches: [...] }`이며 타입은 `types/home.ts`에 있습니다.
- `pitch_time`, `take_time`, `take_elapsed`는 초입니다. 점수와 증감의 `null`은 0과 구분합니다.
- 날짜, 주간 통계, 자료 버전, Take 상태는 현재 응답에 없으므로 추정해서 표시하지 않습니다.
- 썸네일이 없거나 로드에 실패하면 기본 표지를 표시합니다.
- 목 환경도 동일한 응답을 사용합니다. `mocks/home.ts`를 수정하면 빈 목록·null 사례를 시연할 수 있습니다.

## 백엔드에서 필요한 후속 필드

현재 DTO에는 리소스 ID가 없습니다. 아래 필드가 추가되어야 제목 클릭 → 자료 편집과 리포트 이동을 연결할 수 있습니다.

| DTO | 필요한 필드 | 프론트 목적지 |
| --- | --- | --- |
| PitchesDTO | `pitch_id: UUID` | `/pitch/:id/edit` |
| TakeSummaryDTO | `take_id: UUID` | `/takes/:takeId` |

`pitch.title`이나 `take_number`는 ID가 아닙니다. 프론트에서 임의 ID를 만들지 않으며, ID가 없는 항목은 이동 링크를 표시하지 않습니다. 실제 ID가 응답에 추가되면 링크가 활성화됩니다.

## 실서버 확인

PR #47이 반영된 백엔드와 실제 로그인 세션이 필요합니다. `.env.local`의 `VITE_USE_MOCK=false`, `VITE_API_BASE`를 해당 백엔드에 맞추고 Vite를 다시 실행합니다. 인증과 CORS는 기존 개발 주소(3000) 설정을 사용합니다. 3002는 목 데이터 미리보기용입니다.

현재 확인 범위는 PR 코드의 응답 계약, 계약 기반 테스트와 목 화면입니다. 배포된 API에 대한 실계정 검증은 포함하지 않습니다.
