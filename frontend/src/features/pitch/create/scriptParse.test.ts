import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ApiFailure } from '@/shared/api/tokenStore';
import { createScript, getScript } from '@/shared/api/script';
import { useCreateStore } from './createStore';
import { startScriptMapping } from './scriptParse';

vi.mock('@/shared/api/script', () => ({
  createScript: vi.fn(),
  getScript: vi.fn(),
  reparseScript: vi.fn(),
}));

const store = () => useCreateStore.getState();
const parseOf = () => store().draft.scripts[0]?.parse;

beforeEach(() => {
  vi.useFakeTimers();
  store().reset();
  store().setPitchId('pitch1');
  store().addScriptVersion('슬라이드 1\n처음');
  vi.mocked(createScript).mockResolvedValue({
    script_version_id: 'sv1',
    version: 1,
    parse_status: 'PENDING',
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.mocked(getScript).mockReset();
});

it('대본이 없어졌으면(404) 100초를 기다리지 않고 바로 실패를 보여 준다', async () => {
  vi.mocked(getScript).mockRejectedValue(new ApiFailure(404, 'SCRIPT_NOT_FOUND'));

  startScriptMapping(1);
  await vi.advanceTimersByTimeAsync(1_000);

  expect(getScript).toHaveBeenCalledTimes(1);
  expect(parseOf()).toEqual({
    status: 'failed',
    message: '대본을 찾을 수 없어요. 대본 매핑을 다시 눌러 주세요.',
  });
});

it('일시적인 실패(5xx · 네트워크)는 이어서 다시 묻는다', async () => {
  vi.mocked(getScript)
    .mockRejectedValueOnce(new ApiFailure(503, 'INTERNAL_SERVER_ERROR'))
    .mockRejectedValueOnce(new TypeError('Failed to fetch'))
    .mockResolvedValue({
      script_version_id: 'sv1',
      version: 1,
      original_content: '슬라이드 1\n처음',
      parse_status: 'DONE',
      segmented: true,
      slides: [{ slide_number: 1, content: '처음', keywords: [], highlights: [] }],
      terms: [],
      error_code: null,
    });

  startScriptMapping(1);
  await vi.advanceTimersByTimeAsync(3_000);

  expect(getScript).toHaveBeenCalledTimes(3);
  expect(parseOf()).toEqual({ status: 'idle' });
  expect(store().draft.scripts[0]?.blocks).toEqual(['처음']);
});
