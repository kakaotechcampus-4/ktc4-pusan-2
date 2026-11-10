import { expect, it } from 'vitest';
import { metricLabel } from './metricLabels';

it('리뷰 비교의 기존 점수는 내용 전달 카드의 "핵심 내용 전달률"과 이름이 겹치지 않습니다 (#1154 5절)', () => {
  expect(metricLabel('content_coverage')).toBe('핵심 내용 점수(기존 방식)');
  expect(metricLabel('content_coverage')).not.toContain('전달');
});
