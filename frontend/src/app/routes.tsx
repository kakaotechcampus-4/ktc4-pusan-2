import { createBrowserRouter } from 'react-router';
import { Stub } from '@/shared/ui/Stub';
import { StageDemo } from '@/features/rehearsal/Stage/StageDemo';
import { MediaDevPage } from '@/features/rehearsal/media/MediaDevPage';
import { LoginPage } from '@/features/auth/LoginPage';
import { RequireSession } from '@/features/auth/session';

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
  { path: '/',            element: <Stub id="P2"   name="홈 대시보드" track="B" /> },
  { path: '/welcome',     element: <Stub id="P1"   name="온보딩" track="B" /> },
  { path: '/login',       element: <LoginPage /> },
  { path: '/about',       element: <Stub id="P14"  name="분석 방식 설명" track="B" /> },
  { path: '/pitches',     element: <Stub id="P10"  name="Pitch 목록" track="B" /> },
  { path: '/pitch/new',   element: <Stub id="P3"   name="Pitch 생성" track="B" /> },
  { path: '/pitch/:id/edit', element: <Stub id="P13" name="Pitch 수정" track="B" /> },
  { path: '/takes',       element: <Stub id="P16"  name="Take 기록" track="B" /> },
  // ★ Take는 준비 화면의 시작 CTA에서 생긴다.
  //    그래서 준비는 pitchId, 리허설은 takeId를 받는다. 이 경계를 흐리지 말 것.
  { path: '/pitch/:pitchId/prepare',   element: <Stub id="P4"  name="리허설 준비 · Calibration" track="A" /> },
  { path: '/takes/:takeId/rehearsal',  element: <Stub id="P5"  name="실시간 코칭 리허설" track="A" /> },
  { path: '/takes/:takeId/exam',       element: <Stub id="P5x" name="실전 검증 리허설" track="A" /> },
  { path: '/takes/:takeId/processing', element: <Stub id="P6-0" name="분석 대기" track="B" /> },
  { path: '/takes/:takeId/retry',      element: <Stub id="P15" name="업로드 실패 · 재시도" track="A" /> },
  { path: '/takes/:takeId',            element: <Stub id="P6"  name="Take 리포트" track="B" /> },
  { path: '/pitch/:id/comparison',     element: <Stub id="P7"  name="Take 비교" track="B" /> },
  { path: '/pitch/:id/best',           element: <Stub id="P8"  name="Best Take 선택" track="B" /> },
  { path: '/me',          element: <Stub id="P9"   name="마이페이지" track="B" /> },
  { path: '/privacy',     element: <Stub id="F2"   name="개인정보 처리방침" track="B" /> },
  { path: '/terms',       element: <Stub id="F2"   name="이용약관" track="B" /> },
  { path: '/unsupported', element: <Stub id="P18"  name="미지원 브라우저" track="B" /> },
  // 제품 화면이 아니다. 무대 레이아웃·시선 테두리 검증용.
  { path: '/dev/stage',   element: <StageDemo /> },
  // 프레임 예산 계기판. 부하별 처리 fps 를 읽는 곳.
  { path: '/dev/media',   element: <MediaDevPage /> },
  { path: '*',            element: <Stub id="404"  name="찾을 수 없음" track="B" /> },
];

const publicPaths = ['/login', '/about', '/privacy', '/terms', '/unsupported', '/dev/stage', '*'];
export const router = createBrowserRouter(
  routes.map((route) =>
    publicPaths.includes(route.path)
      ? route
      : {
          path: route.path,
          element: <RequireSession />,
          children: [{ index: true, element: route.element }],
        },
  ),
);
