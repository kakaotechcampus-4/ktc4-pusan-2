# 서비스 평점·의견 — FE 실 API 연동

계약: BE PR #576, head 4b689b049148f9c317c5bc21d8177c7c0289057f.
https://github.com/kakaotechcampus-4/ktc4-pusan-2/pull/576

## 흐름

- 인증된 홈(/)이 준비되면 표시 승인 POST를 요청한다. /pitches에서는 요청하지 않는다.
- 홈 방문·계정별 UUID를 유지한다. StrictMode, 렌더, 네트워크 재시도는 같은 표시 ID를 사용한다.
- should_open만 표시 판단에 사용한다. display_count=3인 세 번째 승인도 표시한다.
- 정상 거절이나 요청 실패는 홈을 막지 않는다. 네트워크/5xx 실패만 한 번 자동 재시도한다.
- 표시 승인은 창 포커스·재연결로 재실행하지 않는다. 방문 종료·계정 변경 시 요청을 취소한다.
- 닫기·나중에·Escape·제출 성공은 홈에 머문다. 리포트 링크는 목적지로 즉시 이동한다.
- 리허설 종료의 탭/Take 후보 기록과 sessionStorage 기반 횟수 판단은 제거했다.

## API

기존 Bearer 인증 클라이언트를 사용한다. 운영 비활성화 조건이나 목 전용 경로가 없다.
VITE_USE_MOCK=false 또는 프로덕션 빌드에서는 실제 API를 호출한다.

| API | 요청 |
| --- | --- |
| POST /api/service-feedback/impressions | {display_request_id: UUID} |
| POST /api/service-feedback | {rating: 정수 1~5, suggestion: string 또는 null, category: enum} |
| POST /api/suggestions | {request_id: UUID, content: string, category: enum} |
| GET /api/service-feedback/me | 상태 재확인; 필수 사전 요청 아님 |

takeId·user_id·source는 보내지 않는다. 팝업 의견은 선택, 마이페이지 의견은 필수이며
앞뒤 공백을 뺀 Unicode 코드 포인트 기준 2,000자까지 받는다.

팝업과 마이페이지에서 의견 유형을 선택한다. 기본값은 OTHER이며 POSITIVE(좋았던 점),
IMPROVEMENT(개선할 점), PROBLEM(문제가 있었던 점), QUESTION(질문), OTHER(기타)를 전송한다.
의견이 없는 별점 제출에서는 서버가 유형을 저장하지 않는다. 생략한 요청은 OTHER, null·알 수 없는 유형은 422다.

## 실패와 중복

- 별점 제출 결과가 불확실하면 입력을 유지하고 같은 내용·유형으로 재시도한다. 입력을 잠가 다른 내용으로 중복 제출하지 않는다.
- SERVICE_FEEDBACK_ALREADY_SUBMITTED는 서버 상태를 재확인하고 닫는다.
- SERVICE_FEEDBACK_NOT_ELIGIBLE는 완료 리포트가 없다는 안내를 표시한다.
- 마이페이지의 새 의견은 새 UUID, 재시도는 같은 UUID·내용·유형이다. 성공 후 입력과 요청 ID를 초기화하고 유형은 OTHER로 돌린다.
- SUGGESTION_REQUEST_CONFLICT는 오류로 남기며 자동 새 UUID 전송은 없다. 사용자가 새 입력을 선택하면
  이전 요청이 이미 저장됐을 수 있고 새 의견이 추가될 수 있음을 확인한 뒤 새 작성을 시작한다.
- 422에서는 입력을 수정할 수 있다. 제출 중 닫기·이동은 요청을 취소하고 늦은 결과를 화면에 적용하지 않는다.
- 계정 ID로 폼과 홈 승인을 구분한다. 이전 사용자 입력과 응답을 새 사용자에게 표시하지 않는다.

## 목과 검증

MSW는 동일 경로·응답을 사용하며 사용자별 3회 승인, 별점 1회, 의견 여러 회와 중복/오류 계약을 재현한다. 유형만 바뀐 재시도도 409이며, 마이페이지 응답에 category를 포함한다.
목 데이터는 메모리 안에만 있고 새로고침 시 초기화한다. 탭·기기 간 동시성 보장은 실제 BE의 책임이다.

자동 테스트는 승인 거절/세 번째 승인/재시도 UUID/계정 변경, 별점 필수·선택 의견·409·늦은 응답,
마이페이지 입력 길이·중복 제출·충돌·계정 변경, API Bearer/경로/본문 및 리포트 즉시 이동을 포함한다.

브라우저 확인: VITE_USE_MOCK=true, VITE_MOCK_SESSION=true로 dev 서버를 실행한다.
홈 팝업에서 별점·의견 입력, 닫기와 Escape, 마이페이지 의견 제출, 홈 재방문 시 2·3회 승인,
별점 제출 후 중단 및 이후 마이페이지 의견 추가를 확인한다.
scripts/service-review-layout.check.cjs는 홈에서 320·375·768px 모달 배치를 검사하는 재현용 스크립트다.

실제 BE 배포/DB 저장·여러 기기 UI 연동, 실제 스크린리더와 카메라·마이크 기기 검증은 별도로 필요하다.
기존 디자인 토큰, stage.css, 대본 높이, 시선 측정/보정 조건과 홈 점수 표시는 바꾸지 않았다.

## #567 연동 당시 검증 결과

실행 위치: output/pr567-review-fix/frontend.
- npm test: 64개 파일, 630개 테스트 통과.
- npm run build: 타입 검사와 프로덕션 빌드 통과. 기존 500 kB 청크 경고 유지.
- 변경한 src 파일 대상 oxlint --max-warnings=0, Prettier, git diff --check 통과.
- npm run lint: 기존 Date 순수성 경고 3건(DatePicker, WelcomePage, RehearsalPage)으로 실패. 이번 수정 파일에는 경고 없음.
- MSW 브라우저: 320·375px 팝업, 별점 키보드 선택, Escape 닫기, 3번째 승인, 별점·의견 제출, 제출 후 재노출 중단, 마이페이지 추가 의견 성공 확인. 콘솔 오류 없음.
- 실제 BE와 DB 저장은 미검증. #576 배포 후 실서버 통합 확인 필요.


## category 후속 반영 검증 (2026-10-28)

실행 위치: `C:/Users/ajh83/.codex/worktrees/feedback-category/pitchcoach/frontend`.
`npm ci --no-audit --no-fund`로 lockfile 기준 의존성을 설치했다.

- `npm test`: 80개 파일, 729개 테스트 통과.
- `npm run typecheck`, `npm run build`, `npm run lint`, `npm run format:check`: 모두 통과.
- 저장소 루트 `git diff --check`: 통과.
- 두 폼의 다섯 유형 전송, 기본 OTHER, 실패 후 같은 유형 재시도, 422 후 수정,
  성공·새 작성·계정 전환 초기화, 목 API 기본값·유형 검증·유형 변경 충돌을 자동 검증했다.
- 실제 브라우저 시각·스크린리더, 실제 BE·DB·여러 기기 연동은 이번에 검증하지 않았다.
  category를 받는 #576 서버 배포 후 실서버 통합 확인이 필요하다.
