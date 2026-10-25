import { describe, expect, it } from 'vitest';
import { retryTakeHistory } from './takeHistory';
import { ApiFailure } from './tokenStore';

describe('retryTakeHistory', () => {
  it('없거나 남의 피치(404)는 다시 묻지 않는다', () => {
    expect(retryTakeHistory(0, new ApiFailure(404, 'PITCH_NOT_FOUND'))).toBe(false);
  });

  it('UUID 꼴이 아닌 피치 id(422)는 다시 묻지 않는다', () => {
    expect(retryTakeHistory(0, new ApiFailure(422, 'VALIDATION_ERROR'))).toBe(false);
  });

  it('피치 없음이 아닌 404 는 한 번 더 묻는다', () => {
    expect(retryTakeHistory(0, new ApiFailure(404, 'REQUEST_FAILED'))).toBe(true);
  });

  it('그 밖의 실패는 한 번만 더 묻는다', () => {
    const error = new ApiFailure(500, 'INTERNAL_SERVER_ERROR');
    expect(retryTakeHistory(0, error)).toBe(true);
    expect(retryTakeHistory(1, error)).toBe(false);
    expect(retryTakeHistory(0, new TypeError('Failed to fetch'))).toBe(true);
  });
});
