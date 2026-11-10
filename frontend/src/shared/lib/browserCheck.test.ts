import { expect, it } from 'vitest';
import { isTouchOnlyDevice } from './browserCheck';

const media = (matching: string[]) => (query: string) => ({ matches: matching.includes(query) });

it('손가락뿐인 기기(휴대폰 · 태블릿)는 터치 전용이다', () => {
  expect(isTouchOnlyDevice(media(['(pointer: coarse)']))).toBe(true);
});

it('마우스 · 트랙패드가 있으면 터치 전용이 아니다 (노트북 · 키보드를 단 태블릿)', () => {
  expect(isTouchOnlyDevice(media(['(pointer: fine)', '(any-pointer: fine)']))).toBe(false);
  expect(isTouchOnlyDevice(media(['(pointer: coarse)', '(any-pointer: fine)']))).toBe(false);
});

it('matchMedia 가 없으면 막지 않는다', () => {
  expect(isTouchOnlyDevice(undefined)).toBe(false);
});
