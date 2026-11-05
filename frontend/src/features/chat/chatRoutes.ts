import { matchPath } from 'react-router';

/**
 * Explicit allowlist for the floating chat. Presentation preparation and
 * live rehearsal are intentionally excluded; adding a new route requires an
 * explicit decision rather than exposing chat by accident.
 * 패턴은 `app/routes.tsx` 의 경로 문자열 그대로입니다.
 */
const chatPaths = [
  '/',
  '/pitches',
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
  return chatPaths.some((pattern) => matchPath(pattern, pathname));
}
