/**
 * 미리보기 진입점. 화면 ID로 그릴 화면을 고르고, 미리보기 안의 링크를 설계서 안에서만 이동시킨다.
 * 화면은 `src/screens/`의 샘플(제품 코드를 읽고 설계서가 다시 그린 것, import하지 않음), 기초는 `src/foundations/`,
 * 컴포넌트 · 대화 상자는 `src/components/`다.
 * 실제 서버나 기기에 접근하지 않는다.
 */
import { useState, type MouseEvent } from 'react';
import { Frame, type Navigate } from './screens/shared';
import { HomeScreen } from './screens/HomeScreen';
import { CreateConceptScreen } from './screens/CreateConceptScreen';
import { CreateScreen } from './screens/CreateScreen';
import { PrepareScreen } from './screens/PrepareScreen';
import { StageScreen } from './screens/StageScreen';
import { ProcessingScreen, ConfirmScreen } from './screens/AnalysisScreen';
import { RetryScreen } from './screens/RetryScreen';
import { ReportScreen } from './screens/ReportScreen';
import { TakesScreen } from './screens/TakesScreen';
import { ComparisonScreen } from './screens/ComparisonScreen';
import {
  AboutScreen,
  MyScreen,
  NotFoundScreen,
  StageBlockedScreen,
  UnsupportedScreen,
} from './screens/InfoScreens';
import { LoginScreen, WelcomeScreen } from './screens/OnboardingScreens';
import {
  ColorsFoundation,
  ResponsiveFoundation,
  SpacingFoundation,
  SurfacesFoundation,
  TypographyFoundation,
  WritingFoundation,
} from './foundations/Foundations';
import { ComponentDoc } from './components/ComponentDocs';
import { componentEntries } from './components/entries';
import { AppHeader } from '@/ui/AppHeader';

export type { Navigate as PreviewNavigate } from './screens/shared';

/**
 * 위에 공용 상단바가 얹히는 화면과 그때 표시되는 메뉴. 제품 `app/routes.tsx`의 appHeaderPaths와
 * 리포트(ReportPage가 직접 둔다)다. 피치 생성은 #738에서 더해졌다(시작 전 세팅은 #706에서 정한다)
 */
const APP_HEADER: Record<string, 'home' | 'takes' | 'me' | null> = {
  home: 'home',
  takes: 'takes',
  me: 'me',
  retry: null,
  comparison: null,
  report: null,
  'pitch-create': null,
  'pitch-create-a': null,
};

/** 제품 경로를 설계서 화면으로 바꾼다. 알 수 없는 경로는 제품으로 열지 않는다 */
function targetFor(path: string): string | null {
  if (path === '/') return 'home';
  if (path === '/pitch/new' || /^\/pitch\/[^/]+\/edit$/.test(path)) return 'pitch-create';
  if (/^\/pitch\/[^/]+\/device-check$/.test(path)) return 'prepare-mission';
  if (/^\/pitch\/[^/]+\/comparison$/.test(path)) return 'comparison';
  if (/^\/takes\/[^/]+\/(rehearsal|exam)$/.test(path)) return 'stage';
  if (/^\/takes\/[^/]+\/processing$/.test(path)) return 'processing-loading';
  if (/^\/takes\/[^/]+\/confirm$/.test(path)) return 'confirm';
  if (/^\/takes\/[^/]+\/retry$/.test(path)) return 'retry';
  if (/^\/takes\/[^/]+$/.test(path)) return 'report';
  if (path === '/takes') return 'takes';
  if (path === '/about') return 'about';
  if (path === '/unsupported') return 'unsupported';
  if (path === '/me') return 'me';
  if (path === '/welcome') return 'welcome';
  if (path.startsWith('/login')) return 'login';
  return null;
}

/** Link의 실제 href가 아니라 Router가 만든 경로를 읽는다(HashRouter면 # 뒤) */
function routeFromHref(href: string): string {
  const url = new URL(href, window.location.href);
  if (url.origin !== window.location.origin) return '';
  return url.hash.startsWith('#/') ? url.hash.slice(1).split('?')[0]! : url.pathname;
}

export function ScreenPreview({
  screenId,
  stateId,
  onNavigate,
  inline = false,
}: {
  screenId: string;
  stateId: string;
  onNavigate: Navigate;
  /** 전체 보기처럼 미리보기를 여럿 나란히 둘 때 true. 창(dialog)이 페이지를 막지 않게 연다 */
  inline?: boolean;
}) {
  const [unavailable, setUnavailable] = useState(false);

  function containNavigation(event: MouseEvent<HTMLDivElement>) {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const anchor = target.closest('a[href]');
    if (!(anchor instanceof HTMLAnchorElement)) return;
    event.preventDefault();
    event.stopPropagation();
    const destination = targetFor(routeFromHref(anchor.href));
    if (destination) {
      setUnavailable(false);
      onNavigate(destination);
    } else {
      setUnavailable(true);
    }
  }

  return (
    <div
      onClickCapture={containNavigation}
      onAuxClickCapture={(event) => {
        // 가운데 클릭으로 새 탭에 열리는 것도 막는다
        if (
          event.button === 1 &&
          event.target instanceof Element &&
          event.target.closest('a[href]')
        ) {
          event.preventDefault();
          event.stopPropagation();
        }
      }}
    >
      {unavailable && (
        <p role="status" className="border-2 border-ink bg-panel p-3 text-sm text-ink">
          이 링크는 설계서에서 연결하지 않은 화면이에요. 왼쪽 목록에서 화면을 골라 주세요.
        </p>
      )}
      {screenId in APP_HEADER ? (
        <div className="flex min-h-dvh flex-col">
          <AppHeader current={APP_HEADER[screenId] ?? undefined} />
          <div className="flex min-h-0 flex-1 flex-col">
            <CurrentScreen
              screenId={screenId}
              stateId={stateId}
              onNavigate={onNavigate}
              inline={inline}
            />
          </div>
        </div>
      ) : (
        <CurrentScreen
          screenId={screenId}
          stateId={stateId}
          onNavigate={onNavigate}
          inline={inline}
        />
      )}
    </div>
  );
}

function CurrentScreen({
  screenId,
  stateId,
  onNavigate,
  inline,
}: {
  screenId: string;
  stateId: string;
  onNavigate: Navigate;
  inline: boolean;
}) {
  switch (screenId) {
    case 'colors':
      return <ColorsFoundation stateId={stateId} />;
    case 'typography':
      return <TypographyFoundation stateId={stateId} />;
    case 'spacing':
      return <SpacingFoundation />;
    case 'surfaces':
      return <SurfacesFoundation />;
    case 'responsive':
      return <ResponsiveFoundation />;
    case 'writing':
      return <WritingFoundation stateId={stateId} />;
    case 'home':
      return <HomeScreen stateId={stateId} onNavigate={onNavigate} />;
    case 'pitch-create-a':
      return <CreateConceptScreen stateId={stateId} onNavigate={onNavigate} inline={inline} />;
    case 'pitch-create':
      return <CreateScreen stateId={stateId} onNavigate={onNavigate} inline={inline} />;
    case 'prepare-mission':
      return <PrepareScreen stateId={stateId} onNavigate={onNavigate} />;
    case 'stage':
      return <StageScreen stateId={stateId} onNavigate={onNavigate} />;
    case 'processing-loading':
      return <ProcessingScreen stateId={stateId} onNavigate={onNavigate} />;
    case 'confirm':
      return <ConfirmScreen stateId={stateId} onNavigate={onNavigate} />;
    case 'retry':
      return <RetryScreen stateId={stateId} onNavigate={onNavigate} />;
    case 'report':
      return <ReportScreen stateId={stateId} onNavigate={onNavigate} />;
    case 'takes':
      return <TakesScreen stateId={stateId} onNavigate={onNavigate} />;
    case 'comparison':
      return <ComparisonScreen stateId={stateId} onNavigate={onNavigate} />;
    case 'about':
      return <AboutScreen stateId={stateId} onNavigate={onNavigate} />;
    case 'unsupported':
      return <UnsupportedScreen stateId={stateId} onNavigate={onNavigate} />;
    case 'not-found':
      return <NotFoundScreen stateId={stateId} onNavigate={onNavigate} />;
    case 'welcome':
      return <WelcomeScreen stateId={stateId} onNavigate={onNavigate} />;
    case 'login':
      return <LoginScreen stateId={stateId} onNavigate={onNavigate} />;
    case 'stage-blocked':
      return <StageBlockedScreen stateId={stateId} onNavigate={onNavigate} />;
    case 'me':
      return <MyScreen stateId={stateId} onNavigate={onNavigate} />;
    case 'slide-preview':
      // 피치 생성 샘플 안의 그 창을 그대로 보인다
      return <CreateScreen stateId="preview" onNavigate={onNavigate} inline={inline} />;
    default:
      if (componentEntries.some((entry) => entry.id === screenId))
        return <ComponentDoc id={screenId} stateId={stateId} />;
      return (
        <Frame title="화면을 찾을 수 없어요">
          <p>목록에서 확인할 화면을 골라 주세요.</p>
        </Frame>
      );
  }
}
