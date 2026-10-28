# #541 출시 카운트다운 · #599 기기 시간 자동 오픈

## 확정한 정책 (2026-10-28 변경)

- 팀 결정에 따라 **FE 기기 시간 기준 자동 오픈**을 사용한다. 공개 예정은 2026-10-30 14:00 Asia/Seoul (`2026-10-30T14:00:00+09:00`)이다.
- FE는 `Date.now()`와 공개 예정 시각을 비교한다. `GET /api/launch-status`, `is_open`, BE 운영 공개 허용 설정에 의존하지 않는다.
- 카운트다운과 경로 안내는 FE 빌드 환경변수 `VITE_LAUNCH_GATE_ENABLED`가 정확히 문자열 `true`일 때만 적용한다. `false`·미설정·그 외 값이면 기존 로그인·세션 흐름을 유지한다.
- 공개 전에는 상단·히어로·하단 CTA를 비활성화하고 공개 일시와 일/시간/분/초를 표시한다. 카운트다운은 고정 폭 숫자이며 스크린리더에 매초 알림을 보내지 않는다.
- 예정 시각 이후에는 안내를 숨기고 세 CTA를 로그인 링크로 바꾼다. 온보딩을 강제로 이동시키지는 않는다.
- `/login`, `/`, 세션이 필요한 경로는 예정 시각 전이면 `/welcome`으로 안내한다. 이후 기존 인증 흐름을 실행한다.
- `/welcome`, `/privacy`, `/terms` 및 기존 공개·개발 경로는 유지한다.
- 매초 기기 시각으로 다시 계산하고 탭 복귀·창 포커스 때도 갱신한다. 백그라운드에서 누락된 초를 누적하지 않는다. 화면 전환은 브라우저 타이머 실행 시점에 반영된다.

## BE와의 역할 구분

- 이번 결정은 **FE 안내를 우회해도 BE에서 로그인을 차단한다는 팀의 판단**을 전제로 한다. 실제 로그인 허용 시점과 차단 구현은 BE가 담당한다.
- FE는 서버로 공개 설정 값을 보내지 않는다. 기기 시간을 바꾸면 FE 화면을 먼저 열 수 있으며, 이것은 보안 차단 수단이 아니다.
- #562의 공개 상태 API와 수동 공개 설정은 이 FE 기능의 연동 요건에서 제외한다. 조회 실패·재시도·서버 준비 지연 안내와 해당 MSW 목도 제거한다.
- 기존 토큰·refresh·API·WS의 차단 여부는 이번 FE 변경으로 보장하거나 변경하지 않는다. BE 로그인 차단 및 운영 배포 상태는 이번 작업에서 검증하지 않는다.
- BE가 공개 예정 시각에도 로그인을 차단하면 FE 버튼은 활성화되지만 로그인은 거절된다. 기기 시간이 틀리면 화면이 일찍 또는 늦게 열린다.

## 빌드·배포

- release: `VITE_LAUNCH_GATE_ENABLED=true`, develop-next/dev: `false`를 유지한다.
- `frontend/Dockerfile`의 같은 이름 `ARG`에 Docker `--build-arg`, Compose `build.args` 또는 Actions `build-args`로 전달한다.
- Vite 빌드 시 포함되는 값이므로 변경 시 재빌드·재배포한다. 실행 컨테이너 환경변수 변경이나 재시작만으로는 반영되지 않는다.
- 공개 시각에 변수를 끄거나 FE를 다시 배포할 필요는 없다. 공개 예정 일시 자체를 바꾸려면 `frontend/src/shared/api/launch.ts`를 수정하고 재빌드·재배포한다.
- 실제 테스트는 개발 환경에서 진행한다. release에서 로그인하지 않는다. BE·인프라 파일 변경은 이번 작업에 포함하지 않는다.

## 검증

예정 시각 직전·정각·직후, 타임존 표현, 열린 화면의 자동 전환, 탭 복귀, 세 CTA, 경로 가드, 빌드 설정별 진입, 공개 상태 API 호출이 없음을 자동 테스트로 확인한다.

### 2026-10-28 기기 시간 방식 검증

- 실행 위치: `output/launch-local-time/frontend` (기준 `origin/develop-next`의 `60453589`).
- 기존 설치 의존성을 연결하고 TEMP/TMP를 `.tmp/launch-local`로 지정했다.
- `npm run test -- --maxWorkers=2`: 80개 파일, 703개 테스트 통과. 카운트다운·경로 테스트 18개도 별도 실행해 통과했다.
- `npm run typecheck`, `npm run lint`, `npm run format:check`: 통과.
- `VITE_LAUNCH_GATE_ENABLED=true` 및 `false` 각각 `npm run build`: 통과.
- 작업 폴더 루트에서 `git diff --check`: 통과.
- 실제 브라우저·BE 로그인 차단·OAuth·API·WS·운영 배포는 미검증. 검증 이후 #562에 정책 변경을 기록하고 종료했으며, FE 후속 구현은 #599에서 추적한다.

## 이전 정책의 검증 기록

아래는 서버 공개 상태 API를 사용하던 당시의 기록이며, 현재 정책은 위 내용을 따른다.

### 2026-10-27 환경변수 전환 검증

- 실행 위치: `output/launch-countdown-pr/frontend`.
- TEMP/TMP를 `.tmp/launch-domain`으로 지정하고 `npm run test -- src/shared/api/launch.test.ts src/features/onboarding/LaunchCountdown.test.tsx --maxWorkers=1`: 15개 통과. `true`의 차단 정책과 `false`·미설정·빈 값·`TRUE`의 조회 생략 및 기존 진입을 확인했다.
- `VITE_LAUNCH_GATE_ENABLED=true`와 `false` 각각 `npm run build`: 타입 검사·빌드 통과(기존 번들 크기 경고).
- 변경 TS/TSX의 Prettier 검사·oxlint 및 저장소 루트 `git diff --check`: 통과.
- Docker 이미지 빌드·실제 배포·OAuth·API·WS는 미검증이며 release에 접속하거나 로그인하지 않았다.

### 2026-10-27 도메인 분기 검증 (환경변수 전환 이전 기록)

- 실행 위치: `output/launch-countdown-pr/frontend`.
- `npm run test -- src/shared/api/launch.test.ts src/features/onboarding/LaunchCountdown.test.tsx --maxWorkers=1`: 14개 통과. release의 기존 차단 정책과 dev·localhost·127.0.0.1의 CTA 활성화, 가드 통과, 안내 숨김, 탭 복귀 시 조회 생략을 확인했다.
- Windows 임시 폴더 rename 권한 오류 때문에 TEMP/TMP를 작업 폴더의 `.tmp/launch-domain`으로 지정해 테스트를 실행했다.
- `npm run build`: 타입 검사·프로덕션 빌드 통과(기존 번들 크기 경고).
- 변경 TS/TSX 4개 파일 대상 `npx oxlint --max-warnings=0`, Prettier 및 저장소 루트 `git diff --check`: 통과.
- release에 접속하거나 로그인하지 않았다. 실제 dev 서버의 OAuth·API·WS 및 운영 배포는 미검증이다.

### 2026-10-26 FE 구현 검증

- 실행 위치: `frontend/`.
- `npx vitest run src/shared/api/launch.test.ts src/features/onboarding/LaunchCountdown.test.tsx`: 2개 파일, 9개 테스트 통과. 경계 시각·동일 공개 순간의 타임존 표현·잘못된 응답·서버 미공개·세 CTA·오류 안내·로그인 경로 가드를 확인했다.
- `npm run build`: 타입 검사 및 프로덕션 빌드 통과. 기존 번들 크기 경고가 있다.
- 신규 로직·컴포넌트·테스트·라우트 대상 `npx oxlint --max-warnings=0`: 통과.
- 전체 `npm run lint`는 기존 WelcomePage·DatePicker·RehearsalPage의 Date 순수성 경고로 통과하지 못했다. 신규 훅 의존성 경고는 수정했다.
- 최초 테스트는 Windows 임시 폴더 rename 권한 오류로 실행되지 않아 저장소 `output/launch-tests-temp`로 TEMP/TMP를 지정해 재실행했다.
- 실브라우저 모바일/데스크톱 시각 검수, 키보드·스크린리더 실사용, 탭 복귀·실제 서버·OAuth·WS·운영 배포는 아직 검증하지 않았다.

### 2026-10-26 브라우저 시각 검수

- 실행 위치: `frontend/`, `npm run dev -- --port 3011`. 인앱 Chromium에서 `http://localhost:3011/welcome`의 개발 목 응답으로 확인했다.
- 데스크톱 1440×1000, 노트북 1366×768, 모바일 390×844·360×800·320×740의 CSS 뷰포트를 확인했다. 실제 휴대폰 검증은 아니다.
- 날짜·일/시간/분/초의 겹침이나 잘림 없이 표시되고 초가 갱신된다. 세 CTA가 ‘오픈 예정’으로 비활성화된다.
- 320px에서는 날짜가 자연스럽게 두 줄로 바뀐다. 짧은 화면에서는 스크롤로 버튼까지 확인해야 한다.
- 320px에서 발견한 약 1px 가로 넘침을 랜딩 루트의 `overflow-x-clip`으로 제거했다. 모바일 메뉴 간격도 줄였다. 수정 후 문서 clientWidth/scrollWidth가 모두 305px, 1440px 뷰포트에서는 모두 1425px인 것을 확인했다(세로 스크롤바 제외).
- 캡처: 저장소 `output/launch-qa/desktop-1440.jpg`, `mobile-390.jpg`, `mobile-320.jpg`.
- 키보드·스크린리더 실사용, 실제 BE 공개 전환과 인증 차단은 이번 시각 검수 범위에 포함하지 않았다.

### PR 분리 검증

origin/develop-next 기반 feat/fe-launch-countdown에서 카운트다운 변경만 분리했다. 다른 feature/fix PR을 먼저 병합할 필요가 없는 독립 PR이다. frontend/에서 npm run build, 두 테스트 파일의 vitest(9개), 신규 코드와 라우트의 oxlint가 통과했다. 원래 작업 디렉터리의 대본 확인·서비스 리뷰 변경은 포함하지 않았다.

카운트다운은 흰색 패널 위의 플립 카드로 표시한다. 변경된 단위만 560ms 동안 전환하며 기존 모션 정지 및 prefers-reduced-motion에서 정적인 숫자로 표시한다. 데스크톱과 390px·320px 브라우저 뷰포트에서 표시를 확인했다.
