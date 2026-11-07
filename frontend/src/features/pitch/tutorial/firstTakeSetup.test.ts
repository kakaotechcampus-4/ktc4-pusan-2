import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchPitchList } from '@/shared/api/home';
import { createPitch, getPitchResources } from '@/shared/api/pitch';
import { uploadPresentation } from '@/shared/api/presentation';
import { createScript, getScript } from '@/shared/api/script';
import { createFirstTakePdf } from './firstTakePdf';
import { FIRST_TAKE_TITLE } from './firstTakeMaterials';
import { ensureFirstTakePitch } from './firstTakeSetup';

vi.mock('@/shared/api/home', () => ({ fetchPitchList: vi.fn() }));
vi.mock('@/shared/api/pitch', () => ({ createPitch: vi.fn(), getPitchResources: vi.fn() }));
vi.mock('@/shared/api/presentation', () => ({ uploadPresentation: vi.fn() }));
vi.mock('@/shared/api/script', () => ({
  createScript: vi.fn(),
  getScript: vi.fn(),
  reparseScript: vi.fn(),
}));
vi.mock('./firstTakePdf', () => ({ createFirstTakePdf: vi.fn() }));

const mapped = {
  script_version_id: 'script-1',
  version: 1,
  original_content: null,
  parse_status: 'DONE' as const,
  segmented: true,
  slides: [1, 2, 3, 4, 5].map((slide_number) => ({
    slide_number,
    content: '테스트',
    keywords: [],
    highlights: [],
  })),
  terms: [],
  error_code: null,
};

describe('ensureFirstTakePitch', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getScript).mockResolvedValue(mapped);
  });

  it('기존 샘플은 재사용하고 Take를 만들지 않는다', async () => {
    vi.mocked(fetchPitchList).mockResolvedValue({
      pitches: [
        {
          pitch_id: 'existing',
          pitch_title: FIRST_TAKE_TITLE,
          pitch_time: 180,
          thumbnail_url: null,
          takes: [],
          can_delete: true,
        },
      ],
    });
    vi.mocked(getPitchResources).mockResolvedValue({
      pitch_id: 'existing',
      presentation_versions: [{ id: 'slide-1', version: 1, page_count: 5 }],
      script_versions: [{ id: 'script-1', version: 1 }],
      evaluation_versions: [],
    });
    const result = await ensureFirstTakePitch(vi.fn());
    expect(result).toBe('existing');
    expect(createPitch).not.toHaveBeenCalled();
    expect(uploadPresentation).not.toHaveBeenCalled();
    expect(createScript).not.toHaveBeenCalled();
  });

  it('처음에는 피치·PDF·대본을 만들고 분석까지 확인한다', async () => {
    vi.mocked(fetchPitchList).mockResolvedValue({ pitches: [] });
    vi.mocked(createPitch).mockResolvedValue({ pitch_id: 'created', message: 'ok' });
    vi.mocked(getPitchResources).mockResolvedValue({
      pitch_id: 'created',
      presentation_versions: [],
      script_versions: [],
      evaluation_versions: [],
    });
    vi.mocked(createFirstTakePdf).mockResolvedValue(
      new File(['pdf'], 'sample.pdf', { type: 'application/pdf' }),
    );
    vi.mocked(uploadPresentation).mockResolvedValue({
      message: 'ok',
      presentation: {
        pitch_id: 'created',
        presentation_version_id: 'slide-1',
        file_url: 'https://example.com/sample.pdf',
        page_count: 5,
      },
    });
    vi.mocked(createScript).mockResolvedValue({
      script_version_id: 'script-1',
      version: 1,
      parse_status: 'PENDING',
    });
    expect(await ensureFirstTakePitch(vi.fn())).toBe('created');
    expect(createPitch).toHaveBeenCalledOnce();
    expect(uploadPresentation).toHaveBeenCalledOnce();
    expect(createScript).toHaveBeenCalledOnce();
  });

  it('대본 슬라이드 개수가 맞지 않으면 이동하지 않는다', async () => {
    vi.mocked(fetchPitchList).mockResolvedValue({
      pitches: [
        {
          pitch_id: 'existing',
          pitch_title: FIRST_TAKE_TITLE,
          pitch_time: 180,
          thumbnail_url: null,
          takes: [],
          can_delete: true,
        },
      ],
    });
    vi.mocked(getPitchResources).mockResolvedValue({
      pitch_id: 'existing',
      presentation_versions: [{ id: 'slide-1', version: 1, page_count: 5 }],
      script_versions: [{ id: 'script-1', version: 1 }],
      evaluation_versions: [],
    });
    vi.mocked(getScript).mockResolvedValue({
      ...mapped,
      segmented: false,
      slides: mapped.slides.slice(0, 1),
    });
    await expect(ensureFirstTakePitch(vi.fn())).rejects.toThrow('5개 슬라이드');
  });
});
