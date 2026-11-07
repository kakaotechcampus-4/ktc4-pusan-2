/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const product = readFileSync(new URL('../../index.css', import.meta.url), 'utf8');
const design = readFileSync(
  new URL('../../../../docs/fe/design/src/styles.css', import.meta.url),
  'utf8',
);
const productStage = readFileSync(
  new URL('../../features/rehearsal/stage/stage.css', import.meta.url),
  'utf8',
);
const designStage = readFileSync(
  new URL('../../../../docs/fe/design/src/ui/stage.css', import.meta.url),
  'utf8',
);
const devStage = readFileSync(
  new URL('../../dev/stage/stage-preview.css', import.meta.url),
  'utf8',
);
const camera = readFileSync(
  new URL('../../vendor/gaze/camera/camera.css', import.meta.url),
  'utf8',
);

function colorToken(css: string, selector: string, token: string): string {
  // 선택자 목록(`.a, .b {`) 안에 selector 가 들어 있는 블록을 찾는다. `@import …;` 같은 앞 문장은 떼고, 안에 다른 블록을 품은 @scope · @media 머리는 건너뛴다.
  const blocks = [...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(
    ([, prelude]) =>
      prelude!
        .split(';')
        .pop()!
        .split(',')
        .some((part) => part.trim() === selector),
  );
  if (blocks.length === 0) throw new Error('CSS selector not found: ' + selector);

  const declaration = new RegExp('^\\s*' + token + '\\s*:\\s*(#[0-9a-f]{6})\\s*;', 'im');
  for (const [, , body] of blocks) {
    const color = body!.match(declaration)?.[1];
    if (color) return color;
  }
  throw new Error('CSS color token not found: ' + selector + ' ' + token);
}

function glassRgba(css: string): [number, number, number, number] {
  const match = css.match(
    /--glass\s*:\s*rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)/,
  );
  if (!match) throw new Error('CSS glass token not found');
  return [Number(match[1]), Number(match[2]), Number(match[3]), Number(match[4])];
}

// 대본 자리 칸은 --glass 가 아닌 자기 반투명 배경(블록의 마지막 rgba)을 쓴다.
function scriptAreaRgba(css: string): [number, number, number, number] {
  const body = css.match(/\.gzc-script\s*\{([^}]*)\}/)?.[1];
  // border-top 의 rgba 가 아니라 background 선언에서만 읽는다.
  const background = body?.match(/background:([^;]*);/)?.[1];
  const colors = [
    ...(background ?? '').matchAll(/rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)/g),
  ];
  const last = colors.at(-1);
  if (!last) throw new Error('CSS .gzc-script background not found');
  return [Number(last[1]), Number(last[2]), Number(last[3]), Number(last[4])];
}

// 유리 카드(rgba) 를 영상 색 위에 얹었을 때 실제로 보이는 색
function overBackdrop(glass: [number, number, number, number], backdrop: string): string {
  const [r, g, b, alpha] = glass;
  const under = backdrop
    .slice(1)
    .match(/.{2}/g)!
    .map((channel) => Number.parseInt(channel, 16));
  return (
    '#' +
    [r, g, b]
      .map((value, i) => Math.round(value * alpha + under[i]! * (1 - alpha)))
      .map((value) => value.toString(16).padStart(2, '0'))
      .join('')
  );
}

function luminance(hex: string): number {
  const channels = hex
    .slice(1)
    .match(/.{2}/g)!
    .map((channel) => {
      const value = Number.parseInt(channel, 16) / 255;
      return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    });
  return channels[0]! * 0.2126 + channels[1]! * 0.7152 + channels[2]! * 0.0722;
}

function contrast(foreground: string, background: string): number {
  const [lighter, darker] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (lighter! + 0.05) / (darker! + 0.05);
}

describe('stone text contrast (WCAG AA, normal text)', () => {
  it.each([
    ['cream', '--color-cream'],
    ['greige', '--color-greige'],
    ['panel', '--color-panel'],
    ['coral wash', '--color-coral-wash'],
  ])('general screen: stone on %s is at least 4.5:1', (_name, backgroundToken) => {
    const stone = colorToken(product, '@theme', '--color-stone');
    const background = colorToken(product, '@theme', backgroundToken);
    expect(contrast(stone, background)).toBeGreaterThanOrEqual(4.5);
  });

  it.each([
    ['cream', '--color-cream'],
    ['greige', '--color-greige'],
    ['panel', '--color-panel'],
    ['coral wash', '--color-coral-wash'],
  ])('pitch-create: stone on %s is at least 4.5:1', (_name, backgroundToken) => {
    const stone = colorToken(product, '.pitch-create', '--color-stone');
    const background = colorToken(product, '.pitch-create', backgroundToken);
    expect(contrast(stone, background)).toBeGreaterThanOrEqual(4.5);
  });

  // 실제 리허설과 설계서 무대는 `stage stage-live` 로 뒤의 .stage-live 가 토큰을 다시 덮는다.
  it.each([
    ['stage', '--color-stage'],
    ['stage panel', '--color-stage-panel'],
    ['stage deep', '--color-stage-deep'],
  ])('light rehearsal (.stage-live): stone on %s is at least 4.5:1', (_name, backgroundToken) => {
    const stone = colorToken(productStage, '.stage-live', '--color-stone');
    const background = colorToken(productStage, '.stage-live', backgroundToken);
    expect(contrast(stone, background)).toBeGreaterThanOrEqual(4.5);
  });

  it('does not override stone on the base .stage (the .stage-live value must be the one applied)', () => {
    for (const css of [productStage, designStage]) {
      expect(() => colorToken(css, '.stage', '--color-stone')).toThrow('color token not found');
    }
  });

  it('keeps product and design-guide stone tokens in sync', () => {
    for (const scope of ['@theme', '.pitch-create']) {
      expect(colorToken(product, scope, '--color-stone')).toBe(
        colorToken(design, scope, '--color-stone'),
      );
    }
    const stage = colorToken(productStage, '.stage-live', '--color-stone');
    expect(colorToken(designStage, '.stage-live', '--color-stone')).toBe(stage);
    expect(colorToken(devStage, '.stage-live', '--color-stone')).toBe(stage);
  });

  // 장치 점검의 시선 보정 화면(vendor/gaze/camera)은 index.css 토큰을 복사한 자기 변수를 쓴다.
  it.each([
    ['cream', '--cream'],
    ['greige', '--greige'],
    ['panel', '--panel'],
    ['coral wash', '--coral-wash'],
  ])('gaze calibration (.gzc): stone on %s is at least 4.5:1', (_name, backgroundToken) => {
    const stone = colorToken(camera, '.gzc', '--stone');
    const background = colorToken(camera, '.gzc', backgroundToken);
    expect(contrast(stone, background)).toBeGreaterThanOrEqual(4.5);
  });

  // 유리 카드는 반투명이라 영상이 가장 어두운 검정일 때가 최악이다.
  it.each([
    ['black', '#000000'],
    ['backdrop', null],
    ['white', '#ffffff'],
  ])(
    'gaze calibration (.gzc): stone on the glass card over %s video is at least 4.5:1',
    (_name, video) => {
      const stone = colorToken(camera, '.gzc', '--stone');
      const under = video ?? colorToken(camera, '.gzc', '--backdrop');
      const card = overBackdrop(glassRgba(camera), under);
      expect(contrast(stone, card)).toBeGreaterThanOrEqual(4.5);
    },
  );

  // 대본 자리 라벨은 stone 이 아닌 ink 로 쓴다. 같은 이유로 영상이 검정일 때가 최악이다.
  it('gaze calibration (.gzc-script): the label uses ink, not stone', () => {
    // `.gzc-lens em,\n.gzc-script em {` 처럼 목록에 든 블록도 있어서 `.gzc-script em` 이 든 블록을 모두 본다.
    const label = [...camera.matchAll(/([^{}]*\.gzc-script em\s*)\{([^}]*)\}/g)]
      .map((block) => block[2])
      .join('\n');
    expect(label).toMatch(/color:\s*var\(--ink\)/);
    expect(label).not.toMatch(/var\(--stone\)/);
  });

  it.each([
    ['black', '#000000'],
    ['backdrop', null],
    ['white', '#ffffff'],
  ])(
    'gaze calibration (.gzc-script): ink label over %s video is at least 4.5:1',
    (_name, video) => {
      const ink = colorToken(camera, '.gzc', '--ink');
      const under = video ?? colorToken(camera, '.gzc', '--backdrop');
      const area = overBackdrop(scriptAreaRgba(camera), under);
      expect(contrast(ink, area)).toBeGreaterThanOrEqual(4.5);
    },
  );

  it('keeps the gaze calibration stone in sync with the product stone', () => {
    expect(colorToken(camera, '.gzc', '--stone')).toBe(
      colorToken(product, '@theme', '--color-stone'),
    );
  });
});
