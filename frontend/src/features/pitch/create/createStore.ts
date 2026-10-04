import { create } from 'zustand';
import type { Chosen, CriteriaVersion, DraftNode, PitchDraft, ScriptVersion } from './lib/draft';
import { CHOOSE_LATEST } from './lib/draft';
import { organizeCriteria } from './lib/criteria';

/**
 * 피치 생성 한 판의 초안.
 *
 * 왜 Zustand인가 — 화면 여섯 장이 **한 라우트 안에서** 좌측 사이드바 선택으로
 * 갈리고, 넷(발표정보·슬라이드·대본·평가기준)이 서로의 상태를 봐야 합니다.
 * 서버에서 온 값이 아니고(그건 TanStack Query), 프레임 단위도 아닙니다(그건 ref).
 * 상태 배치표의 가운데 칸입니다 — prepareStore 와 같은 이유입니다.
 *
 * ★ 지금은 서버가 없어서 전부 메모리에만 있습니다. 새로고침하면 사라집니다.
 *   업로드·저장 API 가 생기면 각 저장 지점에서 서버로 올리고, 이 스토어는
 *   "아직 저장하지 않은 편집분"만 들고 있게 됩니다.
 */

interface CreateState {
  draft: PitchDraft;
  /** 본문에 무엇을 띄울지. 사이드바에서 고른 것 */
  node: DraftNode;
  /** 고른 버전. null 이면 그 갈래의 최신 */
  version: number | null;

  /**
   * 서버가 준 pitch id. **생성 API(`POST /pitches`)가 아직 없어서 늘 null 입니다.**
   *
   * 뒤 화면(장치 점검 · 준비)은 URL 의 `pitchId` 로 서버를 부릅니다. 그래서 null 인
   * 동안에는 아래 `draftId` 로 대신 이동합니다 — 목이 어떤 id 로도 답하므로 흐름은
   * 확인되지만 **서버에 저장된 것은 아무것도 없습니다.** API 가 붙으면 응답의 id 를
   * `setPitchId` 로 넣고, 그 순간부터 draftId 는 쓰이지 않습니다.
   */
  pitchId: string | null;
  /** 저장 전 임시 식별자. 스토어가 만들어질 때 한 번 정해집니다 */
  draftId: string;
  setPitchId: (id: string) => void;

  /**
   * 이번 연습에 들고 갈 버전. 셋을 **따로** 고릅니다 (슬라이드 V2 · 대본 V1 …).
   * `null` 은 최신입니다 — 고르지 않은 것을 번호로 박아 두면 새 버전을 올려도
   * 옛것으로 계속 연습하게 됩니다. 고르는 곳은 시작 직전의 확인 창(`StartConfirm`)입니다.
   */
  chosen: Chosen;
  chooseVersion: (node: Exclude<DraftNode, 'info'>, version: number | null) => void;

  select: (node: DraftNode, version?: number | null) => void;

  setMeta: (
    patch: Partial<
      Pick<
        PitchDraft,
        'title' | 'presentationDate' | 'timeLimitSec' | 'toleranceBelowSec' | 'toleranceAboveSec'
      >
    >,
  ) => void;
  saveInfo: () => void;

  /** 슬라이드 새 버전. 파일을 올리기 전이라 장수는 아직 모릅니다 */
  addSlideVersion: () => void;
  /**
   * 파일을 받았습니다. 파일을 기다리던 버전이 있으면 거기 채우고, 없으면 새로 만듭니다.
   *
   * 한 동작으로 묶은 이유 — 나눠 부르면 "버전을 만들었는데 장수는 다음 렌더에
   * 들어오는" 중간 상태가 생기고, 그 틈에 진행 조건이 한 번 잘못 계산됩니다.
   */
  attachSlides: (fileName: string, pageCount: number) => void;
  /** "파일 교체" — 같은 버전에 다른 파일을 올립니다. 장수가 바뀌면 매핑이 어긋납니다 */
  replaceSlides: (version: number, fileName: string, pageCount: number) => void;

  /** 대본 새 버전. 직전 글과 슬라이드 연결을 물려받습니다 */
  addScriptVersion: (text?: string) => void;
  editScript: (version: number, text: string) => void;
  /** "연결할 슬라이드" 를 바꿉니다. 장수가 달라질 수 있어 지난 매핑은 버립니다 */
  linkSlide: (version: number, slideVersion: number) => void;
  /** 매핑 실행 결과를 붙입니다. 아직 저장 전입니다 */
  setBlocks: (version: number, blocks: string[]) => void;
  editBlock: (version: number, index: number, text: string) => void;
  /** 매핑 확인을 마치고 저장합니다 */
  saveMapping: (version: number) => void;
  /** 매핑 화면에서 입력 화면으로 돌아갑니다. 글은 그대로, 나눈 결과만 버립니다 */
  clearMapping: (version: number) => void;

  addCriteriaVersion: () => void;
  editCriteriaSource: (version: number, source: string) => void;
  /** 원문을 항목으로 정리합니다. 아직 저장 전입니다 */
  organize: (version: number) => void;
  saveCriteria: (version: number) => void;

  reset: () => void;
}

const EMPTY_DRAFT: PitchDraft = {
  title: '',
  presentationDate: '',
  // 5분 · −30초 · +1분. 시안의 기본값입니다
  timeLimitSec: 300,
  toleranceBelowSec: 30,
  toleranceAboveSec: 60,
  infoSaved: false,
  slides: [],
  scripts: [],
  criteria: [],
};

/** 버전 번호는 갈래 안에서 1부터 셉니다 — 서버의 presentation_versions 와 같은 규칙 */
const nextVersion = (list: { version: number }[]) => (list.at(-1)?.version ?? 0) + 1;

const patchScript = (
  s: { draft: PitchDraft },
  version: number,
  patch: (v: ScriptVersion) => ScriptVersion,
) => ({
  draft: {
    ...s.draft,
    scripts: s.draft.scripts.map((v) => (v.version === version ? patch(v) : v)),
  },
});

const patchCriteria = (
  s: { draft: PitchDraft },
  version: number,
  patch: (v: CriteriaVersion) => CriteriaVersion,
) => ({
  draft: {
    ...s.draft,
    criteria: s.draft.criteria.map((v) => (v.version === version ? patch(v) : v)),
  },
});

export const useCreateStore = create<CreateState>((set) => ({
  draft: EMPTY_DRAFT,
  node: 'info',
  version: null,

  pitchId: null,
  draftId: crypto.randomUUID(),
  setPitchId: (id) => set({ pitchId: id }),

  chosen: CHOOSE_LATEST,
  chooseVersion: (node, version) => set((s) => ({ chosen: { ...s.chosen, [node]: version } })),

  select: (node, version = null) => set({ node, version }),

  // 값이 바뀌면 저장 표시가 풀립니다 — 저장한 뒤에 고친 값이 "저장됨" 으로 남으면 안 됩니다
  setMeta: (patch) =>
    set((s) => ({
      draft: {
        ...s.draft,
        ...patch,
        toleranceBelowSec: Math.min(
          patch.toleranceBelowSec ?? s.draft.toleranceBelowSec,
          patch.timeLimitSec ?? s.draft.timeLimitSec,
        ),
        infoSaved: false,
      },
    })),
  saveInfo: () => set((s) => ({ draft: { ...s.draft, infoSaved: true } })),

  addSlideVersion: () =>
    set((s) => {
      const version = nextVersion(s.draft.slides);
      return {
        draft: {
          ...s.draft,
          slides: [...s.draft.slides, { version, fileName: null, pageCount: null }],
        },
        node: 'slides',
        version,
      };
    }),

  attachSlides: (fileName, pageCount) =>
    set((s) => {
      const pending = s.draft.slides.find((v) => v.version === s.version) ?? s.draft.slides.at(-1);
      // 파일을 기다리던 버전이 있으면 그것을 채웁니다 — "+ 새 버전" 뒤의 경로
      if (pending && pending.pageCount === null) {
        return {
          draft: {
            ...s.draft,
            slides: s.draft.slides.map((v) =>
              v.version === pending.version ? { ...v, fileName, pageCount } : v,
            ),
          },
          node: 'slides' as const,
          version: pending.version,
        };
      }
      const version = nextVersion(s.draft.slides);
      return {
        draft: { ...s.draft, slides: [...s.draft.slides, { version, fileName, pageCount }] },
        node: 'slides' as const,
        version,
      };
    }),

  replaceSlides: (version, fileName, pageCount) =>
    set((s) => ({
      draft: {
        ...s.draft,
        slides: s.draft.slides.map((v) =>
          v.version === version ? { ...v, fileName, pageCount } : v,
        ),
        scripts: s.draft.scripts.map((v) =>
          v.slideVersion === version ? { ...v, blocks: null, mappingSaved: false } : v,
        ),
      },
    })),

  addScriptVersion: (text = '') =>
    set((s) => {
      const version = nextVersion(s.draft.scripts);
      const slideVersion =
        s.draft.scripts.at(-1)?.slideVersion ?? s.draft.slides.at(-1)?.version ?? null;
      return {
        draft: {
          ...s.draft,
          scripts: [
            ...s.draft.scripts,
            { version, slideVersion, text, blocks: null, mappingSaved: false },
          ],
        },
        node: 'script',
        version,
      };
    }),

  // 글자가 바뀌면 지난 매핑은 근거를 잃습니다. 다시 실행해야 합니다.
  editScript: (version, text) =>
    set((s) => patchScript(s, version, (v) => ({ ...v, text, blocks: null, mappingSaved: false }))),

  linkSlide: (version, slideVersion) =>
    set((s) =>
      patchScript(s, version, (v) => ({ ...v, slideVersion, blocks: null, mappingSaved: false })),
    ),

  setBlocks: (version, blocks) =>
    set((s) => patchScript(s, version, (v) => ({ ...v, blocks, mappingSaved: false }))),

  editBlock: (version, index, text) =>
    set((s) =>
      patchScript(s, version, (v) => {
        const blocks = v.blocks?.map((b, i) => (i === index ? text : b)) ?? null;
        return { ...v, blocks, text: blocks?.join('\n\n') ?? v.text, mappingSaved: false };
      }),
    ),

  saveMapping: (version) =>
    set((s) => patchScript(s, version, (v) => ({ ...v, mappingSaved: true }))),

  clearMapping: (version) =>
    set((s) => patchScript(s, version, (v) => ({ ...v, blocks: null, mappingSaved: false }))),

  addCriteriaVersion: () =>
    set((s) => {
      const version = nextVersion(s.draft.criteria);
      return {
        draft: {
          ...s.draft,
          criteria: [
            ...s.draft.criteria,
            { version, source: '', organizedSource: '', items: [], skipped: [], saved: false },
          ],
        },
        node: 'criteria',
        version,
      };
    }),

  editCriteriaSource: (version, source) =>
    set((s) => patchCriteria(s, version, (v) => ({ ...v, source, saved: false }))),

  organize: (version) =>
    set((s) =>
      patchCriteria(s, version, (v) => {
        const { items, skipped } = organizeCriteria(v.source);
        return {
          ...v,
          items: items.map((text) => ({ id: crypto.randomUUID(), text })),
          skipped,
          organizedSource: v.source,
          saved: false,
        };
      }),
    ),

  saveCriteria: (version) => set((s) => patchCriteria(s, version, (v) => ({ ...v, saved: true }))),

  reset: () =>
    set({
      draft: EMPTY_DRAFT,
      node: 'info',
      version: null,
      pitchId: null,
      draftId: crypto.randomUUID(),
      chosen: CHOOSE_LATEST,
    }),
}));
