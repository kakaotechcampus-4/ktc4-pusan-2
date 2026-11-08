import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fetchPitchList } from '@/shared/api/home';
import { createPitch, getPitchResources } from '@/shared/api/pitch';
import { uploadPresentation } from '@/shared/api/presentation';
import { createScript, getScript, reparseScript } from '@/shared/api/script';
import { ApiFailure } from '@/shared/api/tokenStore';
import { FirstTakeError } from './firstTakeError';
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

  it('이미 취소된 요청은 데이터를 생성하지 않는다', async () => {
    const controller = new AbortController();
    controller.abort();

    await expect(ensureFirstTakePitch(vi.fn(), controller.signal)).rejects.toMatchObject({
      name: 'AbortError',
    });
    expect(fetchPitchList).not.toHaveBeenCalled();
    expect(createPitch).not.toHaveBeenCalled();
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

describe('실패 복구와 중복 샘플', () => {
  beforeEach(() => {
    vi.clearAllMocks();
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
    vi.mocked(getScript).mockResolvedValue(mapped);
  });

  it('동일한 샘플이 둘이면 기존 것을 재사용하고 새로 만들지 않는다', async () => {
    const result = await fetchPitchList();
    vi.mocked(fetchPitchList).mockResolvedValue({
      pitches: [result.pitches[0]!, { ...result.pitches[0]!, pitch_id: 'duplicate' }],
    });
    await expect(ensureFirstTakePitch(vi.fn())).resolves.toBe('existing');
    expect(createPitch).not.toHaveBeenCalled();
  });

  it('조회 502 장애 한 번 뒤 정상 결과가 오면 계속 진행한다', async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(getScript)
        .mockRejectedValueOnce(new ApiFailure(502, 'INTERNAL_SERVER_ERROR'))
        .mockResolvedValueOnce(mapped);
      const pending = ensureFirstTakePitch(vi.fn());
      await vi.advanceTimersByTimeAsync(1000);
      await expect(pending).resolves.toBe('existing');
      expect(getScript).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it('조회 404는 복구 불가능한 오류로 즉시 중단한다', async () => {
    vi.mocked(getScript).mockRejectedValueOnce(new ApiFailure(404, 'SCRIPT_NOT_FOUND'));
    await expect(ensureFirstTakePitch(vi.fn())).rejects.toMatchObject({
      code: 'SCRIPT_NOT_FOUND',
    });
    expect(getScript).toHaveBeenCalledOnce();
  });

  it('분석 FAILED에서 재분석 후 DONE으로 회복한다', async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(getScript)
        .mockResolvedValueOnce({ ...mapped, parse_status: 'FAILED' })
        .mockResolvedValueOnce(mapped);
      const pending = ensureFirstTakePitch(vi.fn());
      await vi.advanceTimersByTimeAsync(1000);
      await expect(pending).resolves.toBe('existing');
      expect(reparseScript).toHaveBeenCalledOnce();
    } finally {
      vi.useRealTimers();
    }
  });

  it('재분석 409는 다른 탭이 진행 중인 것으로 보고 계속 조회한다', async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(getScript)
        .mockResolvedValueOnce({ ...mapped, parse_status: 'FAILED' })
        .mockResolvedValueOnce(mapped);
      vi.mocked(reparseScript).mockRejectedValueOnce(
        new ApiFailure(409, 'SCRIPT_PARSE_IN_PROGRESS'),
      );
      const pending = ensureFirstTakePitch(vi.fn());
      await vi.advanceTimersByTimeAsync(1000);
      await expect(pending).resolves.toBe('existing');
      expect(getScript).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it('분석이 100초를 넘기면 사용자에게 재시도 안내를 준다', async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(getScript).mockResolvedValue({ ...mapped, parse_status: 'PENDING' });
      const pending = ensureFirstTakePitch(vi.fn());
      const assertion = expect(pending).rejects.toThrow(
        '다시 시도하면 기존 작업을 이어서 확인합니다.',
      );
      await vi.advanceTimersByTimeAsync(101_000);
      await assertion;
    } finally {
      vi.useRealTimers();
    }
  });

  it('새 샘플의 PDF 생성이 실패하면 피치 등록을 하지 않는다', async () => {
    vi.mocked(fetchPitchList).mockResolvedValue({ pitches: [] });
    vi.mocked(createFirstTakePdf).mockRejectedValueOnce(
      new FirstTakeError('이 브라우저에서는 샘플 발표자료를 만들 수 없어요.'),
    );
    await expect(ensureFirstTakePitch(vi.fn())).rejects.toBeInstanceOf(FirstTakeError);
    expect(createPitch).not.toHaveBeenCalled();
  });
});
