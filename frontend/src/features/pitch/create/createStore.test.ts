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

it('같은 장수의 PDF를 교체해도 확인 전에는 시작할 수 없다', () => {
  store().replaceSlides(1, { ...uploaded, presentationVersionId: 'pv2' });
  expect(store().draft.scripts[0]?.saved).toBe(false);
  expect(toPracticeCombo(store().draft, store().chosen)).toBeNull();
  store().saveMapping(1);
  expect(toPracticeCombo(store().draft, store().chosen)?.presentationVersionId).toBe('pv2');
});

it('장수가 같아도 연결하지 않은 슬라이드 버전으로 시작하지 않는다', () => {
  store().addSlideVersion();
  store().attachSlides({ ...uploaded, presentationVersionId: 'pv2' });
  expect(toPracticeCombo(store().draft, { ...CHOOSE_LATEST, slides: 2, script: 1 })).toBeNull();
});

it('이전 빈 버전에 시작한 업로드는 화면을 옮겨도 그 버전에 들어간다', () => {
  store().addSlideVersion();
  store().addSlideVersion();
  store().select('script', 1);
  store().attachSlides({ ...uploaded, presentationVersionId: 'pv2' }, 2);
  expect(store().draft.slides.find((v) => v.version === 2)?.presentationVersionId).toBe('pv2');
  expect(store().draft.slides.find((v) => v.version === 3)?.pageCount).toBeNull();
});

it('파싱 중에는 매핑을 바꾸거나 저장할 수 없다', () => {
  store().editMappingBlock(1, 0, '수정');
  store().patchScript(1, { parse: { status: 'pending' } });
  store().editMappingBlock(1, 0, '다른 내용');
  store().saveMapping(1);
  expect(store().draft.scripts[0]?.blocks?.[0]).toBe('수정');
  expect(store().draft.scripts[0]?.saved).toBe(false);
});
