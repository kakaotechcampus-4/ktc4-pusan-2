import { expect, it } from 'vitest';
import { ApiFailure } from './tokenStore';
import { toMessage } from './errorMessage';

it('거절된 평가기준은 재시도 대신 입력 수정을 안내한다', () => {
  expect(toMessage(new ApiFailure(422, 'STANDARD_INPUT_REJECTED'))).toContain('고쳐 주세요');
});

// BE `pitch/exception.py` · `take/exception.py` 의 코드 이름 그대로입니다. 오타가 나면 기본 문구로 떨어집니다
it.each([
  'PRESENTATION_TOO_COMPLEX',
  'PRESENTATION_BUSY',
  'STANDARD_PARSE_IN_PROGRESS',
  'STANDARD_PARSE_BUSY',
  'EVALUATION_NOT_FOUND',
  'TAKE_RECORDS_NOT_SAVED',
  'TAKE_SESSION_MISMATCH',
])('%s 는 기본 문구가 아니라 자기 안내를 띄운다', (code) => {
  const fallback = toMessage(new ApiFailure(500, 'UNKNOWN_CODE'));
  expect(toMessage(new ApiFailure(500, code))).not.toBe(fallback);
});
