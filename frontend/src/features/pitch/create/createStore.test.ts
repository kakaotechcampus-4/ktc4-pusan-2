import { beforeEach, expect, it } from 'vitest';
import { useCreateStore } from './createStore';
import { CHOOSE_LATEST, toPracticeCombo, type UploadedSlides } from './lib/draft';

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
  store().addScriptVersion('슬라이드 1\n처음\n\n슬라이드 2\n마지막');
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
    text: '슬라이드 1\n수정한 문장\n\n슬라이드 2\n마지막',
    remote: null,
    saved: false,
  });
  store().saveMapping(1);
  expect(toPracticeCombo(store().draft, store().chosen)).toBeNull();
  store().unmapScript(1);
  expect(store().draft.scripts[0]?.text).toContain('수정한 문장');
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
