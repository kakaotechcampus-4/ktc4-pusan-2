import { describe, expect, it, vi } from 'vitest';
import {
  clearMemoryItems,
  fetchMemory,
  memoryKey,
  readMemorySettings,
  removeMemoryItem,
  saveMemoryEnabled,
} from './chatMemory';

const ITEM = { item_id: '44000000-0000-4000-8000-000000000001', key: 'TONE', value: '짧게 설명' };

describe('readMemorySettings — BE MemorySettingResponse 읽기', () => {
  it('설정과 항목을 그대로 읽는다', () => {
    expect(readMemorySettings({ enabled: false, items: [ITEM] })).toEqual({
      enabled: false,
      items: [ITEM],
    });
  });

  it.each([
    null,
    {},
    { enabled: 'true', items: [] },
    { enabled: true },
    { enabled: true, items: {} },
  ])('설정 모양이 아니면 던진다 — %j', (body) => {
    expect(() => readMemorySettings(body)).toThrow();
  });

  it('깨진 항목 · 모르는 종류의 항목만 빼고 나머지는 읽고, 뺀 개수만 경고로 남긴다', () => {
    const warns = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    const settings = readMemorySettings({
      enabled: true,
      items: [
        ITEM,
        { ...ITEM, item_id: '' },
        { ...ITEM, key: 'UNKNOWN' },
        { ...ITEM, value: '' },
        null,
        'TONE',
      ],
    });
    expect(settings.items).toEqual([ITEM]);
    // 값(사용자 정보)은 남기지 않고 개수만
    expect(warns.mock.calls).toEqual([['기억 항목이 계약 모양이 아니라 뺐어요', { dropped: 5 }]]);
    warns.mockRestore();
  });

  it('뺀 항목이 없으면 경고하지 않는다', () => {
    const warns = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    readMemorySettings({ enabled: true, items: [ITEM] });
    readMemorySettings({ enabled: false, items: [] });
    expect(warns).not.toHaveBeenCalled();
    warns.mockRestore();
  });
});

describe('기억 요청 — 응답을 검사해 돌려준다', () => {
  const settings = { enabled: true, items: [ITEM] };
  const api = () => ({
    getMemory: vi.fn(async () => settings),
    setMemoryEnabled: vi.fn(async () => ({ ...settings, enabled: false })),
    deleteMemoryItems: vi.fn(async () => ({ ...settings, items: [] })),
    deleteMemoryItem: vi.fn(async () => ({ ...settings, items: [] })),
  });

  it('읽기 · 켜고 끄기 · 전체 지우기 · 하나 지우기', async () => {
    const fake = api();
    expect(await fetchMemory(undefined, fake)).toEqual(settings);
    expect((await saveMemoryEnabled(false, fake)).enabled).toBe(false);
    expect(fake.setMemoryEnabled).toHaveBeenCalledWith(false);
    expect((await clearMemoryItems(fake)).items).toEqual([]);
    expect((await removeMemoryItem(ITEM.item_id, fake)).items).toEqual([]);
    expect(fake.deleteMemoryItem).toHaveBeenCalledWith(ITEM.item_id);
  });

  it('응답 모양이 다르면 실패로 알린다 — 깨진 값을 캐시에 넣지 않게', async () => {
    const fake = { ...api(), getMemory: vi.fn(async () => ({ enabled: 'yes' })) };
    await expect(fetchMemory(undefined, fake)).rejects.toThrow();
  });

  it('캐시 키는 사용자별 — 사용자가 바뀌면 앞 사람의 기억을 쓰지 않는다', () => {
    expect(memoryKey('a')).not.toEqual(memoryKey('b'));
  });
});
