/** 주소의 device · w · h · rotate가 그릴 크기로 바뀌는지. */
import { expect, it } from 'vitest';
import { previewSize, previewZoom } from './frame';

const size = (query: string) => previewSize(new URLSearchParams(query));

it('기기 크기 · 칸에 맞춤 · 알 수 없는 값', () => {
  expect(size('')).toBeNull();
  expect(size('device=fit')).toBeNull();
  expect(size('device=mobile')).toEqual({ width: 390, height: 844 });
  expect(size('device=laptop-wide')).toEqual({ width: 1366, height: 768 });
  expect(size('device=nope')).toBeNull();
});

it('직접 입력은 범위 안으로 맞추고, 비면 1280 × 800', () => {
  expect(size('device=custom&w=414&h=896')).toEqual({ width: 414, height: 896 });
  expect(size('device=custom&w=10&h=99999')).toEqual({ width: 280, height: 2560 });
  expect(size('device=custom')).toEqual({ width: 1280, height: 800 });
});

it('돌리면 가로 · 세로를 바꾼다', () => {
  expect(size('device=tablet&rotate=1')).toEqual({ width: 1024, height: 768 });
  expect(size('device=custom&w=400&h=900&rotate=1')).toEqual({ width: 900, height: 400 });
});

it('브라우저 확대는 목록의 값만 받고, 없으면 100%', () => {
  const zoom = (query: string) => previewZoom(new URLSearchParams(query));
  expect(zoom('')).toBe(1);
  expect(zoom('zoom=125')).toBe(1.25);
  expect(zoom('zoom=33')).toBe(1);
});
