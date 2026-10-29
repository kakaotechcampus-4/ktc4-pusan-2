/** 기초 문서가 styles.css를 제대로 읽는지 — 제목 주석 묶음 · 줄 끝 설명 · 밝은 지면 · 대비 계산. */
import { expect, it } from 'vitest';
import { contrast, lightOverrides, themeGroups, themeTokens } from './tokens';

it('@theme 값을 제목 주석별로 묶고 줄 끝 설명을 붙인다', () => {
  const groups = new Map(themeGroups().map((group) => [group.title, group]));
  const measured = [...groups.values()].find((group) => group.title.includes('계측 조건'));
  expect(measured?.tokens.map((token) => token.name)).toEqual([
    '--spacing-script-highlight',
    '--spacing-script-keyword',
    '--spacing-script-off',
  ]);
  expect(measured?.description).toContain('팀 합의');
  expect(groups.get('크기')?.tokens.map((token) => token.name)).toEqual([
    '--spacing-app-header',
    '--spacing-sidebar',
    '--container-page',
  ]);
  const ink = themeTokens().find((token) => token.name === '--color-ink');
  expect(ink).toMatchObject({ value: '#2A2723', note: '본문', group: '글자' });
  // 여러 줄 값은 한 줄로 모은다
  expect(themeTokens().find((token) => token.name === '--font-sans')?.value).not.toContain('\n');
});

it('밝은 지면이 바꾸는 색만 읽는다', () => {
  const light = lightOverrides();
  expect(light.get('--color-panel')).toBe('#ffffff');
  expect(light.has('--color-coral')).toBe(false);
});

it('대비는 WCAG 식대로 1~21이다', () => {
  expect(contrast('#000000', '#FFFFFF')).toBeCloseTo(21, 5);
  expect(contrast('#FFFFFF', '#FFFFFF')).toBeCloseTo(1, 5);
  expect(contrast('#767676', '#FFFFFF')).toBeCloseTo(4.54, 2);
});
