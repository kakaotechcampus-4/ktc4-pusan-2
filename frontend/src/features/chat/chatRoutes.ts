import { matchPath } from 'react-router';

/** 정상 런처와 오류 안내가 공유하는 고정 위치. */
export const CHAT_LAUNCHER_CLASS =
  'light-surface fixed bottom-[var(--chat-launcher-bottom)] left-2 z-[70] text-ink';

/**
 * Explicit allowlist for the floating chat. Live rehearsal is intentionally
 * excluded; adding a new route requires an explicit decision rather than
 * exposing chat by accident.
 * 패턴은 `app/routes.tsx` 의 경로 문자열 그대로입니다. 피치 만들기 · 수정 · 장치 점검은 화면 상태를 함께 보내고,
 * 연습 준비로 옮겨 가도 대화가 이어져야 해서 둡니다 (`docs/ai/features/chat-agent.md` 12-4 F1 · F2)
 */
const chatRoutePatterns = [
  '/',
  '/pitches',
  '/pitch/new',
  '/pitch/:id/edit',
  '/pitch/:pitchId/device-check',
  '/takes',
  '/me',
  '/about',
  '/takes/:takeId',
  '/takes/:takeId/processing',
  '/takes/:takeId/confirm',
  '/takes/:takeId/retry',
];

export function canShowChat(pathname: string): boolean {
  return chatRoutePatterns.some((pattern) => matchPath(pattern, pathname));
}

/**
 * 지금 화면 — 보내기의 `page` 와 처음 범위의 바탕입니다 (12-4 F2 "처음 범위" 표 · "메시지를 보낼 때마다 지금 화면 `page`").
 * - `route` 는 경로 패턴 그대로(`ChatPage.route`), 실제 ID 는 `pitchId` · `takeId` 로 따로 둡니다.
 * - `pitchId` 는 화면이 바로 아는 피치 — 피치 수정 · 장치 점검은 경로의 ID, Take 기록은 `?pitch=`.
 *   리포트와 그 아래 화면(`/takes/:takeId/…`)은 Take 를 조회해야 알아서 여기서는 null 이고 `needsTake` 가 true 입니다.
 * 채팅을 띄우지 않는 화면이면 null
 */
export interface ChatRouteInfo {
  route: string;
  pitchId: string | null;
  takeId: string | null;
  /** 그 Take 의 피치를 조회해야 범위를 압니다 — 그 전에는 "범위 확인 중" 입니다 */
  needsTake: boolean;
}

/** 경로에 든 값이 ID 로 쓸 만한지 — 빈 값 · 경로 문자가 섞인 값은 버립니다 */
const usableId = (v: string | null | undefined): string | null =>
  v && /^[A-Za-z0-9-]+$/.test(v) ? v : null;

export function chatRouteOf(pathname: string, search = ''): ChatRouteInfo | null {
  for (const route of chatRoutePatterns) {
    const match = matchPath(route, pathname);
    if (!match) continue;
    const takeId = usableId(match.params.takeId);
    if (route === '/takes')
      return {
        route,
        pitchId: usableId(new URLSearchParams(search).get('pitch')),
        takeId: null,
        needsTake: false,
      };
    if (takeId) return { route, pitchId: null, takeId, needsTake: true };
    return {
      route,
      pitchId: usableId(match.params.id ?? match.params.pitchId),
      takeId: null,
      needsTake: false,
    };
  }
  return null;
}

/**
 * 왼쪽 아래 런처의 아래 여백(px). 기본 32px 는 홈의 "사용방법 살펴보기"와 바닥글 사이 빈칸입니다.
 * 하단 고정 바가 있는 화면에서만 올립니다 (1366×650 · 1920×950 · 125% 확대 기준, #941).
 */
const launcherLifts: [pattern: string, bottom: number][] = [
  // 피치 만들기 · 수정: 아래 고정 바(79px)보다 위에 둡니다 (12-4 F1 "겹침")
  ['/pitch/new', 88],
  ['/pitch/:id/edit', 88],
];

export function chatLauncherBottom(pathname: string): number {
  return launcherLifts.find(([pattern]) => matchPath(pattern, pathname))?.[1] ?? 32;
}
