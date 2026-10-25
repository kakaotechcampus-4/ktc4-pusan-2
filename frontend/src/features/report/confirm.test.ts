import { expect, it } from 'vitest';
import { confirmationItemsFixture as items } from '@/mocks/report';
import { allPicked, itemKey, remainingLabel, splitAround, toAnswers } from './confirmFormat';

it('keys items by slide and item id, since item ids restart on every slide', () => {
  expect(items.map(itemKey)).toEqual(['3:R1', '5:R1']);
});

it('sends only real answers — unsure and unpicked items stay out of the score', () => {
  expect(toAnswers(items, { '3:R1': 'as_script', '5:R1': 'unsure' })).toEqual([
    { slide_number: 3, item_id: 'R1', answer: 'as_script' },
  ]);
  expect(toAnswers(items, { '5:R1': 'as_stt' })).toEqual([
    { slide_number: 5, item_id: 'R1', answer: 'as_stt' },
  ]);
  expect(toAnswers(items, {})).toEqual([]);
});

it('counts unsure as picked', () => {
  expect(allPicked(items, { '3:R1': 'as_script' })).toBe(false);
  expect(allPicked(items, { '3:R1': 'as_script', '5:R1': 'unsure' })).toBe(true);
  expect(allPicked([], {})).toBe(true);
});

it('rounds the remaining time up and never shows a negative', () => {
  expect(remainingLabel(570_000)).toBe('09:30');
  expect(remainingLabel(1)).toBe('00:01');
  expect(remainingLabel(0)).toBe('00:00');
  expect(remainingLabel(-5000)).toBe('00:00');
});

it('splits the sentence around the word, or gives up when it is not there', () => {
  expect(splitAround('매출은 37% 늘었습니다.', '37%')).toEqual(['매출은 ', '37%', ' 늘었습니다.']);
  expect(splitAround('매출은 늘었습니다.', '37%')).toBeNull();
  expect(splitAround(null, '37%')).toBeNull();
  expect(splitAround('매출', '')).toBeNull();
});
