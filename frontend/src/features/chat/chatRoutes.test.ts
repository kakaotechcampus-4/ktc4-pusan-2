import { describe, expect, it } from 'vitest';
import { canShowChat, chatLauncherBottom, chatRouteOf } from './chatRoutes';

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
    '/me',
    '/about',
  ])('allows chat on %s', (path) => {
    expect(canShowChat(path)).toBe(true);
  });

  it.each([
    '/login',
    '/welcome',
    '/pitch/p1/prepare',
    '/pitch/p1/comparison',
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

describe('지금 화면 (12-4 F2)', () => {
  it.each([
    ['/', '', { route: '/', pitchId: null, takeId: null, needsTake: false }],
    ['/pitch/new', '', { route: '/pitch/new', pitchId: null, takeId: null, needsTake: false }],
    [
      '/pitch/p1/edit',
      '',
      { route: '/pitch/:id/edit', pitchId: 'p1', takeId: null, needsTake: false },
    ],
    [
      '/pitch/p1/device-check',
      '',
      { route: '/pitch/:pitchId/device-check', pitchId: 'p1', takeId: null, needsTake: false },
    ],
    [
      '/pitch/p1/comparison',
      '',
      { route: '/pitch/:id/comparison', pitchId: 'p1', takeId: null, needsTake: false },
    ],
    ['/takes', '', { route: '/takes', pitchId: null, takeId: null, needsTake: false }],
    ['/takes', '?pitch=p2', { route: '/takes', pitchId: 'p2', takeId: null, needsTake: false }],
    ['/takes/t1', '', { route: '/takes/:takeId', pitchId: null, takeId: 't1', needsTake: true }],
    [
      '/takes/t1/processing',
      '',
      { route: '/takes/:takeId/processing', pitchId: null, takeId: 't1', needsTake: true },
    ],
  ])('%s%s → 경로 패턴과 화면이 아는 ID', (path, search, info) => {
    expect(chatRouteOf(path, search)).toEqual(info);
  });

  it('채팅을 띄우지 않는 화면은 null', () => {
    expect(chatRouteOf('/takes/t1/rehearsal')).toBeNull();
    expect(chatRouteOf('/welcome')).toBeNull();
  });

  it('ID 로 쓸 수 없는 값은 버린다 — 빈 값 · 경로 문자', () => {
    expect(chatRouteOf('/takes', '?pitch=')!.pitchId).toBeNull();
    expect(chatRouteOf('/takes', '?pitch=a%2Fb')!.pitchId).toBeNull();
    expect(chatRouteOf('/pitch/a%2Fb/edit')!.pitchId).toBeNull();
  });
});
