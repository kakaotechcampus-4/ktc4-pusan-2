import { beforeEach, expect, it } from 'vitest';
import { splitScript } from '@/mocks/scripts';
import { useCreateStore } from './createStore';
import { CHOOSE_LATEST, toPracticeCombo, type UploadedSlides } from './lib/draft';
import { mappingEditNotice } from './lib/markerHint';

const uploaded: UploadedSlides = {
  pageCount: 2,
  fileUrl: '/slides.pdf',
  fileName: 'slides.pdf',
  presentationVersionId: 'pv1',
};
const store = () => useCreateStore.getState();

beforeEach(() => {
  store().reset();
  store().setPitchId('pitch1');
  store().attachSlides(uploaded);
  store().addScriptVersion('[슬라이드 1]\n처음\n\n[슬라이드 2]\n마지막');
  store().patchScript(1, {
    remote: { id: 'sv1', version: 1 },
    blocks: ['처음', '마지막'],
    segmented: true,
  });
  store().saveMapping(1);
});

it('매핑 수정은 원문에 보존하고 서버 재매핑 전 연습을 막는다', () => {
  store().editMappingBlock(1, 0, '수정한 문장');
  expect(store().draft.scripts[0]).toMatchObject({
    text: '[슬라이드 1]\n수정한 문장\n\n[슬라이드 2]\n마지막',
    remote: null,
    saved: false,
  });
  store().saveMapping(1);
  expect(toPracticeCombo(store().draft, store().chosen)).toBeNull();
  store().unmapScript(1);
  expect(store().draft.scripts[0]?.text).toContain('수정한 문장');
});

it('매핑을 고친 원문을 다시 매핑해도 같은 장수 · 같은 글로 나뉜다', () => {
  store().editMappingBlock(1, 0, '수정한 문장');
  const { text } = store().draft.scripts[0]!;
  // 목은 AI 와 같은 구분자 규칙으로 나눕니다 — 콜론 없는 "슬라이드 1" 이면 1장으로 뭉칩니다
  const { segmented, slides } = splitScript(text);
  expect(segmented).toBe(true);
  expect(slides.map((s) => s.content)).toEqual(['수정한 문장', '마지막']);
});

// #826 리뷰: 장 대본에 구분자처럼 생긴 줄을 쓰면 다시 매핑할 때 장이 달라집니다 — 매핑 편집이 그 줄을 짚습니다
it.each([
  ['수정\n[1] 통계청 자료(2025)', '[1] 통계청 자료(2025)'],
  ['슬라이드 2: 요약으로 넘어가면', '슬라이드 2: 요약으로 넘어가면'],
  ['요약\n슬라이드 3', '슬라이드 3'], // script_parser-1.3 부터 구분자
])(
  '장 대본에 구분자로 읽히는 줄이 있으면 다시 매핑한 장이 달라지고, 편집 안내가 그 줄을 짚는다: %j',
  (block, line) => {
    store().editMappingBlock(1, 0, block);
    const { text, blocks } = store().draft.scripts[0]!;
    const { segmented, slides } = splitScript(text);
    expect(segmented && slides.length === 2).toBe(false);
    expect(mappingEditNotice(blocks!)).toContain(
      `1장 대본의 “${line}” 줄은 슬라이드 구분으로 읽혀요`,
    );
  },
);

it('구분자가 아닌 줄은 짚지 않고, 다시 매핑해도 같은 장수로 나뉜다', () => {
  store().editMappingBlock(1, 0, '출처는 [1] 통계청 자료\n슬라이드 3 보시면');
  const { text, blocks } = store().draft.scripts[0]!;
  expect(mappingEditNotice(blocks!)).toBeNull();
  expect(splitScript(text).slides.map((s) => s.content)).toEqual([
    '출처는 [1] 통계청 자료\n슬라이드 3 보시면',
    '마지막',
  ]);
});

it('서버가 수정 대본을 반환하고 저장한 뒤 새 서버 id로 연습한다', () => {
  store().editMappingBlock(1, 0, '수정');
  store().patchScript(1, {
    remote: { id: 'sv2', version: 2 },
    blocks: ['수정', '마지막'],
    segmented: true,
  });
  store().saveMapping(1);
  expect(toPracticeCombo(store().draft, store().chosen)?.scriptVersionId).toBe('sv2');
});

it('장수가 같아도 연결하지 않은 슬라이드 버전으로 시작하지 않는다', () => {
  store().addSlideVersion();
  store().attachSlides({ ...uploaded, presentationVersionId: 'pv2' });
  expect(toPracticeCombo(store().draft, { ...CHOOSE_LATEST, slides: 2, script: 1 })).toBeNull();
});

it('슬라이드를 다시 올리면 장수가 같은 대본을 새 슬라이드로 옮기고 매핑을 다시 확인받는다', () => {
  store().attachSlides({ ...uploaded, presentationVersionId: 'pv2' });
  expect(store().draft.scripts[0]).toMatchObject({ slideVersion: 2, saved: false });
  expect(toPracticeCombo(store().draft, store().chosen)).toBeNull();
  store().saveMapping(1);
  expect(toPracticeCombo(store().draft, store().chosen)?.presentationVersionId).toBe('pv2');
});

it('파일을 기다리던 빈 슬라이드 버전을 채워도 대본을 옮기고 매핑을 다시 확인받는다', () => {
  store().addSlideVersion();
  store().attachSlides({ ...uploaded, presentationVersionId: 'pv2' });
  expect(store().draft.slides.map((v) => v.version)).toEqual([1, 2]);
  expect(store().draft.scripts[0]).toMatchObject({ slideVersion: 2, saved: false });
  store().saveMapping(1);
  expect(toPracticeCombo(store().draft, store().chosen)?.presentationVersionId).toBe('pv2');
});

it('아직 나누지 않은 대본은 새 슬라이드로 옮기되 저장 상태를 두어 새 버전을 막지 않는다', () => {
  // 다시 들어온 피치에서 나누는 중이거나 실패한 대본 — 서버에 올렸지만 `blocks` 가 없습니다 (`loadPitch.ts`)
  store().patchScript(1, { blocks: null, segmented: null });
  store().attachSlides({ ...uploaded, pageCount: 3, presentationVersionId: 'pv2' });
  expect(store().draft.scripts[0]).toMatchObject({ slideVersion: 2, saved: true });
  store().addScriptVersion();
  expect(store().draft.scripts.map((v) => v.version)).toEqual([1, 2]);
});

it('슬라이드를 다시 올려도 지난 대본 버전은 그대로라 새 대본 버전을 만들 수 있다', () => {
  store().addScriptVersion('슬라이드 1\n둘째\n\n슬라이드 2\n버전');
  store().patchScript(2, {
    remote: { id: 'sv2', version: 2 },
    blocks: ['둘째', '버전'],
    segmented: true,
  });
  store().saveMapping(2);
  store().attachSlides({ ...uploaded, presentationVersionId: 'pv2' });
  expect(store().draft.scripts[0]).toMatchObject({ slideVersion: 1, saved: true });
  store().saveMapping(2);
  store().addScriptVersion();
  expect(store().draft.scripts.map((v) => v.version)).toEqual([1, 2, 3]);
});

it('지난 대본 버전을 골라 두었으면 그 버전을 새 슬라이드로 옮긴다', () => {
  store().addScriptVersion('슬라이드 1\n둘째\n\n슬라이드 2\n버전');
  store().patchScript(2, {
    remote: { id: 'sv2', version: 2 },
    blocks: ['둘째', '버전'],
    segmented: true,
  });
  store().saveMapping(2);
  store().select('script', 1);
  store().select('slides', 1);
  store().attachSlides({ ...uploaded, presentationVersionId: 'pv2' });
  expect(store().draft.scripts.map((v) => [v.slideVersion, v.saved])).toEqual([
    [2, false],
    [1, true],
  ]);
});

it('장수가 다른 슬라이드를 올리면 대본은 앞 슬라이드에 남는다', () => {
  store().attachSlides({ ...uploaded, pageCount: 3, presentationVersionId: 'pv2' });
  expect(store().draft.scripts[0]).toMatchObject({ slideVersion: 1, saved: true });
  expect(toPracticeCombo(store().draft, store().chosen)?.presentationVersionId).toBe('pv1');
});

it('빈 버전에 시작한 업로드는 화면을 옮겨도 그 버전에 들어간다', () => {
  store().addSlideVersion();
  // 파일을 기다리는 빈 버전(V2)이 있으면 새 버전을 더 만들지 않습니다 (pendingVersion)
  store().addSlideVersion();
  expect(store().draft.slides.map((v) => v.version)).toEqual([1, 2]);
  store().select('script', 1);
  store().attachSlides({ ...uploaded, presentationVersionId: 'pv2' }, 2);
  expect(store().draft.slides.find((v) => v.version === 2)?.presentationVersionId).toBe('pv2');
});

it('파싱 중에는 매핑을 바꾸거나 저장할 수 없다', () => {
  store().editMappingBlock(1, 0, '수정');
  store().patchScript(1, { parse: { status: 'pending' } });
  store().editMappingBlock(1, 0, '다른 내용');
  store().saveMapping(1);
  expect(store().draft.scripts[0]?.blocks?.[0]).toBe('수정');
  expect(store().draft.scripts[0]?.saved).toBe(false);
});

it('이번 작성의 업로드만 확인 필요이며 확인·화면 이동·서버 재조회 규칙을 지킨다', () => {
  expect(store().pendingSlideReviews).toEqual([1]);
  expect(store().slideReviewVersion).toBe(1);
  store().setSlideReview(null);
  store().select('info');
  expect(store().pendingSlideReviews).toEqual([1]);
  store().confirmSlides(99);
  expect(store().pendingSlideReviews).toEqual([1]);
  store().confirmSlides(1);
  expect(store().pendingSlideReviews).toEqual([]);
  store().attachSlides({ ...uploaded, presentationVersionId: 'pv2' });
  expect(store().pendingSlideReviews).toEqual([2]);
  const draft = store().draft;
  store().hydrate('re-entered', 'pitch1', draft);
  expect(store().pendingSlideReviews).toEqual([]);
  expect(store().slideReviewVersion).toBeNull();
});

it('평가기준 정리·저장 응답은 확인 필요이고 수정 중에는 확인할 수 없다', () => {
  store().applyParsedCriteria(null, {
    version: 1,
    standards: ['기준'],
    sourceText: '원문',
    exceptText: null,
  });
  expect(store().pendingCriteriaReviews).toEqual([1]);
  store().editCriterion(1, store().draft.criteria[0]!.items[0]!.id, '수정');
  store().confirmCriteria(1);
  expect(store().pendingCriteriaReviews).toEqual([1]);
  store().markCriteriaSaved(1, {
    version: 2,
    standards: ['AI가 다시 다듬은 기준'],
    sourceText: '수정',
    exceptText: null,
  });
  expect(store().pendingCriteriaReviews).toContain(2);
  store().confirmCriteria(2);
  expect(store().pendingCriteriaReviews).not.toContain(2);
  store().hydrate('re-entered', 'pitch1', store().draft);
  expect(store().pendingCriteriaReviews).toEqual([]);
});
