import { matchPath } from 'react-router';

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
  '/pitch/:id/comparison',
];

export function canShowChat(pathname: string): boolean {
  return chatRoutePatterns.some((pattern) => matchPath(pattern, pathname));
}

/**
 * 런처의 아래 여백(px). 기본 32px 는 홈의 "사용방법 살펴보기"와 바닥글 사이 빈칸입니다.
 * 아래 고정 바나 오른쪽 끝까지 찬 컨트롤이 있는 화면만 올립니다 (1366×650 · 1920×950 · 125% 확대에서 잼, #941 리뷰)
 */
const launcherLifts: [pattern: string, bottom: number][] = [
  // 피치 만들기 · 수정: 아래 고정 바(79px)보다 위에 둡니다 (12-4 F1 "겹침")
  ['/pitch/new', 88],
  ['/pitch/:id/edit', 88],
  // 장치 점검은 올리지 않고 화면의 오른쪽 여백을 런처 자리만큼 넓힙니다 (index.css `[data-chat-launcher]`).
  // 오른쪽 열이 화면 끝까지 차서 어느 높이에 올려도 낮은 창 · 확대에서 무엇인가를 가리기 때문입니다
];

export function chatLauncherBottom(pathname: string): number {
  return launcherLifts.find(([pattern]) => matchPath(pattern, pathname))?.[1] ?? 32;
}
