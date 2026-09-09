# 로그인 인증 연동 (P11)

## 로컬 브랜치

- 화면 보관: feat/fe-login-page, 4cee762
- 인증 작업: feat/fe-login-auth (화면 브랜치에서 분기)
- 원본 pitchcoach-fe-share 폴더는 수정하지 않았습니다.
- 초기 코드가 팀에 병합되면 화면 변경과 인증 변경을 별도로 옮깁니다.

## 구현 범위

Google 로그인 페이지 이동 → /login 복귀 → refresh → /api/users/me → 원래 목적지.
Access Token은 shared/api/tokenStore.ts 메모리에만 보관합니다.
refresh와 logout은 credentials: include 및 최신 csrf_token의 X-CSRF-Token 헤더를 사용합니다.
동일 탭의 갱신은 하나의 Promise로 합치며, 늦게 도착한 401은 이미 갱신한 토큰을 사용합니다.
Web Locks 지원 브라우저에서는 탭 간 쿠키 요청도 직렬화합니다.
401 갱신 실패는 비로그인 처리, 네트워크/서버 오류는 재시도 안내로 구분합니다.
로그아웃 성공 시 사용자별 Query 캐시를 비웁니다.
공용 API 호출은 shared/api/client.ts의 apiRequest를 사용해야 이 처리가 적용됩니다.
신규 사용자 온보딩 분기는 구현하지 않았습니다.

## 실행

frontend에서 npm ci, npm run dev 실행 후 /login을 엽니다.
기본 MSW 환경은 비로그인입니다. Google 버튼은 서버 연결 안내를 표시합니다.
목 응답으로 실제 로그인한 것으로 표시하지 않습니다.

실서버용 .env.local:

```dotenv
VITE_USE_MOCK=false
VITE_API_BASE=http://localhost:8000
```

프론트는 http://localhost:5173 으로 접속하고 백엔드 FRONTEND_BASE_URL도 이 값으로 맞춥니다.
localhost와 127.0.0.1을 혼용하면 CSRF 쿠키를 읽지 못합니다.
운영은 동일 호스트에서 /api를 백엔드로 프록시하며 VITE_API_BASE를 비웁니다.
백엔드는 Google OAuth 구현 코드, PostgreSQL, Redis 및 Google 클라이언트 설정이 필요합니다.
GOOGLE_REDIRECT_URI는 Google Console의 허용된 callback URI와 일치해야 합니다.
.env의 Google secret은 프론트에 넣지 않습니다.

실제 Google 계정 로그인/새로고침/로그아웃, 다중 탭 및 쿠키 정책의 브라우저 검증은
백엔드 실행 환경 연결 후 수행해야 합니다. 현재 자동 테스트는 모의 HTTP 응답을 사용합니다.
다른 탭에서의 로그아웃은 이미 발급된 Access Token을 즉시 무효화하지 않습니다.

## 검토 위치

shared/api/: 토큰 보관, 갱신 합류, 재시도 제한, 테스트.
features/auth/: 화면, 세션 훅, 보호 라우트.
app/routes.tsx: 공개 경로 이외에 인증 가드 적용.
mocks/auth.ts: 비로그인 기본 응답.

토큰 모듈은 shared 계층에서 features를 참조하지 않도록 shared/api에 배치했습니다.
공용 types/api.ts의 기존 ApiError.detail 타입은 수정하지 않았습니다.
Google 공식 버튼 이미지 출처: https://developers.google.com/identity/branding-guidelines

