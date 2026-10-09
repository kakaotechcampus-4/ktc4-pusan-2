import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { uploadPresentation } from '@/shared/api/presentation';
import { createScript } from '@/shared/api/script';
import { openPdf } from '@/shared/lib/pdf';
import { isCurrentDraft, useCreateStore } from './createStore';
import { startScriptMapping } from './scriptParse';
import { uploadSlides } from './slideUpload';

vi.mock('@/shared/api/presentation', () => ({ uploadPresentation: vi.fn() }));
vi.mock('@/shared/lib/pdf', () => ({ openPdf: vi.fn() }));
vi.mock('@/shared/api/script', () => ({
  createScript: vi.fn(),
  getScript: vi.fn(),
  reparseScript: vi.fn(),
}));

const store = () => useCreateStore.getState();

/** 끝날 때를 테스트가 정하는 약속 */
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

beforeEach(() => {
  store().reset();
  store().beginEntry('entry-A');
});

afterEach(() => {
  vi.mocked(uploadPresentation).mockReset();
  vi.mocked(openPdf).mockReset();
  vi.mocked(createScript).mockReset();
});

describe('새 피치 시작과 작성 중 복귀', () => {
  it('다른 기록 항목에서 들어오면 앞 피치를 비우고 새로 시작한다 — 저장이 POST 로 간다', () => {
    store().setPitchId('pitch-A');
    store().setMeta({ ...store().draft, title: '피치 A' });
    const before = store().draftId;

    store().beginEntry('entry-B');

    expect(store().pitchId).toBeNull();
    expect(store().draft.title).toBe('');
    expect(store().draftId).not.toBe(before);
    expect(isCurrentDraft(before)).toBe(false);
  });

  it('같은 기록 항목으로 돌아오면(뒤로 가기) 작성 중이던 내용을 그대로 둔다', () => {
    store().setPitchId('pitch-A');
    const before = store().draftId;

    store().beginEntry('entry-A');

    expect(store().pitchId).toBe('pitch-A');
    expect(store().draftId).toBe(before);
  });
});

describe('새 피치를 시작한 뒤 늦게 끝난 앞 피치의 작업', () => {
  it('슬라이드 업로드 결과를 새 작성에 붙이지 않는다', async () => {
    store().setPitchId('pitch-A');
    const upload = deferred<Awaited<ReturnType<typeof uploadPresentation>>>();
    vi.mocked(uploadPresentation).mockReturnValue(upload.promise);
    vi.mocked(openPdf).mockResolvedValue({ numPages: 3 } as Awaited<ReturnType<typeof openPdf>>);

    const running = uploadSlides(new File(['%PDF'], 'a.pdf'));
    store().beginEntry('entry-B');
    upload.resolve({
      message: 'ok',
      presentation: { pitch_id: 'pitch-A', presentation_version_id: 'pv-A', file_url: '/a.pdf' },
    });
    await running;

    expect(store().draft.slides).toEqual([]);
    expect(store().slideUpload).toEqual({ status: 'idle' });
  });

  it('대본 매핑의 서버 버전을 새 작성의 같은 번호 대본에 붙이지 않는다', async () => {
    store().setPitchId('pitch-A');
    store().addScriptVersion('슬라이드 1\n가');
    const created = deferred<Awaited<ReturnType<typeof createScript>>>();
    vi.mocked(createScript).mockReturnValue(created.promise);

    startScriptMapping(1);
    store().beginEntry('entry-B');
    store().setPitchId('pitch-B');
    store().addScriptVersion('슬라이드 1\n나');
    created.resolve({ script_version_id: 'sv-A', version: 1, parse_status: 'PENDING' });
    await vi.waitFor(() => expect(createScript).toHaveBeenCalledTimes(1));
    await Promise.resolve();

    expect(store().draft.scripts[0]).toMatchObject({ text: '슬라이드 1\n나', remote: null });
  });
});
