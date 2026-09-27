import { create } from 'zustand';
import type { Chosen, DraftCriterion, DraftNode, PitchDraft, ScoringMode } from './lib/draft';
import { CHOOSE_LATEST, MAX_CRITERIA } from './lib/draft';

/**
 * 피치 생성 한 판의 초안.
 *
 * 왜 Zustand인가 — 화면 다섯 장이 **한 라우트 안에서** 좌측 사이드바 선택으로
 * 갈리고, 셋(슬라이드·대본·평가기준)이 서로의 상태를 봐야 합니다. 서버에서 온
 * 값이 아니고(그건 TanStack Query), 프레임 단위도 아닙니다(그건 ref).
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
   * 옛것으로 계속 연습하게 됩니다.
   */
  chosen: Chosen;
  chooseVersion: (node: DraftNode, version: number | null) => void;

  select: (node: DraftNode, version?: number | null) => void;

  setMeta: (
    patch: Partial<Pick<PitchDraft, 'title' | 'presentationDate' | 'timeLimitSec'>>,
  ) => void;

  /** 슬라이드 새 버전. 변환 전이라 장수는 아직 모릅니다 */
  addSlideVersion: () => void;
  /**
   * 파일을 받았습니다. 변환 대기 중인 버전이 있으면 거기 채우고, 없으면 새로 만듭니다.
   *
   * 한 동작으로 묶은 이유 — 나눠 부르면 "버전을 만들었는데 장수는 다음 렌더에
   * 들어오는" 중간 상태가 생기고, 그 틈에 진행 조건이 한 번 잘못 계산됩니다.
   */
  attachSlides: (pageCount: number) => void;
  /** 서버 변환이 끝나 장수가 정해졌을 때 */
  setPageCount: (version: number, pageCount: number) => void;

  /** 대본 새 버전. 목업 사이드바의 "+ 새로운 대본 추가" */
  addScriptVersion: (text?: string) => void;
  editScript: (version: number, text: string) => void;
  /** 매핑 실행 결과를 붙입니다 */
  setBlocks: (version: number, blocks: string[]) => void;

  addCriteriaVersion: () => void;
  addCriterion: (version: number) => void;
  editCriterion: (version: number, id: string, patch: Partial<Omit<DraftCriterion, 'id'>>) => void;
  removeCriterion: (version: number, id: string) => void;

  reset: () => void;
}

const EMPTY_DRAFT: PitchDraft = {
  title: '',
  presentationDate: '',
  // 5분. 목업의 기본값입니다
  timeLimitSec: 300,
  slides: [],
  scripts: [],
  criteria: [],
};

/** 버전 번호는 갈래 안에서 1부터 셉니다 — 서버의 presentation_versions 와 같은 규칙 */
const nextVersion = (list: { version: number }[]) => (list.at(-1)?.version ?? 0) + 1;

export const useCreateStore = create<CreateState>((set) => ({
  draft: EMPTY_DRAFT,
  node: 'slides',
  version: null,

  pitchId: null,
  draftId: crypto.randomUUID(),
  setPitchId: (id) => set({ pitchId: id }),

  chosen: CHOOSE_LATEST,
  chooseVersion: (node, version) => set((s) => ({ chosen: { ...s.chosen, [node]: version } })),

  select: (node, version = null) => set({ node, version }),

  setMeta: (patch) => set((s) => ({ draft: { ...s.draft, ...patch } })),

  addSlideVersion: () =>
    set((s) => {
      const version = nextVersion(s.draft.slides);
      return {
        draft: { ...s.draft, slides: [...s.draft.slides, { version, pageCount: null }] },
        node: 'slides',
        version,
      };
    }),

  attachSlides: (pageCount) =>
    set((s) => {
      const pending = s.draft.slides.at(-1);
      // 변환을 기다리던 버전이 있으면 그것을 채웁니다 — "+ 새로운 슬라이드 추가" 뒤의 경로
      if (pending && pending.pageCount === null) {
        return {
          draft: {
            ...s.draft,
            slides: s.draft.slides.map((v) =>
              v.version === pending.version ? { ...v, pageCount } : v,
            ),
          },
          node: 'slides' as const,
          version: pending.version,
        };
      }
      const version = nextVersion(s.draft.slides);
      return {
        draft: { ...s.draft, slides: [...s.draft.slides, { version, pageCount }] },
        node: 'slides' as const,
        version,
      };
    }),

  setPageCount: (version, pageCount) =>
    set((s) => ({
      draft: {
        ...s.draft,
        slides: s.draft.slides.map((v) => (v.version === version ? { ...v, pageCount } : v)),
      },
    })),

  addScriptVersion: (text = '') =>
    set((s) => {
      const version = nextVersion(s.draft.scripts);
      return {
        draft: { ...s.draft, scripts: [...s.draft.scripts, { version, text, blocks: null }] },
        node: 'script',
        version,
      };
    }),

  editScript: (version, text) =>
    set((s) => ({
      draft: {
        ...s.draft,
        scripts: s.draft.scripts.map((v) =>
          // 글자가 바뀌면 지난 매핑은 근거를 잃습니다. 다시 실행해야 합니다.
          v.version === version ? { ...v, text, blocks: null } : v,
        ),
      },
    })),

  setBlocks: (version, blocks) =>
    set((s) => ({
      draft: {
        ...s.draft,
        scripts: s.draft.scripts.map((v) => (v.version === version ? { ...v, blocks } : v)),
      },
    })),

  addCriteriaVersion: () =>
    set((s) => {
      const version = nextVersion(s.draft.criteria);
      return {
        draft: { ...s.draft, criteria: [...s.draft.criteria, { version, items: [] }] },
        node: 'criteria',
        version,
      };
    }),

  addCriterion: (version) =>
    set((s) => ({
      draft: {
        ...s.draft,
        criteria: s.draft.criteria.map((v) =>
          v.version === version && v.items.length < MAX_CRITERIA
            ? {
                ...v,
                items: [...v.items, { id: crypto.randomUUID(), text: '', scoring: 'AUTO' }],
              }
            : v,
        ),
      },
    })),

  editCriterion: (version, id, patch) =>
    set((s) => ({
      draft: {
        ...s.draft,
        criteria: s.draft.criteria.map((v) =>
          v.version === version
            ? { ...v, items: v.items.map((it) => (it.id === id ? { ...it, ...patch } : it)) }
            : v,
        ),
      },
    })),

  removeCriterion: (version, id) =>
    set((s) => ({
      draft: {
        ...s.draft,
        criteria: s.draft.criteria.map((v) =>
          v.version === version ? { ...v, items: v.items.filter((it) => it.id !== id) } : v,
        ),
      },
    })),

  reset: () =>
    set({
      draft: EMPTY_DRAFT,
      node: 'slides',
      version: null,
      pitchId: null,
      draftId: crypto.randomUUID(),
      chosen: CHOOSE_LATEST,
    }),
}));

export type { ScoringMode };
