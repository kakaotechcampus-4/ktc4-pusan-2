import { describe, expect, it } from 'vitest';
import { SILENCE_END_NOTICE, autoEndState, autoEndText, readAutoEnd } from './autoEnd';

describe('readAutoEnd', () => {
  it('reads what the stage passed', () => {
    expect(readAutoEnd(autoEndState('SILENCE', '2분 넘게 말이 없어요.'))).toEqual({
      reason: 'SILENCE',
      message: '2분 넘게 말이 없어요.',
    });
  });

  it.each([
    ['no state', null],
    ['another screen state', { clientSessionId: 's1' }],
    ['a reason the server does not auto end with', { autoEnd: { reason: 'CLOSED', message: 'x' } }],
    ['a broken notice', { autoEnd: 'SILENCE' }],
  ])('ignores %s', (_name, state) => {
    expect(readAutoEnd(state)).toBeNull();
  });

  it('keeps the reason even if the message is missing', () => {
    expect(readAutoEnd({ autoEnd: { reason: 'TIME_LIMIT' } })).toEqual({
      reason: 'TIME_LIMIT',
      message: '',
    });
  });
});

describe('autoEndText', () => {
  it('shows the server message as is', () => {
    expect(autoEndText({ reason: 'SILENCE', message: '90초 넘게 말이 없어요.' }).message).toBe(
      '90초 넘게 말이 없어요.',
    );
  });

  it('falls back to its own words when the server sent none', () => {
    expect(autoEndText({ reason: 'SILENCE', message: ' ' }).message).toBe(
      '2분 넘게 말이 없어 연습을 자동으로 끝냈어요.',
    );
    expect(autoEndText({ reason: 'TIME_LIMIT', message: '' }).message).toBe(
      '연습은 최대 1시간까지라 자동으로 끝냈어요.',
    );
  });

  it('tells a silence end apart from the goal time', () => {
    expect(autoEndText({ reason: 'SILENCE', message: '' }).after).toContain('발표 목표 시간');
  });
});

// BE `take_silence_end_sec = 120` 과 같아야 합니다. 바꾸면 BE 와 같이 바꿉니다
it('warns before the take with the server silence limit and not the goal time', () => {
  expect(SILENCE_END_NOTICE).toContain('말이 없는 상태가 2분 넘게 이어지면');
  // 2분만으로 끝나지 않습니다(최근 5분 중 80% 조건도 봅니다). 단정하지 않습니다
  expect(SILENCE_END_NOTICE).toContain('끝날 수 있어요');
  expect(SILENCE_END_NOTICE).toContain('발표 목표 시간과는 상관없어요');
});
