import { create } from 'zustand';
import type {
  Chosen,
  DraftNode,
  InfoForm,
  PaneNode,
  PitchDraft,
  ScriptParse,
  ScriptVersion,
  SlideUpload,
  UploadedSlides,
} from './lib/draft';
import {
  CHOOSE_LATEST,
  canSaveMapping,
  MAX_CRITERIA,
  infoChanged,
  infoOf,
  latestUploadedSlides,
} from './lib/draft';

/**
 * 피치 생성 한 판의 초안.
 *
 * 왜 Zustand인가 — 화면 다섯 장이 **한 라우트 안에서** 좌측 사이드바 선택으로
 * 갈리고, 셋(슬라이드·대본·평가기준)이 서로의 상태를 봐야 합니다. 서버에서 온
 * 값이 아니고(그건 TanStack Query), 프레임 단위도 아닙니다(그건 ref).
 * 상태 배치표의 가운데 칸입니다 — prepareStore 와 같은 이유입니다.
 *
 * 각 저장 지점(발표정보 저장 · 슬라이드 업로드 · 평가기준 정리 · 대본 매핑)에서 서버로 올리고,
 * 서버가 준 id 를 버전 안에 들고 있습니다.
 *
 * ★ 새로고침하면 사라집니다. 서버에는 남아 있지만, 다시 열어 사이드바를 채우는 일
 *   (`GET /pitches/{id}/resources`)은 아직 붙지 않았습니다.
 */

interface CreateState {
  draft: PitchDraft;
  /** 본문에 무엇을 띄울지. 사이드바에서 고른 것 */
  node: PaneNode;
  /** 고른 버전. null 이면 그 갈래의 최신 */
  version: number | null;

  /**
   * 서버가 준 pitch id. 발표정보를 처음 저장할 때(`POST /pitches/add`) 생깁니다.
   * 발표자료 · 대본 · 평가기준은 전부 이 id 아래에 올라가므로, null 인 동안에는
   * 그 화면들이 "발표정보를 먼저 저장해 주세요"로 막습니다.
   */
  pitchId: string | null;
  setPitchId: (id: string) => void;

  /**
   * 이번 연습에 들고 갈 버전. 슬라이드와 대본은 매핑을 저장할 때 함께 정해집니다.
   * `null` 은 최신입니다 — 고르지 않은 것을 번호로 박아 두면 새 버전을 올려도
   * 옛것으로 계속 연습하게 됩니다.
   */
  chosen: Chosen;
  chooseVersion: (node: DraftNode, version: number | null) => void;

  select: (node: PaneNode, version?: number | null) => void;

  /**
   * 발표정보 화면에서 고치는 중인 값. 저장하기 전까지는 `draft` 에 넣지 않습니다 —
   * 위 정보 줄이 입력 중인 글자를 따라 깜빡이지 않게. 화면을 떠났다 와도 남도록 여기 둡니다.
   * 고친 게 없으면 null 입니다.
   */
  infoEdits: InfoForm | null;
  editInfo: (patch: Partial<InfoForm>) => void;
  /** 발표정보 저장이 서버에서 끝났습니다. 고치던 값은 비웁니다 */
  setMeta: (saved: InfoForm) => void;

  /** 슬라이드 새 버전. 올리기 전이라 장수는 아직 모릅니다 */
  addSlideVersion: () => void;
  /** 업로드 진행 상태. 흐름은 `slideUpload.ts` 가 몹니다 */
  slideUpload: SlideUpload;
  setSlideUpload: (next: SlideUpload) => void;
  /**
   * 올리고 열기까지 끝났습니다. 비어 있는 버전이 있으면 거기 채우고, 없으면 새로 만듭니다.
   *
   * 한 동작으로 묶은 이유 — 나눠 부르면 "버전을 만들었는데 장수는 다음 렌더에
   * 들어오는" 중간 상태가 생기고, 그 틈에 진행 조건이 한 번 잘못 계산됩니다.
   */
  attachSlides: (uploaded: UploadedSlides, targetVersion?: number | null) => void;
  /** "파일 교체" — 이 버전의 파일만 바꿉니다. 버전 번호는 그대로입니다 */
  replaceSlides: (version: number, uploaded: UploadedSlides) => void;

  /** 대본 새 버전. 직전 글을 물려받고, 마지막으로 올린 슬라이드에 연결합니다 */
  addScriptVersion: (text?: string) => void;
  editScript: (version: number, text: string) => void;
  /** 매핑 편집은 원문에도 반영하고 서버 버전은 다시 올릴 때까지 분리합니다. */
  editMappingBlock: (version: number, index: number, text: string) => void;
  /**
   * 대본을 맞춰 볼 슬라이드 버전을 바꿉니다. 서버는 슬라이드를 모르고 구분자로만 나누므로
   * 나눈 결과는 그대로 두고, 장수가 맞는지는 진행 조건이 다시 봅니다.
   */
  linkSlides: (version: number, slideVersion: number) => void;
  /** 서버 쪽 진행을 적습니다. `scriptParse.ts` 만 부릅니다 */
  patchScript: (
    version: number,
    patch: Partial<Pick<ScriptVersion, 'remote' | 'parse' | 'blocks' | 'segmented'>>,
  ) => void;
  /** 매핑 확인에서 대본 입력으로 돌아갑니다. 다시 매핑하면 서버에 새 버전으로 올라갑니다 */
  unmapScript: (version: number) => void;
  /** 매핑 확인의 "저장" — 이 대본과 연결한 슬라이드를 연습할 조합으로 정합니다 */
  saveMapping: (version: number) => void;

  /** 평가기준 새 버전. 원문을 물려받습니다 — 대개 "조금 고쳐서 다시 정리"입니다 */
  addCriteriaVersion: () => void;
  /**
   * 서버가 나누고 저장한 결과를 넣습니다. "+ 새 버전"으로 만든 빈 버전이면 거기 채우고,
   * 아니면 새 버전을 만듭니다 — 서버도 정리할 때마다 새로 저장하고, 저장한 것은 고치지 않습니다.
   */
  applyParsedCriteria: (
    version: number | null,
    parsed: { sourceText: string; standards: string[]; exceptText: string | null },
  ) => void;

  reset: () => void;
}

const EMPTY_DRAFT: PitchDraft = {
  title: '',
  presentationDate: '',
  // 5분 · −30초 / +1분. 목업의 기본값입니다
  timeLimitSec: 300,
  lowerToleranceSec: 30,
  upperToleranceSec: 60,
  slides: [],
  scripts: [],
  criteria: [],
};

/** 버전 번호는 갈래 안에서 1부터 셉니다 — 서버의 presentation_versions 와 같은 규칙 */
const nextVersion = (list: { version: number }[]) => (list.at(-1)?.version ?? 0) + 1;

/** 서버와의 연결을 끊은 대본. 글이 바뀌면 서버의 버전과 더는 같지 않습니다 */
const DETACHED: Pick<ScriptVersion, 'remote' | 'parse' | 'blocks' | 'segmented' | 'saved'> = {
  remote: null,
  parse: { status: 'idle' } satisfies ScriptParse,
  blocks: null,
  segmented: null,
  saved: false,
};

const mapScripts = (
  draft: PitchDraft,
  version: number,
  update: (v: ScriptVersion) => ScriptVersion,
): PitchDraft => ({
  ...draft,
  scripts: draft.scripts.map((v) => (v.version === version ? update(v) : v)),
});

export const useCreateStore = create<CreateState>((set) => ({
  draft: EMPTY_DRAFT,
  // 목업의 순서대로 발표정보부터 엽니다
  node: 'info',
  version: null,

  pitchId: null,
  setPitchId: (id) => set({ pitchId: id }),

  chosen: CHOOSE_LATEST,
  chooseVersion: (node, version) => set((s) => ({ chosen: { ...s.chosen, [node]: version } })),

  select: (node, version = null) => set({ node, version }),

  infoEdits: null,
  editInfo: (patch) =>
    set((s) => ({ infoEdits: { ...(s.infoEdits ?? infoOf(s.draft)), ...patch } })),

  setMeta: (saved) => set((s) => ({ draft: { ...s.draft, ...saved }, infoEdits: null })),

  addSlideVersion: () =>
    set((s) => {
      const version = nextVersion(s.draft.slides);
      return {
        draft: { ...s.draft, slides: [...s.draft.slides, { version, pageCount: null }] },
        node: 'slides',
        version,
      };
    }),

  slideUpload: { status: 'idle' },
  setSlideUpload: (next) => set({ slideUpload: next }),

  attachSlides: (uploaded, targetVersion) =>
    set((s) => {
      const pending =
        targetVersion === undefined
          ? s.draft.slides.at(-1)
          : s.draft.slides.find((v) => v.version === targetVersion);
      // 파일을 기다리던 빈 버전이 있으면 그것을 채웁니다 — "+ 새 버전" 뒤의 경로
      if (pending && pending.pageCount === null) {
        return {
          draft: {
            ...s.draft,
            slides: s.draft.slides.map((v) =>
              v.version === pending.version ? { ...v, ...uploaded } : v,
            ),
          },
          node: 'slides' as const,
          version: pending.version,
        };
      }
      const version = nextVersion(s.draft.slides);
      return {
        draft: { ...s.draft, slides: [...s.draft.slides, { version, ...uploaded }] },
        node: 'slides' as const,
        version,
      };
    }),

  replaceSlides: (version, uploaded) =>
    set((s) => ({
      draft: {
        ...s.draft,
        slides: s.draft.slides.map((v) => (v.version === version ? { ...v, ...uploaded } : v)),
        // 이 슬라이드로 정한 연습 조합은 다시 확인해야 합니다 — 장수나 내용이 바뀌었을 수 있습니다
        scripts: s.draft.scripts.map((v) =>
          v.slideVersion === version ? { ...v, saved: false } : v,
        ),
      },
    })),

  addScriptVersion: (text = '') =>
    set((s) => {
      const version = nextVersion(s.draft.scripts);
      const slideVersion = latestUploadedSlides(s.draft)?.version ?? null;
      return {
        draft: {
          ...s.draft,
          scripts: [...s.draft.scripts, { version, text, slideVersion, ...DETACHED }],
        },
        node: 'script',
        version,
      };
    }),

  // 글자가 바뀌면 서버에 올린 것과 달라집니다. 다시 매핑하면 새 버전으로 올라갑니다
  editScript: (version, text) =>
    set((s) => ({ draft: mapScripts(s.draft, version, (v) => ({ ...v, text, ...DETACHED })) })),

  editMappingBlock: (version, index, text) =>
    set((s) => ({
      draft: mapScripts(s.draft, version, (v) => {
        if (!v.blocks || index < 0 || index >= v.blocks.length || v.parse.status === 'pending')
          return v;
        const blocks = v.blocks.map((block, i) => (i === index ? text : block));
        return {
          ...v,
          ...DETACHED,
          blocks,
          text: blocks.map((block, i) => `슬라이드 ${i + 1}\n${block}`).join('\n\n'),
        };
      }),
    })),

  linkSlides: (version, slideVersion) =>
    set((s) => ({
      draft: mapScripts(s.draft, version, (v) => ({ ...v, slideVersion, saved: false })),
    })),

  patchScript: (version, patch) =>
    set((s) => ({ draft: mapScripts(s.draft, version, (v) => ({ ...v, ...patch })) })),

  unmapScript: (version) =>
    set((s) => ({ draft: mapScripts(s.draft, version, (v) => ({ ...v, ...DETACHED })) })),

  saveMapping: (version) =>
    set((s) => {
      const script = s.draft.scripts.find((v) => v.version === version);
      const slide = s.draft.slides.find((v) => v.version === script?.slideVersion);
      if (!script || !canSaveMapping(script, slide)) return s;
      return {
        draft: {
          ...s.draft,
          scripts: s.draft.scripts.map((v) => (v.version === version ? { ...v, saved: true } : v)),
        },
        // ★ 저장한 조합이 곧 연습할 조합입니다 — 둘을 따로 고르면 장수가 어긋날 수 있습니다
        chosen: { ...s.chosen, slides: script.slideVersion, script: version },
      };
    }),

  addCriteriaVersion: () =>
    set((s) => {
      const version = nextVersion(s.draft.criteria);
      return {
        draft: {
          ...s.draft,
          criteria: [
            ...s.draft.criteria,
            {
              version,
              items: [],
              saved: false,
              sourceText: s.draft.criteria.at(-1)?.sourceText ?? '',
            },
          ],
        },
        node: 'criteria',
        version,
      };
    }),

  applyParsedCriteria: (version, { sourceText, standards, exceptText }) =>
    set((s) => {
      const items = standards
        .slice(0, MAX_CRITERIA)
        .map((text) => ({ id: crypto.randomUUID(), text }));
      const target = s.draft.criteria.find((v) => v.version === version);

      if (!target || target.saved) {
        const next = nextVersion(s.draft.criteria);
        return {
          draft: {
            ...s.draft,
            criteria: [
              ...s.draft.criteria,
              { version: next, items, saved: true, sourceText, exceptText },
            ],
          },
          node: 'criteria' as const,
          version: next,
        };
      }
      return {
        draft: {
          ...s.draft,
          criteria: s.draft.criteria.map((v) =>
            v.version === target.version ? { ...v, items, saved: true, sourceText, exceptText } : v,
          ),
        },
      };
    }),

  reset: () =>
    set({
      draft: EMPTY_DRAFT,
      node: 'info',
      version: null,
      pitchId: null,
      infoEdits: null,
      chosen: CHOOSE_LATEST,
      slideUpload: { status: 'idle' },
    }),
}));

/** 발표정보를 고치고 아직 저장하지 않았나. 사이드바 표시와 떠나기 경고가 함께 봅니다 */
export const selectInfoUnsaved = (s: CreateState): boolean =>
  s.infoEdits !== null && infoChanged(s.infoEdits, infoOf(s.draft));

/** 서버로 가는 중인 일이 있나 — 슬라이드 업로드 · 대본 나누기. 지금 떠나면 결과를 받을 곳이 없습니다 */
export const selectBusy = (s: CreateState): boolean =>
  s.slideUpload.status === 'uploading' || s.draft.scripts.some((v) => v.parse.status === 'pending');
