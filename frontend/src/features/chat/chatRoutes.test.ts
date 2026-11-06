import { describe, expect, it } from 'vitest';
import { canShowChat, chatLauncherBottom } from './chatRoutes';

describe('chat route visibility', () => {
  it.each([
    '/',
    '/pitches',
    '/pitch/new',
    '/pitch/p1/edit',
    '/pitch/p1/device-check',
    '/takes',
    '/takes/123',
    '/takes/123/processing',
    '/takes/123/confirm',
    '/takes/123/retry',
    '/pitch/p1/comparison',
    '/me',
    '/about',
  ])('allows chat on %s', (path) => {
    expect(canShowChat(path)).toBe(true);
  });

  it.each([
    '/login',
    '/welcome',
    '/pitch/p1/prepare',
    '/takes/123/rehearsal',
    '/takes/123/exam',
    '/terms',
    '/privacy',
    '/unsupported',
    '/dev/stage',
    '/unknown',
  ])('keeps chat off %s', (path) => {
    expect(canShowChat(path)).toBe(false);
  });

  it.each([
    ['/', 32],
    ['/takes/123', 32],
    ['/pitch/new', 88],
    ['/pitch/p1/edit', 88],
    ['/pitch/p1/device-check', 32],
  ])('lifts the launcher on %s to %ipx', (path, bottom) => {
    expect(chatLauncherBottom(path)).toBe(bottom);
  });
});
