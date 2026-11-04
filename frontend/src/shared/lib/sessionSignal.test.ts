import { afterEach, describe, expect, it } from 'vitest';
import { announceSignedOut, onSignedOut } from './sessionSignal';

const settle = () => new Promise((resolve) => setTimeout(resolve, 30));
const offs: (() => void)[] = [];
afterEach(() => offs.splice(0).forEach((off) => off()));

describe('로그아웃 신호', () => {
  it('이 탭의 듣는 쪽은 바로 한 번만 부른다 — 채널로 되돌아온 자기 메시지는 버린다', async () => {
    let calls = 0;
    offs.push(onSignedOut(() => calls++));
    announceSignedOut();
    expect(calls).toBe(1);
    await settle();
    expect(calls).toBe(1);
  });

  it('다른 탭이 보낸 신호를 받는다', async () => {
    let calls = 0;
    offs.push(onSignedOut(() => calls++));
    // 다른 탭: 같은 이름의 채널로 다른 탭 표시를 단 메시지
    const other = new BroadcastChannel('pitchcoach-session');
    other.postMessage({ type: 'signed-out', tab: 'other-tab' });
    other.close();
    await settle();
    expect(calls).toBe(1);
  });

  it('모르는 메시지는 무시하고, 그만 들으면 부르지 않는다', async () => {
    let calls = 0;
    const off = onSignedOut(() => calls++);
    const other = new BroadcastChannel('pitchcoach-session');
    other.postMessage('signed-out');
    await settle();
    off();
    announceSignedOut();
    other.postMessage({ type: 'signed-out', tab: 'other-tab' });
    other.close();
    await settle();
    expect(calls).toBe(0);
  });
});

describe('듣는 쪽이 실패해도', () => {
  it('나머지 듣는 쪽과 다른 탭 알림은 그대로 간다', async () => {
    let calls = 0;
    let otherTab = 0;
    const error = console.error;
    console.error = () => undefined;
    offs.push(
      onSignedOut(() => {
        throw new Error('boom');
      }),
    );
    offs.push(onSignedOut(() => calls++));
    const other = new BroadcastChannel('pitchcoach-session');
    other.onmessage = () => otherTab++;
    try {
      expect(() => announceSignedOut()).not.toThrow();
      await settle();
      expect([calls, otherTab]).toEqual([1, 1]);
    } finally {
      other.close();
      console.error = error;
    }
  });
});

describe('같은 함수로 두 번 들으면', () => {
  it('하나를 풀어도 다른 하나는 남는다', () => {
    let calls = 0;
    const listener = () => calls++;
    const first = onSignedOut(listener);
    offs.push(onSignedOut(listener));
    first();
    announceSignedOut();
    expect(calls).toBe(1);
  });
});
