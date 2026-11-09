import { expect, it } from 'vitest';
import { PARSE_WAIT_TEXT, parseWaitText } from './parseWait';

it.each([
  [0, null],
  [2_999, null],
  [3_000, PARSE_WAIT_TEXT.normal],
  [9_999, PARSE_WAIT_TEXT.normal],
  [10_000, PARSE_WAIT_TEXT.slow],
  [60_000, PARSE_WAIT_TEXT.slow],
])('%dms 기다렸으면 안내는 %s', (elapsedMs, text) => {
  expect(parseWaitText(elapsedMs)).toBe(text);
});
