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
});
