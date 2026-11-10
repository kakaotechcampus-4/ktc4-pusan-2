import { Suspense, type ReactElement } from 'react';
import { RouteLoadError } from './RouteLoadError';
import {
  StageDemo,
  MediaDevPage,
  SttDevPage,
  PolicyPage,
  DeviceCheckPage,
  FirstTakePage,
  PitchCreatePage,
  PitchEditPage,
  RehearsalPage,
  RetryPage,
  ProcessingPage,
  ReportPage,
  ConfirmPage,
  AboutPage,
  UnsupportedPage,
  NotFoundPage,
  MyPage,
  TakesPage,
} from './LazyScreens';
import { createBrowserRouter, Outlet, ScrollRestoration } from 'react-router';
import { TakeComparisonRedirect } from '@/features/report/TakeComparisonRedirect';
import { PageTitle } from './PageTitle';
import { LaunchGate } from './LaunchGate';
import { DesktopGate } from './DesktopGate';
import { LoginPage } from '@/features/auth/LoginPage';
import { RequireSession } from '@/features/auth/session';
import { WelcomePage } from '@/features/onboarding/WelcomePage';
import { AppHeader } from '@/shared/ui/AppHeader';
import { MAIN_ID } from '@/shared/ui/SkipLink';
import { HomePage } from '@/features/pitch/HomePage';
import { PrepareRedirect } from '@/features/rehearsal/prepare/PrepareRedirect';
import { ChatAgentHost } from '@/features/chat/ChatAgentHost';

/**
 * 화면 19개 + 모달 3개 + 상태 10개.
 * W4에는 전부 Stub이지만 라우팅은 다 열려 있어야 합니다 —
 * 그래야 두 트랙이 서로를 기다리지 않고 각자 채워 넣습니다.
 *
 * P 번호는 화면 목록 문서와 같습니다. 바꾸지 마세요.
 */
// P 번호와 경로를 열로 맞춰 둔다 — 화면 목록 문서와 나란히 놓고 대조하는 표다.
// prettier-ignore
const routes = [
  { path: '/',            title: '홈 대시보드 | 피치코치', element: <HomePage /> },
  { path: '/welcome',     title: '피치코치 | AI 발표 코치', element: <WelcomePage /> },
  { path: '/login',       title: '로그인 | 피치코치', element: <LoginPage /> },
  { path: '/about',       title: '분석 방식 설명 | 피치코치', element: <AboutPage /> },
  { path: '/pitches',     title: 'Pitch 목록 | 피치코치', element: <HomePage /> },
  { path: '/pitch/new',   title: 'Pitch 생성 | 피치코치', element: <PitchCreatePage /> },
  { path: '/pitch/tutorial', title: '첫 번째 Take 체험 | 피치코치', element: <FirstTakePage /> },
  { path: '/pitch/:id/edit', title: 'Pitch 수정 | 피치코치', element: <PitchEditPage /> },
  { path: '/takes',       title: 'Take 기록 | 피치코치', element: <TakesPage /> },
  // ★ Take는 준비 화면의 시작 CTA에서 생긴다.
  //    그래서 준비는 pitchId, 리허설은 takeId를 받는다. 이 경계를 흐리지 말 것.
  //    장치 점검은 준비 바로 앞에 선다. 여기서는 Take 를 만들지 않는다 — 점검하다 그만둔
  //    만큼 빈 Take 가 쌓이고 takeNumber 가 실제 연습 횟수와 어긋난다.
  { path: '/pitch/:pitchId/device-check', title: '장치 점검 | 피치코치', element: <DeviceCheckPage /> },
  // 준비 화면은 시작 전 세팅(09)에 합쳐졌습니다. 옛 주소는 404 대신 그쪽으로 보냅니다
  { path: '/pitch/:pitchId/prepare',   title: '시작 전 세팅 | 피치코치', element: <PrepareRedirect /> },
  //    같은 무대다. 코치가 말을 하느냐 마느냐만 다르고, 그 차이는 Take 의 mode 가 정한다 —
  //    화면이 경로로 판단하지 않는다 (경로와 Take 가 어긋나면 서버 값이 맞다).
  { path: '/takes/:takeId/rehearsal',  title: '실시간 코칭 리허설 | 피치코치', element: <RehearsalPage /> },
  { path: '/takes/:takeId/exam',       title: '실전 검증 리허설 | 피치코치', element: <RehearsalPage /> },
  { path: '/takes/:takeId/processing', title: '분석 대기 | 피치코치', element: <ProcessingPage /> },
  { path: '/takes/:takeId/confirm',    title: '대본 확인 | 피치코치', element: <ConfirmPage /> },
  { path: '/takes/:takeId/retry',      title: '업로드 실패 · 재시도 | 피치코치', element: <RetryPage /> },
  { path: '/takes/:takeId',            title: 'Take 리포트 | 피치코치', element: <ReportPage /> },
  { path: '/pitch/:id/comparison',     title: 'Take 기록 | 피치코치', element: <TakeComparisonRedirect /> },
  { path: '/me',          title: '마이페이지 | 피치코치', element: <MyPage /> },
  { path: '/privacy',     title: '개인정보 처리방침 | 피치코치', element: <PolicyPage kind="privacy" /> },
  { path: '/terms',       title: '이용약관 | 피치코치', element: <PolicyPage kind="terms" /> },
  { path: '/unsupported', title: '미지원 브라우저 | 피치코치', element: <UnsupportedPage /> },
  // 제품 화면이 아니다. 무대 레이아웃·시선 테두리 검증용.
  { path: '/dev/stage',   title: '무대 레이아웃 검증 | 피치코치', element: <StageDemo /> },
  // 프레임 예산 계기판. 부하별 처리 fps 를 읽는 곳.
  { path: '/dev/media',   title: '미디어 성능 검증 | 피치코치', element: <MediaDevPage /> },
  // 실시간 STT WebSocket 검증. takeId 를 손으로 넣어 WS 경로만 실서버에 붙인다.
  { path: '/dev/stt',     title: '실시간 STT 검증 | 피치코치', element: <SttDevPage /> },
  { path: '*',            title: '찾을 수 없음 | 피치코치', element: <NotFoundPage /> },
] satisfies { path: string; title: string; element: ReactElement }[];

const publicPaths = [
  '/login',
  '/welcome',
  '/about',
  '/privacy',
  '/terms',
  '/unsupported',
  '/dev/stage',
  '/dev/media',
  '/dev/stt',
  '*',
];

// 카메라 · 마이크로 연습하는 화면. 휴대폰 · 태블릿에서는 /unsupported 로 보냅니다 (#710).
// 분석 대기 · 대본 확인 · 재시도 같은 리포트 쪽은 모바일에서도 열립니다.
const practicePaths = [
  '/pitch/tutorial',
  '/pitch/:pitchId/device-check',
  '/takes/:takeId/rehearsal',
  '/takes/:takeId/exam',
];

// 홈과 같은 상단바(AppHeader)를 얹는 화면. 메뉴를 옮기거나 기록을 열어도 상단바가 바뀌지 않게 합니다.
// 리허설 · 분석 대기 · 대본 확인은 집중 화면이라 각자의 틀을 씁니다
const appHeaderPaths = [
  '/',
  '/pitches',
  '/takes',
  '/me',
  '/takes/:takeId/retry',
  '/pitch/new',
  '/pitch/tutorial',
  '/pitch/:id/edit',
  '/pitch/:pitchId/device-check',
];

const screenRoutes = routes.map((route) => {
  const screen = (
    <Suspense
      fallback={
        // 상단바가 먼저 그려져도 건너뛰기 링크가 갈 곳이 있도록 본문 자리를 지킵니다
        <main id={MAIN_ID} tabIndex={-1} className="flex-1 bg-panel text-ink">
          <p role="status" className="p-8">
            화면을 불러오고 있어요.
          </p>
        </main>
      }
    >
      {route.element}
    </Suspense>
  );
  return publicPaths.includes(route.path)
    ? {
        path: route.path,
        errorElement: <RouteLoadError />,
        element: (
          <PageTitle title={route.title}>
            {route.path === '/login' ? <LaunchGate>{screen}</LaunchGate> : screen}
          </PageTitle>
        ),
      }
    : {
        path: route.path,
        errorElement: <RouteLoadError />,
        element: (
          <PageTitle title={route.title}>
            <LaunchGate>
              {/* 이름 · 로그아웃 줄은 리허설 무대에만 얹습니다. 분석 대기 · 대본 확인은 화면 전체가 그림이고,
                  리포트는 화면 안에서 공용 헤더를 표시합니다 */}
              <RequireSession
                stageBar={['/takes/:takeId/rehearsal', '/takes/:takeId/exam'].includes(route.path)}
                header={appHeaderPaths.includes(route.path) ? <AppHeader /> : undefined}
              />
            </LaunchGate>
          </PageTitle>
        ),
        children: [{ index: true, element: screen }],
      };
});
// 로그인 · 공개 예정 확인보다 바깥에서 막는다 — 못 하는 연습 주소로 로그인부터 시키지 않으려는 것입니다.
export const appRoutes = screenRoutes.map((route) =>
  practicePaths.includes(route.path)
    ? { ...route, element: <DesktopGate>{route.element}</DesktopGate> }
    : route,
);
// 모든 화면 위에 늘 붙어 있는 층입니다. 화면을 옮겨도 채팅 상태가 남고, 로그인 · 기기 확인은 화면마다 그대로입니다.
// 오류 화면(RouteLoadError)은 화면마다 있고 채팅은 자기 오류 경계를 가져서, 이 층에는 errorElement 를 두지 않습니다
// 제품 라우터와 회귀 테스트가 같은 루트 층을 사용하도록 분리합니다.
export const appRouterShell = (
  <>
    <Outlet />
    <ScrollRestoration />
    <ChatAgentHost />
  </>
);

export const router = createBrowserRouter([
  {
    element: appRouterShell,
    children: appRoutes,
  },
]);
