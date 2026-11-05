/**
 * Explicit allowlist for the floating chat. Presentation preparation and
 * live rehearsal are intentionally excluded; adding a new route requires an
 * explicit decision rather than exposing chat by accident.
 */
const staticPaths = new Set(['/', '/pitches', '/takes', '/me', '/about']);

const dynamicPaths = [
  /^\/takes\/[^/]+$/,
  /^\/takes\/[^/]+\/(?:processing|confirm|retry)$/,
  /^\/pitch\/[^/]+\/comparison$/,
];

export function canShowChat(pathname: string): boolean {
  return staticPaths.has(pathname) || dynamicPaths.some((route) => route.test(pathname));
}
