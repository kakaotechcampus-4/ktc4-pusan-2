import { describe, expect, it } from 'vitest';
import { contentMeasured, contentStatusOf } from '@/mocks/contentCoverage';
import type { ContentStatus, FactSummary, Metric, OffscriptSpan } from '@/types/report';
import {
  UNKNOWN_REASON,
  bandText,
  contentHeadline,
  estimatedOnlyLine,
  factsLine,
  featureVersion,
  metricReason,
  NO_EVALUATED_SLIDES,
  mixLabel,
  percentText,
  previousDelta,
  reasonText,
  reasonTexts,
  scopeLine,
  sentenceStatusLabel,
  splitMarks,
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
    expect(reasonText('STT_INCOMPLETE', 'failedSlide')).toBe(
      '음성 인식이 끊겨 이 장을 평가하지 못했어요',
    );
  });

  it('Take 전체 자리에서는 "이 장" 문구를 쓰지 않습니다 (#1152 Take 지표는 장들의 이유를 합침)', () => {
    for (const code of ['NO_KEY_POINTS', 'NO_SPEECH', 'SENTENCE_FALLBACK', 'STT_INCOMPLETE'])
      expect(reasonText(code, 'take')).not.toContain('이 장');
    expect(reasonText('SENTENCE_FALLBACK', 'take')).toBe(
      '일부 장은 문장 단위로 평가했어요(참고용)',
    );
    // 장 자리는 문구표 그대로
    expect(reasonText('SENTENCE_FALLBACK')).toBe('이 장은 문장 단위로 평가했어요(참고용)');
    // Take 자리에 따로 정하지 않은 코드는 문구표 그대로
    expect(reasonText('FACTS_PENDING', 'take')).toBe(reasonText('FACTS_PENDING'));
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
    // SLIDES_EXCLUDED 만 있으면 평가한 장이 없어 값을 못 낸 것 — 값이 없는데 "빼고 계산했어요"라고 하지 않습니다
    expect(metricReason(metric(null, ['SLIDES_EXCLUDED']))).toBe(NO_EVALUATED_SLIDES);
    expect(metricReason(metric(null, ['SLIDES_EXCLUDED']), 'take')).toBe(NO_EVALUATED_SLIDES);
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
    expect(bandText('about_half')).toBe('절반쯤 말함');
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
    expect(estimatedOnlyLine(estimated.slides)).toBe('대부분 말함 1장');
  });

  it.each([
    [
      { target: 10, scored: 7, estimated: 1, provisional: 2 },
      '10장 중 7장 평가 · 1장 간이 · 2장 미평가',
    ],
    // 잠정 장은 채점된 장에 들어 있어 미평가로 세지 않습니다
    [{ target: 6, scored: 5, estimated: 0, provisional: 1 }, '6장 중 5장 평가 · 1장 미평가'],
    [{ target: 3, scored: 3, estimated: 0, provisional: 1 }, '3장 중 3장 평가'],
    [{ target: 0, scored: 0, estimated: 0, provisional: 0 }, '0장 중 0장 평가'],
  ])('채점도 간이도 아닌 장은 미평가로 셉니다 (#1157 리뷰): %o', (s, line) => {
    expect(
      scopeLine({
        target_slides: s.target,
        scored_slides: s.scored,
        estimated_slides: s.estimated,
        provisional_slides: s.provisional,
        excluded: [],
      }),
    ).toBe(line);
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

  it('BE(#1156)가 지난 Take 칸을 비워 보내도 깨지지 않습니다', () => {
    const prev = contentMeasured.previous!;
    // 기능 버전이 없거나 전달률 칸이 없으면 비교하지 않습니다
    expect(
      previousDelta({ ...contentMeasured, previous: { ...prev, criteria_version: null } }),
    ).toBeNull();
    expect(previousDelta({ ...contentMeasured, previous: { ...prev, delivery: null } })).toBeNull();
    // 범위만 없으면 비교는 하고, 지난 Take 쪽 제외 안내만 뺍니다
    const noScope = previousDelta({
      ...contentMeasured,
      summary: {
        ...contentMeasured.summary!,
        scope: { ...contentMeasured.summary!.scope, excluded: [] },
      },
      previous: { ...prev, scope: null },
    });
    expect(noScope).toEqual({ takeNumber: 2, delta: 8, partial: false });
  });

  it('지난 Take 범위 · 값의 모양이 틀리면 비교만 숨기고 던지지 않습니다 (#1157 리뷰)', () => {
    const prev = contentMeasured.previous!;
    const broken = (patch: object) =>
      previousDelta({ ...contentMeasured, previous: { ...prev, ...patch } as typeof prev });
    // 양쪽 전달률은 숫자인데 범위가 빈 객체 · 제외 목록이 배열이 아님
    expect(() => broken({ scope: {} })).not.toThrow();
    expect(broken({ scope: {} })).toBeNull();
    expect(broken({ scope: { excluded: 'x' } })).toBeNull();
    // 전달률이 숫자가 아님
    expect(broken({ delivery: { ...prev.delivery!, value: '0.4' } })).toBeNull();
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

  it('어림이 있으면 맞음 개수를 정확도로 거꾸로 세지 않고 받은 개수 그대로, 어림은 따로 보입니다', () => {
    // 맞음 1 · 어림 1(0.5 로 셈) → 정확도 0.75. 0.75 × 2 = 1.5 를 반올림하면 "2개 맞음"이 되던 경우
    const line = factsLine(
      facts({
        matched: 1,
        approximate: 1,
        pending: 1,
        accuracy: { ...metric(0.75), evaluated: 2 },
      }),
    );
    expect(line).toEqual({
      kind: 'measured',
      text: '확인한 2개 중 1개 맞음 · 1개 어림 · 1개 확인 대기',
    });
  });

  it('다른 값 · 빠뜨림도 확인한 개수에 들어갑니다', () => {
    const line = factsLine(
      facts({ matched: 1, mismatched: 1, missing: 1, pending: 2, accuracy: metric(0.333) }),
    );
    expect(line).toEqual({ kind: 'measured', text: '확인한 3개 중 1개 맞음 · 2개 확인 대기' });
  });
});

describe('장별 펼침', () => {
  const span = (start: number, end: number, id = 'O'): OffscriptSpan => ({
    id,
    stt_sentence_index: 0,
    start,
    end,
    category: 'procedural',
    display_category: 'procedural',
    source: 'rule',
    other_slide_number: null,
    tokens: 1,
  });

  it('대본 밖 구간을 코드 포인트로 자릅니다 — 한글', () => {
    const pieces = splitMarks('안녕하세요 지난 분기', [span(0, 5)]);
    expect(pieces).toEqual([
      { text: '안녕하세요', span: span(0, 5) },
      { text: ' 지난 분기', span: null },
    ]);
  });

  it('이모지는 코드 포인트 하나입니다 (UTF-16 으로는 둘)', () => {
    const pieces = splitMarks('🙂 사실 저도 놀랐어요', [span(2, 9)]);
    expect(pieces.map((p) => p.text)).toEqual(['🙂 ', '사실 저도 놀', '랐어요']);
    expect(pieces[1]!.span?.start).toBe(2);
  });

  it('겹치거나 문장 밖을 가리키는 구간은 버립니다', () => {
    const pieces = splitMarks('하나둘셋넷', [span(0, 2, 'a'), span(1, 3, 'b'), span(4, 9, 'c')]);
    expect(pieces.map((p) => [p.text, p.span?.id ?? null])).toEqual([
      ['하나', 'a'],
      ['둘셋넷', null],
    ]);
  });

  it('문장 배지 — 채점하지 않는 문장 · 모르는 상태', () => {
    expect(sentenceStatusLabel('claim', 'said')).toBe('전달');
    expect(sentenceStatusLabel('skip', null)).toBe('채점 안 함');
    expect(sentenceStatusLabel('claim', null)).toBe('채점 안 함');
    expect(sentenceStatusLabel('claim', 'brand_new')).toBe('판정 보류');
  });
});
