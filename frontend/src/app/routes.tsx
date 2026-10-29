import { Suspense, type ReactElement } from 'react';
import { RouteLoadError } from './RouteLoadError';
import {
  StageDemo,
  MediaDevPage,
  SttDevPage,
  PolicyPage,
  DeviceCheckPage,
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
  ComparisonPage,
} from './LazyScreens';
import { createBrowserRouter } from 'react-router';
import { PageTitle } from './PageTitle';
import { LaunchGate } from './LaunchGate';
import { LoginPage } from '@/features/auth/LoginPage';
import { RequireSession } from '@/features/auth/session';
import { WelcomePage } from '@/features/onboarding/WelcomePage';
import { AppHeader } from '@/shared/ui/AppHeader';
import { HomePage } from '@/features/pitch/HomePage';
import { PrepareRedirect } from '@/features/rehearsal/prepare/PrepareRedirect';

// 제품 화면이 아닌 검증용 화면(src/dev). 개발 서버(`npm run dev`)에만 등록한다 —
// 운영 빌드에 두면 주소만 알면 로그인 없이 열린다. 조건이 빌드 때 false 로 접혀 운영 번들에서 빠진다.
// prettier-ignore
const devRoutes = !import.meta.env.DEV ? [] : [
  // 무대 레이아웃·시선 테두리 검증용.
  { path: '/dev/stage',   title: '무대 레이아웃 검증 | 피치코치', element: <StageDemo /> },
  // 프레임 예산 계기판. 부하별 처리 fps 를 읽는 곳.
  { path: '/dev/media',   title: '미디어 성능 검증 | 피치코치', element: <MediaDevPage /> },
  // 실시간 STT WebSocket 검증. takeId 를 손으로 넣어 WS 경로만 실서버에 붙인다.
  { path: '/dev/stt',     title: '실시간 STT 검증 | 피치코치', element: <SttDevPage /> },
];

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
  { path: '/pitch/:id/comparison',     title: 'Take 비교 | 피치코치', element: <ComparisonPage /> },
  { path: '/me',          title: '마이페이지 | 피치코치', element: <MyPage /> },
  { path: '/privacy',     title: '개인정보 처리방침 | 피치코치', element: <PolicyPage kind="privacy" /> },
  { path: '/terms',       title: '이용약관 | 피치코치', element: <PolicyPage kind="terms" /> },
  { path: '/unsupported', title: '미지원 브라우저 | 피치코치', element: <UnsupportedPage /> },
  ...devRoutes,
  { path: '*',            title: '찾을 수 없음 | 피치코치', element: <NotFoundPage /> },
] satisfies { path: string; title: string; element: ReactElement }[];

const publicPaths = [
  '/login',
  '/welcome',
  '/about',
  '/privacy',
  '/terms',
  '/unsupported',
  ...devRoutes.map((route) => route.path),
  '*',
];

// 홈과 같은 상단바(AppHeader)를 얹는 화면. 메뉴를 옮기거나 기록을 열어도 상단바가 바뀌지 않게 합니다.
// 편집 화면(/pitch/new · edit)은 자체 헤더를, 장치 점검 · 리허설 · 분석 대기는 집중 화면이라 각자의 틀을 씁니다
const appHeaderPaths = [
  '/',
  '/pitches',
  '/takes',
  '/me',
  '/takes/:takeId/retry',
  '/pitch/:id/comparison',
];

export const appRoutes = routes.map((route) => {
  const screen = (
    <Suspense
      fallback={
        <p role="status" className="bg-panel p-8 text-ink">
          화면을 불러오고 있어요.
        </p>
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
              {/* 분석 대기 · 대본 확인은 화면 전체가 그림이라 이름 · 로그아웃 줄을 얹지 않습니다 */}
              <RequireSession
                showHeader={
                  ![
                    '/',
                    '/pitches',
                    '/pitch/new',
                    '/pitch/:id/edit',
                    '/takes/:takeId/processing',
                    // 리포트는 화면 안에서 공용 헤더를 표시합니다.
                    '/takes/:takeId',
                    '/takes/:takeId/confirm',
                  ].includes(route.path)
                }
                bright={!['/takes/:takeId/rehearsal', '/takes/:takeId/exam'].includes(route.path)}
                header={appHeaderPaths.includes(route.path) ? <AppHeader /> : undefined}
              />
            </LaunchGate>
          </PageTitle>
        ),
        children: [{ index: true, element: screen }],
      };
});
export const router = createBrowserRouter(appRoutes);
