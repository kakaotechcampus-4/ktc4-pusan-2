import type { ReactElement } from 'react';
import { createBrowserRouter } from 'react-router';
import { PageTitle } from './PageTitle';
import { Stub } from '@/shared/ui/Stub';
import { StageDemo } from '@/features/rehearsal/stage/StageDemo';
import { MediaDevPage } from '@/features/rehearsal/media/MediaDevPage';
import { SttDevPage } from '@/features/rehearsal/media/SttDevPage';
import { LoginPage } from '@/features/auth/LoginPage';
import { RequireSession } from '@/features/auth/session';
import { WelcomePage } from '@/features/onboarding/WelcomePage';
import { DeviceCheckPage } from '@/features/rehearsal/prepare/DeviceCheckPage';
import { PrepareRedirect } from '@/features/rehearsal/prepare/PrepareRedirect';
import { PitchCreatePage } from '@/features/pitch/create/PitchCreatePage';
import { RehearsalPage } from '@/features/rehearsal/stage/RehearsalPage';

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
  { path: '/',            title: '홈 대시보드 | 피치코치', element: <Stub id="P2"   name="홈 대시보드" track="B" /> },
  { path: '/welcome',     title: '피치코치 | AI 발표 코치', element: <WelcomePage /> },
  { path: '/login',       title: '로그인 | 피치코치', element: <LoginPage /> },
  { path: '/about',       title: '분석 방식 설명 | 피치코치', element: <Stub id="P14"  name="분석 방식 설명" track="B" /> },
  { path: '/pitches',     title: 'Pitch 목록 | 피치코치', element: <Stub id="P10"  name="Pitch 목록" track="B" /> },
  { path: '/pitch/new',   title: 'Pitch 생성 | 피치코치', element: <PitchCreatePage /> },
  { path: '/pitch/:id/edit', title: 'Pitch 수정 | 피치코치', element: <Stub id="P13" name="Pitch 수정" track="B" /> },
  { path: '/takes',       title: 'Take 기록 | 피치코치', element: <Stub id="P16"  name="Take 기록" track="B" /> },
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
  { path: '/takes/:takeId/processing', title: '분석 대기 | 피치코치', element: <Stub id="P6-0" name="분석 대기" track="B" /> },
  { path: '/takes/:takeId/retry',      title: '업로드 실패 · 재시도 | 피치코치', element: <Stub id="P15" name="업로드 실패 · 재시도" track="A" /> },
  { path: '/takes/:takeId',            title: 'Take 리포트 | 피치코치', element: <Stub id="P6"  name="Take 리포트" track="B" /> },
  { path: '/pitch/:id/comparison',     title: 'Take 비교 | 피치코치', element: <Stub id="P7"  name="Take 비교" track="B" /> },
  { path: '/pitch/:id/best',           title: 'Best Take 선택 | 피치코치', element: <Stub id="P8"  name="Best Take 선택" track="B" /> },
  { path: '/me',          title: '마이페이지 | 피치코치', element: <Stub id="P9"   name="마이페이지" track="B" /> },
  { path: '/privacy',     title: '개인정보 처리방침 | 피치코치', element: <Stub id="F2"   name="개인정보 처리방침" track="B" /> },
  { path: '/terms',       title: '이용약관 | 피치코치', element: <Stub id="F2"   name="이용약관" track="B" /> },
  { path: '/unsupported', title: '미지원 브라우저 | 피치코치', element: <Stub id="P18"  name="미지원 브라우저" track="B" /> },
  // 제품 화면이 아니다. 무대 레이아웃·시선 테두리 검증용.
  { path: '/dev/stage',   title: '무대 레이아웃 검증 | 피치코치', element: <StageDemo /> },
  // 프레임 예산 계기판. 부하별 처리 fps 를 읽는 곳.
  { path: '/dev/media',   title: '미디어 성능 검증 | 피치코치', element: <MediaDevPage /> },
  // 실시간 STT WebSocket 검증. takeId 를 손으로 넣어 WS 경로만 실서버에 붙인다.
  { path: '/dev/stt',     title: '실시간 STT 검증 | 피치코치', element: <SttDevPage /> },
  { path: '*',            title: '찾을 수 없음 | 피치코치', element: <Stub id="404"  name="찾을 수 없음" track="B" /> },
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

export const router = createBrowserRouter(
  routes.map((route) =>
    publicPaths.includes(route.path)
      ? {
          path: route.path,
          element: <PageTitle title={route.title}>{route.element}</PageTitle>,
        }
      : {
          path: route.path,
          element: (
            <PageTitle title={route.title}>
              <RequireSession />
            </PageTitle>
          ),
          children: [{ index: true, element: route.element }],
        },
  ),
);
