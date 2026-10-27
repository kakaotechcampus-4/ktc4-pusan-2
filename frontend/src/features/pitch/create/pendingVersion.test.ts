import { beforeEach, describe, expect, it } from 'vitest';
import { useCreateStore } from './createStore';
import { pendingVersion, type UploadedSlides } from './lib/draft';

const store = () => useCreateStore.getState();
const versions = (list: { version: number }[]) => list.map((v) => v.version);
const uploaded: UploadedSlides = {
  pageCount: 2,
  fileUrl: '/slides.pdf',
  fileName: 'slides.pdf',
  presentationVersionId: 'pv1',
};

beforeEach(() => {
  store().reset();
  store().setPitchId('pitch1');
});

/**
 * 버전은 지우지 않습니다 (지난 Take 가 가리킵니다). 대신 저장 안 한 버전이 있으면 새 버전을 만들지 않아
 * 빈 버전이 끝없이 쌓이지 않게 합니다 — 새 버전을 누르면 그 저장 안 한 버전을 엽니다.
 */
describe('저장 안 한 버전이 있으면 새 버전을 만들지 않는다', () => {
  it('평가기준 — "+ 새 버전"을 여러 번 눌러도 빈 버전은 하나다', () => {
    store().addCriteriaVersion();
    store().addCriteriaVersion();
    store().addCriteriaVersion();
    expect(versions(store().draft.criteria)).toEqual([1]);
    expect(pendingVersion(store().draft, 'criteria')).toBe(1);
  });

  it('평가기준 — 정리하면 서버에 저장되므로 바로 새 버전을 만들 수 있다 (#463)', () => {
    store().applyParsedCriteria(null, {
      version: 1,
      sourceText: '가',
      standards: ['가'],
      exceptText: null,
    });
    expect(pendingVersion(store().draft, 'criteria')).toBeNull();
    store().addCriteriaVersion();
    expect(versions(store().draft.criteria)).toEqual([1, 2]);
  });

  it('평가기준 — 고친 내용을 저장하지 않았으면 새 버전을 만들지 않고 그 버전을 연다', () => {
    store().applyParsedCriteria(null, {
      version: 1,
      sourceText: '가',
      standards: ['가'],
      exceptText: null,
    });
    const id = store().draft.criteria[0]!.items[0]!.id;
    store().editCriterion(1, id, '가나');
    expect(pendingVersion(store().draft, 'criteria')).toBe(1);
    store().addCriteriaVersion();
    expect(versions(store().draft.criteria)).toEqual([1]);

    store().markCriteriaSaved(1, {
      version: 2,
      standards: ['가나'],
      exceptText: null,
      sourceText: '가나',
    });
    expect(pendingVersion(store().draft, 'criteria')).toBeNull();
  });

  it('평가기준 — "수정하기"도 같은 규칙이다. 아직 정리하지 않은 버전이 있으면 그 버전을 연다', () => {
    store().applyParsedCriteria(null, {
      version: 1,
      sourceText: '가',
      standards: ['가'],
      exceptText: null,
    });
    store().reviseCriteria(1);
    store().reviseCriteria(1);
    expect(versions(store().draft.criteria)).toEqual([1, 2]);
    expect(store().version).toBe(2);
  });

  it('슬라이드 — 파일을 기다리는 빈 버전이 있으면 새 버전을 만들지 않는다', () => {
    store().attachSlides(uploaded);
    store().addSlideVersion();
    store().addSlideVersion();
    expect(versions(store().draft.slides)).toEqual([1, 2]);
    expect(pendingVersion(store().draft, 'slides')).toBe(2);
  });

  it('대본 — 매핑을 저장하지 않은 버전이 있으면 새 버전을 만들지 않고 그 버전을 연다', () => {
    store().addScriptVersion('슬라이드 1\n가');
    store().select('criteria');
    store().addScriptVersion('다른 글');
    expect(versions(store().draft.scripts)).toEqual([1]);
    expect(store().node).toBe('script');
    expect(store().version).toBe(1);
  });
});
