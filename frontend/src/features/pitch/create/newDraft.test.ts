import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createPitch } from '@/shared/api/pitch';
import { uploadPresentation } from '@/shared/api/presentation';
import { createScript } from '@/shared/api/script';
import { openPdf } from '@/shared/lib/pdf';
import { isCurrentDraft, useCreateStore } from './createStore';
import { startScriptMapping } from './scriptParse';
import { uploadSlides } from './slideUpload';

vi.mock('@/shared/api/pitch', () => ({ createPitch: vi.fn() }));
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
  vi.mocked(createPitch).mockReset();
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

describe('슬라이드부터 올리는 새 피치', () => {
  const uploaded = {
    message: 'ok',
    presentation: {
      pitch_id: 'pitch-new',
      presentation_version_id: 'pv-1',
      file_url: '/a.pdf',
      page_count: 3,
    },
  };

  it('피치가 없으면 파일 이름을 제목으로 피치부터 만들고 그 아래에 올린다', async () => {
    vi.mocked(createPitch).mockResolvedValue({ message: 'ok', pitch_id: 'pitch-new' });
    vi.mocked(uploadPresentation).mockResolvedValue(uploaded);

    await uploadSlides(new File(['%PDF'], '캡스톤_중간발표-최종.pdf', { type: 'application/pdf' }));

    expect(createPitch).toHaveBeenCalledWith({
      title: '캡스톤 중간발표 최종',
      time_limit_sec: 300,
      lower_deviation: 30,
      upper_deviation: 60,
      presentation_date: null,
    });
    expect(vi.mocked(uploadPresentation).mock.calls[0]?.[0]).toBe('pitch-new');
    expect(store().pitchId).toBe('pitch-new');
    expect(store().draft.title).toBe('캡스톤 중간발표 최종');
    expect(store().autoTitle).toBe('캡스톤 중간발표 최종');
    expect(store().draft.slides).toHaveLength(1);
  });

  it('피치를 만들지 못하면 올리지 않고 같은 파일로 다시 시도하게 한다', async () => {
    vi.mocked(createPitch).mockRejectedValue(new Error('network'));
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const file = new File(['%PDF'], 'a.pdf');

    await uploadSlides(file);

    expect(uploadPresentation).not.toHaveBeenCalled();
    expect(store().pitchId).toBeNull();
    expect(store().slideUpload).toEqual({ status: 'failed', file, reason: 'upload' });
  });

  it('피치를 만드는 사이 새 피치를 시작했으면 만든 피치를 새 작성에 넣지 않는다', async () => {
    const created = deferred<Awaited<ReturnType<typeof createPitch>>>();
    vi.mocked(createPitch).mockReturnValue(created.promise);

    const running = uploadSlides(new File(['%PDF'], 'a.pdf'));
    store().beginEntry('entry-B');
    created.resolve({ message: 'ok', pitch_id: 'pitch-old' });
    await running;

    expect(store().pitchId).toBeNull();
    expect(uploadPresentation).not.toHaveBeenCalled();
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
