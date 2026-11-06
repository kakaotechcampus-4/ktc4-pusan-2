import { describe, expect, it } from 'vitest';
import type { PracticeCombo } from '@/types/api';
import { deviceCheckLink } from './deviceCheckLink';

const practice: PracticeCombo = {
  title: '캡스톤',
  presentationVersionId: 'pv1',
  scriptVersionId: 'sv1',
  slideVersion: 1,
  scriptVersion: 1,
  goalTimeSec: 600,
};

describe('장치 점검으로 돌아가는 링크', () => {
  it('조합을 골라 시작한 Take 면 그 조합을 다시 넘긴다 — 서버 최신으로 바뀌지 않게', () => {
    expect(deviceCheckLink({ pitchId: 'p1', practice })).toEqual({
      to: '/pitch/p1/device-check',
      state: { practice },
    });
  });

  it('홈에서 바로 시작했거나(null) 이 칸 전에 저장된 세션(undefined)이면 넘기지 않는다', () => {
    expect(deviceCheckLink({ pitchId: 'p1', practice: null }).state).toBeUndefined();
    expect(deviceCheckLink({ pitchId: 'p1' }).state).toBeUndefined();
  });
});
