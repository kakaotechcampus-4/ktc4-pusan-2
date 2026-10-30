import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ScriptDetail } from '@/types/script';
import { useCreateStore } from './createStore';
import { pendingVersion, toPracticeCombo } from './lib/draft';
import { loadPitch } from './loadPitch';
import { getPitch, getPitchResources } from '@/shared/api/pitch';
import { openPdf } from '@/shared/lib/pdf';

/**
 * 수정 화면 불러오기. 네트워크와 PDF 는 BE 응답 모양 그대로 흉내 냅니다.
 *
 *   슬라이드  V1 3장 · V2 4장
 *   대본      V1 3장 · V2 3장 (V2 도 슬라이드 V1 에 맞음 — 최신끼리 짝이 아닌 경우)
 *   평가기준  V1
 */
vi.mock('@/shared/api/home', () => ({
  fetchPitchList: vi.fn(async () => ({
    pitches: [
      {
        pitch_id: 'p1',
        pitch_title: '캡스톤',
        pitch_time: 300,
        thumbnail_url: null,
        presentation_date: null,
        takes: [],
      },
    ],
  })),
}));
vi.mock('@/shared/api/pitch', () => ({
  getPitch: vi.fn(async () => ({
    pitch_id: 'p1',
    title: '캡스톤',
    time_limit_sec: 300,
    presentation_date: null,
    lower_deviation: 15,
    upper_deviation: 30,
  })),
  getPitchResources: vi.fn(async () => ({
    pitch_id: 'p1',
    // 순서가 섞여 와도 번호대로 둡니다
    presentation_versions: [
      { id: 'pv2', version: 2 },
      { id: 'pv1', version: 1 },
    ],
    script_versions: [
      { id: 'sv1', version: 1 },
      { id: 'sv2', version: 2 },
    ],
    evaluation_versions: [{ id: 'ev1', version: 1 }],
  })),
}));
vi.mock('@/shared/api/presentation', () => ({
  getPresentation: vi.fn(async (_pitchId: string, id: string) => ({
    pitch_id: 'p1',
    presentation_version_id: id,
    version: id === 'pv1' ? 1 : 2,
    file_url: `https://s3/${id}.pdf`,
    description: null,
    created_at: '2026-10-14',
  })),
}));
vi.mock('@/shared/lib/pdf', () => ({
  openPdf: vi.fn(async (url: string) => ({ numPages: url.includes('pv1') ? 3 : 4 })),
}));
const script = (id: string, version: number, n: number): ScriptDetail => ({
  script_version_id: id,
  version,
  original_content: `원문 ${id}`,
  parse_status: 'DONE',
  segmented: true,
  slides: Array.from({ length: n }, (_, i) => ({
    slide_number: i + 1,
    content: `${id}-${i + 1}`,
    keywords: [],
    highlights: [],
  })),
  terms: [],
  error_code: null,
});
vi.mock('@/shared/api/script', () => ({
  getScript: vi.fn(async (_pitchId: string, id: string) => script(id, id === 'sv1' ? 1 : 2, 3)),
}));
vi.mock('@/shared/api/standards', () => ({
  getEvaluation: vi.fn(async () => ({
    pitch_id: 'p1',
    evaluation_version: 1,
    evaluations: [{ order: 1, standard: '추임새 5번 이하' }],
  })),
}));

const store = () => useCreateStore.getState();

beforeEach(() => store().reset());

describe('수정 화면 — 서버의 피치를 초안으로 읽는다', () => {
  it('버전마다 읽어 번호대로 두고, 불러온 버전은 모두 저장된 것이다', async () => {
    const draft = await loadPitch('p1');

    expect(draft).toMatchObject({
      title: '캡스톤',
      timeLimitSec: 300,
      lowerToleranceSec: 15,
      upperToleranceSec: 30,
    });
    expect(draft.slides.map((s) => [s.version, s.pageCount, s.presentationVersionId])).toEqual([
      [1, 3, 'pv1'],
      [2, 4, 'pv2'],
    ]);
    expect(draft.scripts.map((s) => [s.version, s.remote?.id, s.saved, s.text])).toEqual([
      [1, 'sv1', true, '원문 sv1'],
      [2, 'sv2', true, '원문 sv2'],
    ]);
    expect(draft.criteria).toMatchObject([
      { version: 1, saved: true, items: [{ text: '추임새 5번 이하' }] },
    ]);
  });

  it('대본은 장수가 같은 가장 최근 슬라이드에 연결한다', async () => {
    const draft = await loadPitch('p1');
    // 대본은 3장이라 4장짜리 V2 가 아니라 3장짜리 V1 에 붙습니다
    expect(draft.scripts.map((s) => s.slideVersion)).toEqual([1, 1]);
  });

  it('BE 가 센 쪽수가 있으면 PDF 를 열지 않는다 — 없을 때(옛 발표자료)만 연다 (#298)', async () => {
    vi.mocked(getPitchResources).mockResolvedValueOnce({
      pitch_id: 'p1',
      presentation_versions: [
        { id: 'pv1', version: 1, page_count: 3 },
        { id: 'pv2', version: 2, page_count: null },
      ],
      script_versions: [],
      evaluation_versions: [],
    });
    vi.mocked(openPdf).mockClear();

    const draft = await loadPitch('p1');

    expect(draft.slides.map((s) => s.pageCount)).toEqual([3, 4]);
    expect(vi.mocked(openPdf).mock.calls.map(([url]) => url)).toEqual(['https://s3/pv2.pdf']);
  });

  it('기본정보를 못 읽으면 수정 화면을 열지 않는다', async () => {
    vi.mocked(getPitch).mockRejectedValueOnce(new Error('PITCH_NOT_FOUND'));
    await expect(loadPitch('nope')).rejects.toThrow('PITCH_NOT_FOUND');
  });
});

describe('수정 화면 — 불러온 초안으로 시작한다 (hydrate)', () => {
  it('최신 대본과 그 대본이 맞춘 슬라이드로 바로 연습할 수 있다', async () => {
    store().hydrate('key1', 'p1', await loadPitch('p1'));

    expect(store()).toMatchObject({ pitchId: 'p1', entryKey: 'key1', node: 'slides' });
    // "최신끼리"(슬라이드 V2 · 대본 V2)면 장수가 달라 시작할 수 없습니다
    expect(toPracticeCombo(store().draft, store().chosen)).toMatchObject({
      presentationVersionId: 'pv1',
      scriptVersionId: 'sv2',
    });
  });

  it('불러온 버전은 저장된 것이라 새 버전을 바로 만들 수 있다', async () => {
    store().hydrate('key1', 'p1', await loadPitch('p1'));
    for (const node of ['slides', 'script', 'criteria'] as const)
      expect(pendingVersion(store().draft, node)).toBeNull();

    store().reviseCriteria(1);
    expect(store().draft.criteria.map((v) => v.version)).toEqual([1, 2]);
  });

  it('새로 읽으면 앞 작성의 회차가 바뀐다 — 앞에서 시작한 업로드 결과가 들어오지 않는다', async () => {
    const before = store().draftId;
    store().hydrate('key1', 'p1', await loadPitch('p1'));
    expect(store().draftId).not.toBe(before);
  });
});
