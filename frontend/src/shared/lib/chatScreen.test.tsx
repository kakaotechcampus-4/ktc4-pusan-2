// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  MAX_SCREEN_CRITERIA_CHARS,
  isScreenBody,
  registerChatScreen,
  resetChatScreen,
  takeScreenSnapshot,
  useChatScreen,
  type ScreenBody,
} from './chatScreen';
import { announceSignedOut } from './sessionSignal';

const device: ScreenBody = {
  name: 'device_check',
  camera: 'connected',
  mic: 'detected',
  gaze_setup: 'IDLE',
  gaze_reason: null,
};
const pitch = (criteria: string | null): ScreenBody => ({
  name: 'pitch_create',
  step: 'criteria',
  version_label: 'V1',
  version_id: null,
  saved: null,
  draft: {
    title: '발표',
    slide_count: 3,
    script_chars: 100,
    time_limit_sec: 300,
    criteria_text: criteria,
  },
  changed: [],
});

const ROUTE = '/pitch/:pitchId/device-check';

afterEach(() => resetChatScreen());

describe('화면 상태 등록 · 스냅숏 (12-4 F2)', () => {
  it('보내는 순간에 읽고 snapshot_id 를 붙인다 — 보낼 때마다 새 값', () => {
    const read = vi.fn(() => device);
    registerChatScreen(ROUTE, read);
    expect(read).not.toHaveBeenCalled();
    const a = takeScreenSnapshot(ROUTE);
    const b = takeScreenSnapshot(ROUTE);
    expect(read).toHaveBeenCalledTimes(2);
    expect(a).toMatchObject({
      ok: true,
      screen: { ...device, snapshot_id: expect.stringMatching(/^ss-[0-9a-f]{16}$/) },
    });
    expect(a.ok && b.ok && a.screen!.snapshot_id !== b.screen!.snapshot_id).toBe(true);
  });

  it('등록된 화면이 없거나 · 다른 경로면 화면 상태 없이 보낸다', () => {
    expect(takeScreenSnapshot(ROUTE)).toEqual({ ok: true, screen: null });
    registerChatScreen(ROUTE, () => device);
    expect(takeScreenSnapshot('/')).toEqual({ ok: true, screen: null });
  });

  it('푼 화면 · 뒤에 등록한 화면 — 푸는 함수는 자기 등록만 푼다', () => {
    const offA = registerChatScreen(ROUTE, () => device);
    const offB = registerChatScreen('/pitch/new', () => pitch(null));
    offA(); // 앞 화면의 늦은 해제가 뒤 화면을 지우지 않습니다
    expect(takeScreenSnapshot('/pitch/new')).toMatchObject({
      ok: true,
      screen: { name: 'pitch_create' },
    });
    offB();
    expect(takeScreenSnapshot('/pitch/new')).toEqual({ ok: true, screen: null });
  });

  it('로그아웃 신호 뒤에는 앞 사람 때 등록한 화면 상태를 싣지 않는다', () => {
    registerChatScreen(ROUTE, () => device);
    announceSignedOut();
    expect(takeScreenSnapshot(ROUTE)).toEqual({ ok: true, screen: null });
  });

  it('읽다가 실패하거나 계약 모양이 아니면 화면 상태 없이 보낸다', () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    registerChatScreen(ROUTE, () => {
      throw new Error('화면 쪽 실수');
    });
    expect(takeScreenSnapshot(ROUTE)).toEqual({ ok: true, screen: null });
    expect(errors).toHaveBeenCalled();
    errors.mockRestore();
    const warns = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    registerChatScreen(ROUTE, () => ({ ...device, deviceId: 'cam-1' }) as unknown as ScreenBody);
    expect(takeScreenSnapshot(ROUTE)).toEqual({ ok: true, screen: null });
    registerChatScreen(ROUTE, () => ({ ...device, mic: 'loud' }) as unknown as ScreenBody);
    expect(takeScreenSnapshot(ROUTE)).toEqual({ ok: true, screen: null });
    // 어느 화면인지만 남기고 내용(사용자 입력)은 남기지 않습니다
    expect(warns).toHaveBeenCalledWith(expect.any(String), { route: ROUTE });
    warns.mockRestore();
  });

  it('평가 기준 원문이 2,000자(코드 포인트)를 넘으면 자르지 않고 보내지 않게 알린다', () => {
    registerChatScreen('/pitch/new', () => pitch('가'.repeat(MAX_SCREEN_CRITERIA_CHARS)));
    expect(takeScreenSnapshot('/pitch/new')).toMatchObject({ ok: true });
    // 이모지는 UTF-16 으로 2자지만 코드 포인트로는 1자 — 2,000개는 넘지 않습니다
    registerChatScreen('/pitch/new', () => pitch('👍'.repeat(MAX_SCREEN_CRITERIA_CHARS)));
    expect(takeScreenSnapshot('/pitch/new')).toMatchObject({ ok: true });
    registerChatScreen('/pitch/new', () => pitch('가'.repeat(MAX_SCREEN_CRITERIA_CHARS + 1)));
    expect(takeScreenSnapshot('/pitch/new')).toEqual({ ok: false, reason: 'CRITERIA_TOO_LONG' });
  });

  it('검사 — 칸이 빠지거나 더 있거나 값이 틀리면 거절한다', () => {
    expect(isScreenBody(pitch('원문'))).toBe(true);
    expect(isScreenBody({ ...pitch(null), changed: ['title', 'title'] })).toBe(false);
    expect(isScreenBody({ ...pitch(null), step: 'done' })).toBe(false);
    const { version_id: _drop, ...missing } = pitch(null) as Extract<
      ScreenBody,
      { name: 'pitch_create' | 'pitch_edit' }
    >;
    expect(isScreenBody(missing)).toBe(false);
    expect(isScreenBody({ ...device, gaze_reason: 3 })).toBe(false);
  });
});

describe('useChatScreen', () => {
  afterEach(() => vi.unstubAllEnvs());

  it('그리면 등록하고 마지막으로 그린 때의 값을 읽으며, 사라지면 푼다', () => {
    vi.stubEnv('VITE_CHAT_AGENT', 'true');
    const host = document.createElement('div');
    const root = createRoot(host);
    function Screen({ mic }: { mic: 'checking' | 'detected' }) {
      useChatScreen(ROUTE, () => ({ ...device, mic }));
      return null;
    }
    act(() => root.render(<Screen mic="checking" />));
    expect(takeScreenSnapshot(ROUTE)).toMatchObject({ screen: { mic: 'checking' } });
    act(() => root.render(<Screen mic="detected" />));
    expect(takeScreenSnapshot(ROUTE)).toMatchObject({ screen: { mic: 'detected' } });
    act(() => root.unmount());
    expect(takeScreenSnapshot(ROUTE)).toEqual({ ok: true, screen: null });
  });

  it('대화 빌드 스위치가 꺼져 있으면 등록하지 않는다', () => {
    vi.stubEnv('VITE_CHAT_AGENT', 'false');
    const host = document.createElement('div');
    const root = createRoot(host);
    function Screen() {
      useChatScreen(ROUTE, () => device);
      return null;
    }
    act(() => root.render(<Screen />));
    expect(takeScreenSnapshot(ROUTE)).toEqual({ ok: true, screen: null });
    act(() => root.unmount());
  });
});
