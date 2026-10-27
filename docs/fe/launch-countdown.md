# #541 출시 카운트다운

## 확정한 정책

- 카운트다운·공개 상태 조회·진입 제한은 FE 빌드 환경변수 `VITE_LAUNCH_GATE_ENABLED`가 문자열 `true`일 때만 적용한다. 도메인이나 브랜치명을 코드에서 비교하지 않는다.
- `false` 또는 미설정이면 공개 상태 API를 호출하지 않고 카운트다운을 숨기며 기존 로그인·세션 흐름을 유지한다. `TRUE` 등 다른 값도 활성화하지 않는다.
- 실제 테스트는 개발 도메인에서 진행한다. release 도메인에서는 로그인하지 않는다. 활성화 분기는 자동 테스트에서 환경변수를 모의해 확인한다.

- 공개 예정: 2026-10-30 14:00 Asia/Seoul (`2026-10-30T14:00:00+09:00`).
- 기존 온보딩 제목·캐릭터·소개·사용방법을 유지한다. 히어로 소개 아래, 시작 버튼 위에 공개 일시와 일/시간/분/초를 표시한다.
- 상단·히어로·하단 CTA는 동일한 공개 상태를 사용하며 공개 전에는 비활성화한다.
- `/login`, `/`, 세션이 필요한 내부 경로는 공개 상태를 먼저 확인하고 미공개면 `/welcome`으로 안내한다. 세션 확인은 그 이후 실행한다.
- `/welcome`, `/privacy`, `/terms`는 계속 접근 가능하다. 소개·브라우저 안내·기존 개발 경로는 유지한다.
- 서버 시각이 공개 예정 이후이며 서버의 실제 공개 여부가 참일 때만 진입을 허용한다. 기기 시각이나 카운트다운 0만으로 열지 않는다.
- 공개 시 온보딩의 안내를 내리고 기존 CTA를 활성화한다. 보고 있는 온보딩을 강제로 이동시키지 않는다.
- 최초 조회 중에는 확인 안내, 실패 시 재시도 안내와 버튼을 표시한다. 실패를 공개로 간주하지 않는다.
- 보호 경로의 재조회가 실패하면 마지막 성공 응답을 유지해 진행 중인 발표를 끊지 않는다. 최초 조회 실패와 서버가 명시적으로 비공개를 반환한 경우에는 진입을 차단한다.
- 예정 시각 이후 미공개면 ‘조금만 기다려 주세요. 오픈 준비 중이에요.’와 ‘오픈 준비 중’ 버튼을 표시한다.
- 공개 상태는 30초마다, 탭 복귀 시, 예정 시각 도달 시 재조회한다. 숫자는 서버 시각과 단조 증가 시계의 차이로 매초 다시 계산한다.
- 카운트다운은 고정 폭 숫자를 사용하고 스크린리더에 매초 알림을 보내지 않는다.

## BE 확인 요청 — 아직 합의되지 않음

1. Google start/callback 차단 외에 기존 Refresh 쿠키·유효 Access Token의 보호 API·새 WebSocket 접근도 차단하는지 확인한다. 기존 열린 WS 종료는 별도 합의가 필요하다.
2. 공통 상태 API의 경로와 필드를 확정한다. FE 임시 계약은 `GET /api/launch-status` → `{ server_now: ISO8601, launch_at: ISO8601, is_open: boolean }`이다. 시간에는 타임존을 명시한다. `Cache-Control: no-store`와 비인증 접근이 필요하다.
3. 일반 API/refresh의 미공개 오류 코드·응답 구조를 정한다(제안: `403 SERVICE_NOT_OPEN`). Google 전체 이동 요청은 고정된 `/welcome`으로 안내하는지 확인한다.
4. 운영 공개 허용 설정을 누가 언제 변경하며 모든 인스턴스에 어떻게 적용하는지 확정한다. 날짜가 지나도 준비 미완료면 닫힌 상태를 유지한다.
5. 내부 테스트는 개발 환경을 기본으로 제안한다. 운영 계정 예외 필요 여부는 미정이다.
6. WS 거절 코드와 FE 재연결 중단 계약을 확정한다.

FE는 화면 안내를 담당한다. 실제 인증/API/WS 차단은 BE 책임이며 이번 변경에 BE·인프라 구현은 포함하지 않는다. 위 목록은 담당에게 전달할 요청 초안이며 외부 메시지를 전송한 것은 아니다.

## 구현 및 연동 상태

- FE 상태 어댑터는 `frontend/src/shared/api/launch.ts` 한 곳에 둔다. 계약 확정 후 이곳과 목을 갱신한다.
- 개발 MSW는 현재 서버 시각·예정 일시·`is_open: false`를 반환한다. 출시 제한을 끈 빌드에서는 해당 목을 조회하지 않는다.
- 출시 제한을 켠 빌드는 실제 API 미구현/실패 시 대기 안내를 유지하므로 BE API 동시 준비가 운영 배포 전제다.
- 공개 중 서비스 재차단의 API 오류/refresh/WS 처리는 계약 확정 후 추가 연동한다. 현재 가드는 라우트 진입과 주기적 상태 조회를 기준으로 안내한다.

## 인프라 담당자 요청 사항

- release FE 빌드: `VITE_LAUNCH_GATE_ENABLED=true`.
- develop-next/dev FE 빌드: `VITE_LAUNCH_GATE_ENABLED=false`. 로컬 개발도 기본값은 false다.
- `frontend/Dockerfile`에 같은 이름의 `ARG`를 추가했다. Docker 빌드 명령에는 `--build-arg VITE_LAUNCH_GATE_ENABLED=true`(release) 또는 `false`(dev)를 전달한다. Compose를 사용하면 FE 서비스의 `build.args`, GitHub Actions의 Docker 빌드 단계라면 `build-args`에 전달한다. 실제 사용하는 빌드 경로에 맞춰 연결해 달라.
- 이 값은 Vite 빌드 시 정적 JS에 포함된다. 실행 중인 Caddy 컨테이너의 `environment`만 바꾸거나 컨테이너만 재시작해서는 반영되지 않는다. 환경별 FE 이미지를 빌드하고 재배포해야 하며 dev 이미지를 release에 그대로 승격하지 않는다.
- 미설정 시 제한이 꺼지므로 release 파이프라인에서 정확히 문자열 `true`가 전달되는지 확인한다. 비밀값은 아니며 브라우저에 포함되는 공개 설정이다.
- 공개 시각에 이 변수를 끌 필요는 없다. 켠 상태에서 BE의 공개 상태 응답으로 기존 화면이 열린다. 실제 인증/API/WS 제한 및 dev 예외는 BE 설정으로 별도 관리한다.
- `.github/workflows/`와 `infra/`는 이번 FE 변경에 포함하지 않았다. 위 내용은 전달용 요청이며 담당자에게 외부 메시지를 전송하지 않았다.

## 검증

공개 직전·정각·직후, 다른 타임존, 기기 시각 변경, 탭 복귀, 지연 공개, 상태 실패·복구, 모든 CTA, 기존 세션의 경로 진입, 약관 링크를 확인한다. 실제 OAuth·API·WS 접근 차단과 운영 배포는 BE 연동 후 별도 검증이 필요하다.

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
