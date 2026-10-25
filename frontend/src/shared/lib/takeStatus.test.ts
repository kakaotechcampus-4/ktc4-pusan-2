import { describe, expect, it } from 'vitest';
import { takeStatusView } from './takeStatus';

describe('takeStatusView', () => {
  it('완료는 리포트로, 분석 중은 진행 화면으로 보낸다', () => {
    expect(takeStatusView('t 1', 'COMPLETED', 'SILENCE')).toMatchObject({
      label: '완료',
      link: { to: '/takes/t%201', text: '리포트' },
    });
    expect(takeStatusView('t1', 'ANALYZING', null)).toMatchObject({
      label: '분석 중',
      link: { to: '/takes/t1/processing' },
    });
  });

  it('FAILED 는 끝내지 못한 경우만 중단, 나머지는 실패이고 둘 다 링크가 없다', () => {
    for (const reason of ['ABANDONED', 'CLOSED'] as const)
      expect(takeStatusView('t1', 'FAILED', reason)).toEqual({
        label: '중단',
        tone: 'muted',
        link: null,
      });
    for (const reason of ['SILENCE', 'TIME_LIMIT', null] as const)
      expect(takeStatusView('t1', 'FAILED', reason)).toMatchObject({ label: '실패', link: null });
  });

  it('시작 전 · 연습 중은 볼 화면이 없다', () => {
    expect(takeStatusView('t1', 'READY', null)).toMatchObject({ label: '시작 전', link: null });
    expect(takeStatusView('t1', 'RUNNING', null)).toMatchObject({ label: '연습 중', link: null });
  });
});
