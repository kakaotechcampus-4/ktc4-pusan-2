import { describe, expect, it } from 'vitest';
import { GUIDE_GROUP_ORDER, entries, screens } from './catalog';

describe('화면 설계서 목록', () => {
  it('화면 ID가 중복되지 않고 기존 공유 주소를 유지한다', () => {
    const ids = entries.map((entry) => entry.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(screens[0]?.id).toBe('home');
    for (const id of [
      'pitch-create-a',
      'prepare-mission',
      'prepare-layout-review',
      'processing-loading',
      'comparison',
    ]) {
      expect(ids).toContain(id);
    }
  });

  it('모든 항목이 정해진 그룹에 속한다', () => {
    expect(new Set(GUIDE_GROUP_ORDER).size).toBe(GUIDE_GROUP_ORDER.length);
    for (const entry of entries) {
      expect(GUIDE_GROUP_ORDER).toContain(entry.group);
    }
  });

  it('검토 시안은 제품 샘플과 다른 그룹에 둔다', () => {
    for (const screen of screens) {
      if (screen.status === '시안') {
        expect(screen.group).toBe('검토 시안');
      } else {
        expect(screen.group).not.toBe('검토 시안');
      }
    }
  });
});
