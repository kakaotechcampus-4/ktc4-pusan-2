// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { contentMeasured, contentStatusOf } from '@/mocks/contentCoverage';
import type { ContentStatus, CoverageSummary, Metric } from '@/types/report';
import { ContentCard } from './ContentCard';

/** 화면에 남은 글자만 — 속성(`style` 등)은 빼고 봅니다 */
function textOf(status: ContentStatus | null) {
  const host = document.createElement('div');
  host.innerHTML = renderToStaticMarkup(<ContentCard takeId="t3" status={status} />);
  return host.textContent ?? '';
}

/** "장별 보기"를 눌러 목록을 연 뒤의 글자 */
function openedText(status: ContentStatus | null) {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  const host = document.createElement('div');
  const root = createRoot(host);
  try {
    act(() => root.render(<ContentCard takeId="t3" status={status} />));
    const toggle = [...host.querySelectorAll('button')].find((b) =>
      b.textContent?.startsWith('장별 보기'),
    )!;
    act(() => toggle.click());
    return host.textContent ?? '';
  } finally {
    act(() => root.unmount());
  }
}

const status = (id: string) => contentStatusOf(id, null);

const withSummary = (over: Partial<CoverageSummary>): ContentStatus => ({
  ...contentMeasured,
  summary: { ...contentMeasured.summary!, ...over },
});

const metric = (value: number | null, reason_codes: string[] = []): Metric => ({
  value,
  state: value === null ? 'not_applicable' : 'ok',
  reason_codes,
  evaluated: 0,
  excluded: 0,
});

afterEach(() => vi.unstubAllGlobals());

describe('내용 전달 카드 — 상태', () => {
  it.each([
    [null, '대본 전달도 상세 결과를 아직 제공하지 않아요'],
    [status('t-content-analyzing'), '대본 전달도를 분석하고 있어요'],
    [status('t-content-legacy'), '이 Take는 자세한 결과를 지원하기 전에 분석됐어요'],
    [status('t-content-failed'), '분석이 시간 안에 끝나지 않았어요'],
  ])('%#: 측정값이 없으면 숫자 없이 이유만 보입니다', (given, line) => {
    const text = textOf(given);
    expect(text).toContain('내용 전달');
    expect(text).toContain(line);
    expect(text).not.toContain('핵심 내용 전달률');
  });

  it('실패면 다음 행동을 안내합니다', () => {
    expect(textOf(status('t-content-failed'))).toContain('다시 분석은 아직 지원하지 않아요');
  });

  it('BE 배포 전처럼 키 자체가 없어도 깨지지 않습니다', () => {
    expect(textOf(undefined as unknown as null)).toContain('아직 제공하지 않아요');
  });
});

describe('내용 전달 카드 — 측정됨', () => {
  it('#1152 예시: 대표 숫자 · 다르게 말함 · 일부 장 제외 · 지난 Take 대비', () => {
    const text = textOf(contentMeasured);
    expect(text).toContain('핵심 내용 전달률50%');
    expect(text).toContain('다르게 말함33%');
    expect(text).toContain('일부 장 제외');
    expect(text).toContain('2장 중 1장 평가 · 1장 간이');
    expect(text).toContain('지난 Take보다 +8%p');
    expect(text).toContain('일부 장이 평가에서 제외됐어요');
  });

  it('4칸 막대는 글자 범례와 %를 함께 둡니다', () => {
    const text = textOf(contentMeasured);
    for (const label of ['전달50%', '일부0%', '빠뜨림17%', '다르게 말함33%'])
      expect(text).toContain(label);
  });

  it('놓친 중요 내용 · 수치 · 표현 · 말의 구성', () => {
    const text = textOf(contentMeasured);
    expect(text).toContain('3장 · 신규 고객 비중 60% · 다르게 말함');
    expect(text).toContain('확인한 2개 중 2개 맞음 · 1개 확인 대기');
    expect(text).toContain('바꿔 말해도 감점하지 않아요');
    expect(text).toContain('진행 말10%');
    expect(text).toContain('대본 밖 말은 세부 구분 없이 합쳐 보여요');
  });

  it.each([
    ['OFFSCRIPT_DISABLED (기본 설정)', ['OFFSCRIPT_DISABLED']],
    ['NOT_VALIDATED', ['NOT_VALIDATED']],
  ])(
    '말의 구성에 %s 하나만 와도 합쳐 보인다는 안내를 붙입니다 (#1152 최종 일치 확인)',
    (_, codes) => {
      const mix = contentMeasured.summary!.speech_mix;
      expect(textOf(withSummary({ speech_mix: { ...mix, reason_codes: codes } }))).toContain(
        '대본 밖 말은 세부 구분 없이 합쳐 보여요',
      );
    },
  );

  it('세부 분류 코드가 없으면 합쳐 보인다는 안내를 붙이지 않습니다', () => {
    const mix = contentMeasured.summary!.speech_mix;
    expect(textOf(withSummary({ speech_mix: { ...mix, reason_codes: [] } }))).not.toContain(
      '세부 구분 없이',
    );
  });

  it('장별 목록은 접어 두고 장 수만 보입니다 — 열면 장마다 한 줄', () => {
    const closed = textOf(contentMeasured);
    expect(closed).toContain('장별 보기 (2장)');
    expect(closed).not.toContain('절반쯤 말함(간이)');
    expect(openedText(contentMeasured)).toContain('4장절반쯤 말함(간이)간이');
  });

  it('0 과 null 을 섞지 않습니다 — 0 은 0%와 설명, null 은 —와 이유', () => {
    const zero = textOf(status('t-content-zero'));
    expect(zero).toContain('핵심 내용 전달률0%');
    expect(zero).toContain('핵심 내용을 빠뜨리거나 다르게 말했어요');

    const none = textOf(
      withSummary({
        delivery: metric(null, ['NO_KEY_POINTS']),
        contradiction: metric(null, ['NO_KEY_POINTS']),
        status_shares: null,
      }),
    );
    expect(none).toContain('핵심 내용 전달률—확인할 핵심 내용이 없어요');
    expect(none).not.toContain('전달률0%');
  });

  it('수치가 모두 확인 대기면 확인 전 추정과 추정 배지', () => {
    const text = textOf(status('t-content-zero'));
    expect(text).toContain('확인 전 추정 67%추정');
  });

  it('말의 구성이 없으면 막대 대신 이유', () => {
    expect(textOf(status('t-content-zero'))).toContain('말의 구성말한 내용이 없어요');
  });

  it('Take 지표에 장들의 잠정 이유가 합쳐 오면 Take 문구로 보입니다 (#1152 보완)', () => {
    const text = textOf(
      withSummary({
        delivery: {
          ...contentMeasured.summary!.delivery,
          reason_codes: ['SLIDES_EXCLUDED', 'STT_INCOMPLETE', 'SENTENCE_FALLBACK'],
        },
      }),
    );
    expect(text).toContain('음성 인식이 끊긴 장이 있어요. 녹음된 부분만 봤어요');
    expect(text).toContain('일부 장은 문장 단위로 평가했어요(참고용)');
    expect(text).not.toContain('이 장');
  });

  it('장별 목록 — 0 의 장, 핵심 내용 없는 장, 참고용 장, 실패 장', () => {
    const text = openedText(status('t-content-zero'));
    expect(text).toContain('4장이 장에는 확인할 핵심 내용이 없어요');
    expect(text).toContain('참고용');
    expect(text).toContain('6장음성 인식이 끊겨 이 장을 평가하지 못했어요');
  });

  it('채점된 장 없이 간이 장만 있으면 숫자 대신 간이 단계', () => {
    const text = textOf(status('t-content-estimated'));
    expect(text).toContain('핵심 내용 전달률간이: 대부분 말함 1장간이');
    expect(text).not.toContain('지난 Take보다');
  });

  it('채점된 장이 없으면 Take 전체의 0 개수를 보이지 않고 집계하지 못했다고 알립니다 (#1157 리뷰)', () => {
    const text = textOf(status('t-content-estimated'));
    expect(text).toContain('채점된 장이 없어 핵심 수치 · 표현 · 말의 구성은 집계하지 못했어요');
    expect(text).not.toContain('확인할 수치 0개');
    expect(text).not.toContain('대본대로');
    // 장별 간이 결과는 그대로 둡니다
    expect(openedText(status('t-content-estimated'))).toContain('1장대부분 말함');
  });

  it('모르는 이유 코드 · 범주 · 상태가 와도 일반 문구로 받습니다', () => {
    const text = textOf(
      withSummary({
        delivery: metric(null, ['BRAND_NEW']),
        speech_mix: {
          ...contentMeasured.summary!.speech_mix,
          display: [{ category: 'brand_new' as never, tokens: 1, share: 1 }],
        },
        slides: [
          { ...contentMeasured.summary!.slides[0]!, state: 'brand_new' as never, reason_codes: [] },
        ],
      }),
    );
    expect(text).toContain('이 값을 계산하지 못했어요');
    expect(text).toContain('그 밖의 말100%');
  });

  it.each([
    ['t3', contentMeasured],
    ['zero', status('t-content-zero')],
    ['estimated', status('t-content-estimated')],
  ])('%s: 화면에 undefined · null 글자가 없습니다', (_, given) => {
    const text = openedText(given);
    expect(text).not.toMatch(/undefined|null|NaN/);
  });
});

describe('놓친 중요 내용 — 더 보기', () => {
  it('5개까지 보이고 버튼으로 나머지를 펼칩니다', () => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
    const host = document.createElement('div');
    const root = createRoot(host);
    try {
      act(() =>
        root.render(<ContentCard takeId="t-content-zero" status={status('t-content-zero')} />),
      );
      const items = () => host.querySelectorAll('li').length;
      const before = items();
      const button = [...host.querySelectorAll('button')].find((b) =>
        b.textContent?.includes('더 보기'),
      )!;
      expect(button.getAttribute('aria-expanded')).toBe('false');
      expect(button.textContent).toBe('1개 더 보기');
      act(() => button.click());
      expect(button.getAttribute('aria-expanded')).toBe('true');
      expect(items()).toBe(before + 1);
    } finally {
      act(() => root.unmount());
    }
  });
});
