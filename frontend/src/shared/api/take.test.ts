import { afterEach, expect, it, vi } from 'vitest';
import { apiRequest } from './client';
import { fetchActiveTake, replaceTake } from './take';

vi.mock('./client', () => ({ apiRequest: vi.fn() }));

afterEach(() => {
  vi.mocked(apiRequest).mockReset();
});

it('발표 중인 Take 는 take 칸을 꺼내고, 없으면 null 이다', async () => {
  const take = { take_id: 't1', pitch_id: 'p1', pitch_title: '발표', take_number: 2 };
  vi.mocked(apiRequest).mockResolvedValueOnce({ take });
  expect(await fetchActiveTake()).toEqual(take);
  expect(apiRequest).toHaveBeenLastCalledWith('/api/takes/active');

  vi.mocked(apiRequest).mockResolvedValueOnce({ take: null });
  expect(await fetchActiveTake()).toBeNull();
});

/** 끝낼 Take 는 다른 피치일 수 있어 지금 화면이 아니라 그 Take 의 pitch_id 로 주소를 만듭니다 */
it('끝내기는 그 Take 의 피치 주소로 replace=true 를 붙여 보낸다', async () => {
  vi.mocked(apiRequest).mockResolvedValueOnce({ message: '', take_id: 't1', status: 'FAILED' });
  await replaceTake({ pitch_id: 'p-other', take_id: 't1' });
  expect(apiRequest).toHaveBeenCalledWith('/api/pitches/p-other/takes/t1/abandon?replace=true', {
    method: 'POST',
  });
});
