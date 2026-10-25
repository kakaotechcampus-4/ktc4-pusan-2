import { expect, it } from 'vitest';
import { unsupportedReason } from './unsupportedReason';

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
