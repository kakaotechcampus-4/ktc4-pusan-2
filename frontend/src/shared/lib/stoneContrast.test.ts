import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const product = readFileSync(new URL('../../index.css', import.meta.url), 'utf8');
const design = readFileSync(new URL('../../../../docs/fe/design/src/styles.css', import.meta.url), 'utf8');
const productStage = readFileSync(
  new URL('../../features/rehearsal/stage/stage.css', import.meta.url),
  'utf8',
);
const designStage = readFileSync(
  new URL('../../../../docs/fe/design/src/ui/stage.css', import.meta.url),
  'utf8',
);

function colorToken(css: string, selector: string, token: string): string {
  const start = css.indexOf(selector + ' {');
  if (start < 0) throw new Error('CSS selector not found: ' + selector);
  const end = css.indexOf('\n}', start);
  if (end < 0) throw new Error('CSS block not closed: ' + selector);

  const declaration = new RegExp('^\\s*' + token + '\\s*:\\s*(#[0-9a-f]{6})\\s*;', 'im');
  const color = css.slice(start, end).match(declaration)?.[1];
  if (!color) throw new Error('CSS color token not found: ' + selector + ' ' + token);
  return color;
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
  const [lighter, darker] = [luminance(foreground), luminance(background)].sort(
    (a, b) => b - a,
  );
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

  it.each([
    ['stage', '--color-stage'],
    ['stage panel', '--color-stage-panel'],
    ['stage deep', '--color-stage-deep'],
  ])('dark rehearsal: stone on %s is at least 4.5:1', (_name, backgroundToken) => {
    const stone = colorToken(productStage, '.stage', '--color-stone');
    const background = colorToken(product, '@theme', backgroundToken);
    expect(contrast(stone, background)).toBeGreaterThanOrEqual(4.5);
  });

  it('keeps the general, pitch-create, and dark-stage tones scoped separately', () => {
    const general = colorToken(product, '@theme', '--color-stone');
    const create = colorToken(product, '.pitch-create', '--color-stone');
    const stage = colorToken(productStage, '.stage', '--color-stone');
    expect(new Set([general, create, stage]).size).toBe(3);
  });

  it('keeps product and design-guide stone tokens in sync', () => {
    for (const scope of ['@theme', '.pitch-create']) {
      expect(colorToken(product, scope, '--color-stone')).toBe(
        colorToken(design, scope, '--color-stone'),
      );
    }
    expect(colorToken(productStage, '.stage', '--color-stone')).toBe(
      colorToken(designStage, '.stage', '--color-stone'),
    );
  });
});
