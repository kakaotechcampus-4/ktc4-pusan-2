import { describe, expect, it } from 'vitest';
import { contentMeasured, contentStatusOf } from '@/mocks/contentCoverage';
import type { ContentStatus, FactSummary, Metric } from '@/types/report';
import {
  UNKNOWN_REASON,
  bandText,
  contentHeadline,
  estimatedOnlyLine,
  factsLine,
  featureVersion,
  metricReason,
  mixLabel,
  percentText,
  previousDelta,
  reasonText,
  reasonTexts,
  scopeLine,
  zeroText,
} from './contentFormat';

const metric = (value: number | null, reason_codes: string[] = []): Metric => ({
  value,
  state: value === null ? 'not_applicable' : 'ok',
  reason_codes,
  evaluated: 3,
  excluded: 0,
});

describe('이유 문구', () => {
  it('아는 코드는 표의 문구, 모르는 코드 · null 은 일반 문구', () => {
    expect(reasonText('NO_KEY_POINTS')).toBe('이 장에는 확인할 핵심 내용이 없어요');
    expect(reasonText('SOMETHING_NEW')).toBe(UNKNOWN_REASON);
    expect(reasonText(null)).toBe(UNKNOWN_REASON);
    // Object.prototype 의 이름도 코드로 오면 일반 문구입니다
    expect(reasonText('toString')).toBe(UNKNOWN_REASON);
  });

  it('장이 실패했으면 STT_INCOMPLETE 를 장 평가 실패로 말합니다', () => {
    expect(reasonText('STT_INCOMPLETE')).toContain('녹음된 부분만 봤어요');
    expect(reasonText('STT_INCOMPLETE', true)).toBe('음성 인식이 끊겨 이 장을 평가하지 못했어요');
  });

  it('겹치는 문구는 한 번만, 빼라는 코드는 뺍니다', () => {
    expect(
      reasonTexts(['NOT_VALIDATED', 'OFFSCRIPT_DISABLED', 'SLIDES_EXCLUDED'], ['SLIDES_EXCLUDED']),
    ).toEqual(['대본 밖 말은 세부 구분 없이 합쳐 보여요']);
  });

  it('값이 없는 지표는 SLIDES_EXCLUDED 보다 자기 이유를 먼저 보입니다', () => {
    expect(metricReason(metric(null, ['SLIDES_EXCLUDED', 'NO_KEY_POINTS']))).toBe(
      '이 장에는 확인할 핵심 내용이 없어요',
    );
    expect(metricReason(metric(null, ['SLIDES_EXCLUDED']))).toBe(
      '평가하지 못한 장을 빼고 계산했어요',
    );
    expect(metricReason(null)).toBe(UNKNOWN_REASON);
  });
});

describe('숫자', () => {
  it('null 은 0% 가 아니라 —', () => {
    expect(percentText(null)).toBe('—');
    expect(percentText(0)).toBe('0%');
    expect(percentText(0.333)).toBe('33%');
  });

  it('0 의 설명은 전달률이 0 일 때만', () => {
    expect(zeroText(metric(0), 'all_missing')).toBe('핵심 내용을 모두 빠뜨렸어요');
    expect(zeroText(metric(0.2), 'all_missing')).toBeNull();
    expect(zeroText(metric(null), 'all_missing')).toBeNull();
    expect(zeroText(metric(0), 'new_kind')).toBeNull();
  });

  it('간이 단계와 범주 이름 — 모르는 값은 일반 이름', () => {
    expect(bandText('about_half')).toBe('절반쯤 말함(간이)');
    expect(bandText(null)).toBeNull();
    expect(bandText('new_band')).toBeNull();
    expect(mixLabel('procedural')).toBe('진행 말');
    expect(mixLabel('new_category')).toBe('그 밖의 말');
  });
});

describe('상태 · 범위', () => {
  it('상태 넷과 null', () => {
    expect(contentHeadline(null)).toBe('대본 전달도 상세 결과를 아직 제공하지 않아요');
    expect(contentHeadline(contentStatusOf('t-content-analyzing', null))).toBe(
      '대본 전달도를 분석하고 있어요',
    );
    expect(contentHeadline(contentStatusOf('t-content-legacy', null))).toBe(
      '이 Take는 자세한 결과를 지원하기 전에 분석됐어요',
    );
    expect(contentHeadline(contentStatusOf('t-content-failed', null))).toBe(
      '분석이 시간 안에 끝나지 않았어요',
    );
    expect(contentHeadline(contentMeasured)).toBeNull();
    expect(
      contentHeadline({ ...contentMeasured, state: 'NEW_STATE' } as unknown as ContentStatus),
    ).toBe(UNKNOWN_REASON);
  });

  it('범위와 간이 장만 있을 때의 문구', () => {
    expect(scopeLine(contentMeasured.summary!.scope)).toBe('2장 중 1장 평가 · 1장 간이');
    const estimated = contentStatusOf('t-content-estimated', null)!.summary!;
    expect(estimatedOnlyLine(estimated.slides)).toBe('간이: 대부분 말함 1장');
  });
});

describe('지난 Take 대비', () => {
  it('기능 버전이 같고 양쪽이 숫자면 %p 를 냅니다', () => {
    expect(featureVersion('script_coverage-1.2+abc')).toBe('script_coverage-1.2');
    // 0.5 - 0.42 = 8%p, 이번 Take 에서 장이 빠졌습니다
    expect(previousDelta(contentMeasured)).toEqual({ takeNumber: 2, delta: 8, partial: true });
  });

  it('기능 버전이 다르거나 한쪽이 null 이면 비교하지 않습니다', () => {
    const prev = contentMeasured.previous!;
    expect(
      previousDelta({
        ...contentMeasured,
        previous: { ...prev, criteria_version: 'script_coverage-1.1+x' },
      }),
    ).toBeNull();
    expect(
      previousDelta({ ...contentMeasured, previous: { ...prev, delivery: metric(null) } }),
    ).toBeNull();
    expect(previousDelta({ ...contentMeasured, previous: null })).toBeNull();
    expect(previousDelta(contentStatusOf('t-content-estimated', null)!)).toBeNull();
  });
});

describe('수치 정확도 한 줄', () => {
  const facts = (over: Partial<FactSummary>): FactSummary => ({
    matched: 2,
    approximate: 0,
    mismatched: 0,
    missing: 0,
    pending: 0,
    accuracy: metric(1),
    accuracy_range: null,
    ...over,
  });

  it('수치가 0개면 비율을 그리지 않습니다', () => {
    expect(factsLine(facts({ matched: 0 }))).toEqual({ kind: 'none' });
  });

  it('확인 대기가 있으면 확인한 개수 기준으로', () => {
    expect(factsLine(contentMeasured.summary!.facts)).toEqual({
      kind: 'measured',
      text: '확인한 2개 중 2개 맞음 · 1개 확인 대기',
    });
  });

  it('모두 확인 대기면 확인 전 추정', () => {
    expect(factsLine(contentStatusOf('t-content-zero', null)!.summary!.facts)).toEqual({
      kind: 'estimate',
      text: '확인 전 추정 67%',
    });
  });

  it('대기가 없으면 정확도', () => {
    expect(factsLine(facts({ accuracy: metric(0.5) }))).toEqual({
      kind: 'measured',
      text: '정확도 50%',
    });
  });
});
