import { beforeEach, describe, expect, it } from 'vitest';
import { selectCriteriaUnsaved, useCreateStore } from './createStore';
import { criteriaToSave, latestSavedCriteria } from './lib/draft';

const store = () => useCreateStore.getState();
const v1 = () => store().draft.criteria.find((v) => v.version === 1)!;
const texts = (version: number) =>
  store()
    .draft.criteria.find((v) => v.version === version)!
    .items.map((i) => i.text);

beforeEach(() => {
  store().reset();
  store().setPitchId('pitch1');
  store().applyParsedCriteria(null, {
    sourceText: '추임새는 5번 이하, 청중을 자주 보기',
    standards: ['추임새 5번 이하', '청중 자주 보기'],
    exceptText: null,
  });
});

describe('평가기준 — 정리 → 고치기 → 저장 (BE #192)', () => {
  it('정리만 하면 저장 안 됨이다 — 연습에 쓰지 않고, 떠나면 경고한다', () => {
    expect(v1().saved).toBe(false);
    expect(latestSavedCriteria(store().draft)).toBeNull();
    expect(selectCriteriaUnsaved(store())).toBe(true);
  });

  it('저장 전에는 문장을 고치고 · 더하고 · 지울 수 있다', () => {
    const [first, second] = v1().items;
    store().editCriterion(1, first!.id, '추임새 3번 이하');
    store().removeCriterion(1, second!.id);
    store().addCriterion(1);
    store().editCriterion(1, v1().items.at(-1)!.id, '말 빠르지 않게');
    expect(texts(1)).toEqual(['추임새 3번 이하', '말 빠르지 않게']);
  });

  it('빈 줄과 앞뒤 공백은 빼고 저장할 목록을 만든다', () => {
    store().addCriterion(1);
    store().editCriterion(1, v1().items[0]!.id, '  추임새 5번 이하  ');
    expect(criteriaToSave(v1())).toEqual(['추임새 5번 이하', '청중 자주 보기']);
  });

  it('저장하면 그 버전으로 연습하고, 저장한 목록은 더 고칠 수 없다', () => {
    store().markCriteriaSaved(1, criteriaToSave(v1()));
    expect(v1().saved).toBe(true);
    expect(latestSavedCriteria(store().draft)?.version).toBe(1);
    expect(selectCriteriaUnsaved(store())).toBe(false);

    store().editCriterion(1, v1().items[0]!.id, '바뀌면 안 됨');
    store().addCriterion(1);
    expect(texts(1)).toEqual(['추임새 5번 이하', '청중 자주 보기']);
  });

  it('저장한 버전을 "수정하기" 하면 같은 목록으로 새 버전(저장 안 됨)이 생긴다', () => {
    store().markCriteriaSaved(1, criteriaToSave(v1()));
    store().reviseCriteria(1);

    expect(store().version).toBe(2);
    expect(texts(2)).toEqual(['추임새 5번 이하', '청중 자주 보기']);
    expect(store().draft.criteria[1]!.saved).toBe(false);
    // 새 버전을 저장하기 전까지 연습에는 V1 을 씁니다
    expect(latestSavedCriteria(store().draft)?.version).toBe(1);
  });

  it('저장 전에 다시 정리하면 고친 목록 대신 새 정리 결과가 들어간다 — 같은 버전 안에서', () => {
    store().editCriterion(1, v1().items[0]!.id, '고친 문장');
    store().applyParsedCriteria(1, {
      sourceText: '새로 적은 글',
      standards: ['새 기준'],
      exceptText: null,
    });
    expect(store().draft.criteria).toHaveLength(1);
    expect(texts(1)).toEqual(['새 기준']);
  });
});
