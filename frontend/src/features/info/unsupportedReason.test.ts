import { expect, it } from 'vitest';
import { isTouchOnlyDevice, unsupportedReason } from './unsupportedReason';

it('브라우저 기능이 없으면 휴대폰이어도 브라우저 안내를 먼저 한다', () => {
  expect(unsupportedReason(false, true)).toBe('BROWSER');
  expect(unsupportedReason(false, false)).toBe('BROWSER');
});

it('기능은 있는데 터치 화면이면 PC로 옮기라고 한다', () => {
  expect(unsupportedReason(true, true)).toBe('MOBILE');
});

it('둘 다 아니면 연습할 수 있다고 알린다 (주소로 바로 들어온 경우)', () => {
  expect(unsupportedReason(true, false)).toBe('SUPPORTED');
});

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
