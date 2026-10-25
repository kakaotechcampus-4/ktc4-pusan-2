import { beforeEach, describe, expect, it } from 'vitest';
import { selectCriteriaUnsaved, useCreateStore } from './createStore';
import {
  MAX_CRITERIA,
  MAX_CRITERION_CHARS,
  criteriaToSave,
  isEditableCriteria,
  latestSavedCriteria,
} from './lib/draft';

const store = () => useCreateStore.getState();
const find = (version: number) => store().draft.criteria.find((v) => v.version === version)!;
const v1 = () => find(1);
const texts = (version: number) => find(version).items.map((i) => i.text);

beforeEach(() => {
  store().reset();
  store().setPitchId('pitch1');
  store().applyParsedCriteria(null, {
    sourceText: '추임새는 5번 이하, 청중을 자주 보기',
    standards: ['추임새 5번 이하', '청중 자주 보기'],
    exceptText: null,
  });
});

/** 계약: `docs/ai/features/evaluation-criteria.md` 57 · 282행. BE 는 정리할 때 새 버전으로 저장합니다 (#463) */
describe('평가기준 — 정리 → (고치기 → 저장)', () => {
  it('정리하면 바로 서버에 저장된 버전이다 — 연습에 쓰고, 떠나도 경고하지 않는다', () => {
    expect(v1().saved).toBe(true);
    expect(v1().edited).toBe(false);
    expect(latestSavedCriteria(store().draft)?.version).toBe(1);
    expect(selectCriteriaUnsaved(store())).toBe(false);
  });

  it('가장 최근 버전은 문장을 고치고 · 더하고 · 지울 수 있고, 고치면 "고친 내용 저장 안 됨"이다', () => {
    const [first, second] = v1().items;
    store().editCriterion(1, first!.id, '추임새 3번 이하');
    store().removeCriterion(1, second!.id);
    store().addCriterion(1);
    store().editCriterion(1, v1().items.at(-1)!.id, '말 빠르지 않게');

    expect(texts(1)).toEqual(['추임새 3번 이하', '말 빠르지 않게']);
    expect(v1().edited).toBe(true);
    expect(selectCriteriaUnsaved(store())).toBe(true);
    // 저장하기 전까지 서버(연습)에는 고치기 전 버전이 그대로입니다
    expect(latestSavedCriteria(store().draft)?.version).toBe(1);
  });

  it('빈 줄과 앞뒤 공백은 빼고 저장할 목록을 만든다', () => {
    store().addCriterion(1);
    store().editCriterion(1, v1().items[0]!.id, '  추임새 5번 이하  ');
    expect(criteriaToSave(v1())).toEqual(['추임새 5번 이하', '청중 자주 보기']);
  });

  it('저장하면 서버가 돌려준 목록으로 다시 그린다 — 다시 분석해 보낸 문장과 다를 수 있다', () => {
    store().editCriterion(1, v1().items[0]!.id, '추임새 3번 이하.');
    store().markCriteriaSaved(1, ['추임새 3번 이하', '청중 자주 보기']);

    expect(texts(1)).toEqual(['추임새 3번 이하', '청중 자주 보기']);
    expect(v1().edited).toBe(false);
    expect(selectCriteriaUnsaved(store())).toBe(false);
    // 같은 버전으로 저장합니다 — 새 버전이 생기지 않습니다
    expect(store().draft.criteria).toHaveLength(1);
  });

  it('지난 버전은 고치지 않는다 — 지난 Take 가 가리킨다', () => {
    store().applyParsedCriteria(null, {
      sourceText: '새로 적은 글',
      standards: ['새 기준'],
      exceptText: null,
    });
    expect(isEditableCriteria(store().draft, v1())).toBe(false);

    store().editCriterion(1, v1().items[0]!.id, '바뀌면 안 됨');
    store().addCriterion(1);
    expect(texts(1)).toEqual(['추임새 5번 이하', '청중 자주 보기']);
    expect(v1().edited).toBe(false);
  });

  it('다시 정리하면 서버처럼 새 버전이 생긴다 — 고친 목록은 그 버전에 남지 않는다', () => {
    store().editCriterion(1, v1().items[0]!.id, '고친 문장');
    store().applyParsedCriteria(1, {
      sourceText: '새로 적은 글',
      standards: ['새 기준'],
      exceptText: null,
    });
    expect(store().draft.criteria).toHaveLength(2);
    expect(texts(2)).toEqual(['새 기준']);
    expect(find(2).saved).toBe(true);
  });

  it('지난 버전을 "수정하기" 하면 원문을 물려받은 빈 버전이 생기고, 정리해야 서버에 생긴다', () => {
    store().reviseCriteria(1);

    expect(store().version).toBe(2);
    expect(find(2).items).toEqual([]);
    expect(find(2).saved).toBe(false);
    expect(find(2).sourceText).toBe('추임새는 5번 이하, 청중을 자주 보기');
    // 정리하기 전까지 연습에는 V1 을 씁니다
    expect(latestSavedCriteria(store().draft)?.version).toBe(1);
  });
});

describe('평가기준 — 계약 상한', () => {
  it('표시 기준은 한 줄 50자 · 10개까지다 (evaluation-criteria.md 출력 표, standards.title String(50))', () => {
    expect(MAX_CRITERION_CHARS).toBe(50);
    expect(MAX_CRITERIA).toBe(10);
  });
});
