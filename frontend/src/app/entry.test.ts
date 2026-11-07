import { describe, expect, it } from 'vitest';
import entry from '../main.tsx?raw';

/**
 * 앱 진입점이 늘 해야 하는 일 — 앱 전체를 띄우지 않고 원문으로 봅니다.
 * 동작 자체(로그아웃 신호로 사본 · 메모리를 비움)는 `features/chat/chatStore.test.ts` 가 봅니다
 */
describe('앱 진입점', () => {
  it('화면을 그리기 전에 대화 에이전트를 로그아웃 신호에 묶는다 — 대화창이 없는 화면의 로그아웃에도 사본을 지우게 (#952 리뷰)', () => {
    const bind = entry.indexOf('bindChatToSession();');
    expect(bind).toBeGreaterThan(-1);
    expect(bind).toBeLessThan(entry.indexOf('createRoot('));
    // 조건 · 컴포넌트 안이 아니라 모듈 맨 바깥에서 부릅니다
    expect(entry.slice(0, bind).includes('function boot')).toBe(false);
  });
});
