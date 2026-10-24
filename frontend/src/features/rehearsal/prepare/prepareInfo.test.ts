import { describe, expect, it } from 'vitest';
import type { PracticeCombo, PrepareResponse } from '@/types/api';
import { fromPrepare, newerScriptNotice, prepareProblem, takeChoice } from './prepareInfo';

/** BE #296 · #298 `PrepareDTO` 모양 그대로 */
const res: PrepareResponse = {
  pitch_id: 'p1',
  title: '캡스톤',
  time_limit_sec: 600,
  presentation: { id: 'pv2', version: 2, page_count: 12 },
  script: { id: 'sv2', version: 2, slide_count: 12 },
  newer_script: null,
  next_take_number: 4,
  default_script_mode: 'KEYWORD',
};

const combo: PracticeCombo = {
  title: '캡스톤',
  presentationVersionId: 'pv1',
  scriptVersionId: 'sv3',
  slideVersion: 1,
  scriptVersion: 3,
  goalTimeSec: 300,
};

describe('장치 점검 준비 정보 — BE GET /prepare', () => {
  it('응답을 화면 모양으로 옮긴다', () => {
    expect(fromPrepare(res)).toEqual({
      title: '캡스톤',
      timeLimitSec: 600,
      presentation: { id: 'pv2', version: 2, pageCount: 12 },
      script: { id: 'sv2', version: 2, slideCount: 12 },
      newerScript: null,
      nextTakeNumber: 4,
      defaultScriptMode: 'KEYWORD',
    });
  });

  it('#296 만 머지돼 쪽수 · 장 수가 없으면 모르는 것(null)으로 둔다', () => {
    const info = fromPrepare({
      ...res,
      presentation: { id: 'pv2', version: 2 },
      script: { id: 'sv2', version: 2 },
    });
    expect(info.presentation?.pageCount).toBeNull();
    expect(info.script?.slideCount).toBeNull();
    // 모르는 것은 다르다고 막지 않습니다
    expect(prepareProblem(info)).toBeNull();
  });

  it('발표자료나 대본이 없으면 시작할 수 없다', () => {
    expect(prepareProblem(fromPrepare({ ...res, presentation: null }))).toContain(
      '발표자료가 없어요',
    );
    expect(prepareProblem(fromPrepare({ ...res, script: null }))).toContain('대본이 없어요');
    expect(
      prepareProblem(
        fromPrepare({
          ...res,
          script: null,
          newer_script: { id: 'sv3', version: 3, parse_status: 'PENDING', error_code: null },
        }),
      ),
    ).toContain('나누는 중');
  });

  it('쪽수와 장 수를 둘 다 알고 다르면 시작할 수 없다', () => {
    const info = fromPrepare({ ...res, presentation: { id: 'pv2', version: 2, page_count: 10 } });
    expect(prepareProblem(info)).toBe(
      '발표자료(10쪽)와 대본(12장)의 장 수가 달라요. 피치에서 대본 매핑을 다시 확인해 주세요.',
    );
    expect(takeChoice(null, info)).toBeNull();
  });

  it('새 대본을 아직 못 쓰면 옛 대본으로 연습한다고 알린다', () => {
    const pending = { id: 'sv3', version: 3, error_code: null } as const;
    expect(
      newerScriptNotice(
        fromPrepare({ ...res, newer_script: { ...pending, parse_status: 'PENDING' } }),
      ),
    ).toBe('새 대본(v3)은 아직 나누는 중이라 지난 대본(v2)으로 연습해요');
    expect(
      newerScriptNotice(
        fromPrepare({ ...res, newer_script: { ...pending, parse_status: 'FAILED' } }),
      ),
    ).toBe('새 대본(v3)은 나누지 못해서 지난 대본(v2)으로 연습해요');
    expect(newerScriptNotice(fromPrepare(res))).toBeNull();
  });
});

describe('이번 연습의 조합', () => {
  it('피치 생성에서 고른 조합이 먼저다', () => {
    expect(takeChoice(combo, fromPrepare(res))).toEqual({
      presentationVersionId: 'pv1',
      scriptVersionId: 'sv3',
      timeLimitSec: 300,
      label: '자료 v1 · 대본 v3',
    });
  });

  it('조합이 없으면 준비 정보로 정하고, 리허설 제한 시간은 발표 제한 시간이다', () => {
    expect(takeChoice(null, fromPrepare(res))).toEqual({
      presentationVersionId: 'pv2',
      scriptVersionId: 'sv2',
      timeLimitSec: 600,
      label: '자료 v2 · 대본 v2',
    });
  });

  it('둘 다 없으면 Take 를 만들 수 없다', () => {
    expect(takeChoice(null, null)).toBeNull();
  });
});
