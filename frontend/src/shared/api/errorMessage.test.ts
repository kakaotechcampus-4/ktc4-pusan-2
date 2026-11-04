import { expect, it } from 'vitest';
import { ApiFailure } from './tokenStore';
import { toMessage } from './errorMessage';

it('거절된 평가기준은 재시도 대신 입력 수정을 안내한다', () => {
  expect(toMessage(new ApiFailure(422, 'STANDARD_INPUT_REJECTED'))).toContain('고쳐 주세요');
});
