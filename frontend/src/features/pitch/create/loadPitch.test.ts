import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ScriptDetail } from '@/types/script';
import { useCreateStore } from './createStore';
import { pendingVersion, toPracticeCombo } from './lib/draft';
import { loadPitch } from './loadPitch';
import { getPitch, getPitchResources } from '@/shared/api/pitch';
import { getScript } from '@/shared/api/script';
import { splitScript } from '@/mocks/scripts';
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
// 기본: 대본 V1 은 3장(슬라이드 V1 과 같음), 대본 V2 는 4장(슬라이드 V2 와 같음)
vi.mock('@/shared/api/script', () => ({
  getScript: vi.fn(async (_pitchId: string, id: string) =>
    id === 'sv1' ? script(id, 1, 3) : script(id, 2, 4),
  ),
}));
/** 슬라이드 V2(4장)를 올렸는데 최근 대본 V2 는 아직 3장 — 멘토 리뷰 #923 동선 */
const latestScriptIsShort = () =>
  vi
    .mocked(getScript)
    .mockImplementationOnce(async (_pitchId, id) => script(id, 1, 3))
    .mockImplementationOnce(async (_pitchId, id) => script(id, 2, 3));
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

  it('원문이 없는 예전 대본은 장 대본을 구분자와 함께 이어 다시 매핑해도 장마다 나뉜다', async () => {
    vi.mocked(getScript).mockImplementation(async (_pitchId: string, id: string) => ({
      ...script(id, id === 'sv1' ? 1 : 2, 3),
      original_content: null,
    }));
    const draft = await loadPitch('p1');
    // 다른 시험이 쓰는 기본(대본 V2 는 4장)으로 되돌립니다
    vi.mocked(getScript).mockImplementation(async (_pitchId: string, id: string) =>
      id === 'sv1' ? script(id, 1, 3) : script(id, 2, 4),
    );
    const { text } = draft.scripts[0]!;
    expect(text).toBe('[슬라이드 1]\nsv1-1\n\n[슬라이드 2]\nsv1-2\n\n[슬라이드 3]\nsv1-3');
    expect(splitScript(text)).toMatchObject({ segmented: true });
    expect(splitScript(text).slides.map((s) => s.content)).toEqual(['sv1-1', 'sv1-2', 'sv1-3']);
  });

  it('지난 대본은 장수가 같은 가장 최근 슬라이드에, 가장 최근 대본은 최신 슬라이드에 연결한다', async () => {
    const draft = await loadPitch('p1');
    expect(draft.scripts.map((s) => s.slideVersion)).toEqual([1, 2]);
  });

  it('가장 최근 대본은 장수가 달라도 최신 슬라이드에 붙고, 매핑을 다시 확인받는다 (멘토 리뷰 #923)', async () => {
    latestScriptIsShort();
    const draft = await loadPitch('p1');
    // 3장짜리 예전 슬라이드(V1)에 붙이면 새로고침 뒤 모든 단계가 완료로 보이고 V1 으로 연습합니다
    expect(draft.scripts.map((s) => [s.slideVersion, s.saved])).toEqual([
      [1, true],
      [2, false],
    ]);
  });

  it('지난 대본은 맞는 슬라이드가 없어도 저장된 채로 둔다 — "+ 새 버전"을 막지 않는다', async () => {
    vi.mocked(getScript)
      .mockImplementationOnce(async (_pitchId, id) => script(id, 1, 5))
      .mockImplementationOnce(async (_pitchId, id) => script(id, 2, 4));
    const draft = await loadPitch('p1');
    expect(draft.scripts.map((s) => [s.slideVersion, s.saved])).toEqual([
      [2, true],
      [2, true],
    ]);
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
  it('최신 대본과 최신 슬라이드로 바로 연습할 수 있다', async () => {
    store().hydrate('key1', 'p1', await loadPitch('p1'));

    expect(store()).toMatchObject({ pitchId: 'p1', entryKey: 'key1', node: 'slides' });
    expect(toPracticeCombo(store().draft, store().chosen)).toMatchObject({
      presentationVersionId: 'pv2',
      scriptVersionId: 'sv2',
    });
  });

  it('최신 슬라이드와 최근 대본의 장수가 다르면 예전 슬라이드로 연습을 시작하지 않는다 (멘토 리뷰 #923)', async () => {
    latestScriptIsShort();
    store().hydrate('key1', 'p1', await loadPitch('p1'));
    expect(toPracticeCombo(store().draft, store().chosen)).toBeNull();
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
