import { beforeEach, describe, expect, it } from 'vitest';
import { selectCriteriaUnsaved, useCreateStore } from './createStore';
import {
  MAX_CRITERIA,
  MAX_CRITERION_CHARS,
  criteriaToSave,
  isEditableCriteria,
  latestSavedCriteria,
  pendingVersion,
} from './lib/draft';

const store = () => useCreateStore.getState();
const find = (version: number) => store().draft.criteria.find((v) => v.version === version)!;
const v1 = () => find(1);
const texts = (version: number) => find(version).items.map((i) => i.text);

beforeEach(() => {
  store().reset();
  store().setPitchId('pitch1');
  store().applyParsedCriteria(null, {
    version: 1,
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

  it('저장하면 서버가 돌려준 새 버전과 목록으로 다시 그린다', () => {
    store().editCriterion(1, v1().items[0]!.id, '추임새 3번 이하.');
    store().markCriteriaSaved(1, {
      version: 4,
      standards: ['추임새 3번 이하', '청중 자주 보기'],
      exceptText: '청중 반응',
      sourceText: '추임새 3번 이하.\n청중 자주 보기',
    });

    expect(texts(1)).toEqual(['추임새 5번 이하', '청중 자주 보기']);
    expect(texts(4)).toEqual(['추임새 3번 이하', '청중 자주 보기']);
    expect(v1().edited).toBe(false);
    expect(selectCriteriaUnsaved(store())).toBe(false);
    expect(store().draft.criteria).toHaveLength(2);
    expect(store().version).toBe(4);
    expect(find(4).exceptText).toBe('청중 반응');
    expect(latestSavedCriteria(store().draft)?.version).toBe(4);
  });

  it('지난 버전은 고치지 않는다 — 지난 Take 가 가리킨다', () => {
    store().applyParsedCriteria(null, {
      version: 2,
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
      version: 2,
      sourceText: '새로 적은 글',
      standards: ['새 기준'],
      exceptText: null,
    });
    expect(store().draft.criteria).toHaveLength(2);
    expect(texts(2)).toEqual(['새 기준']);
    expect(find(2).saved).toBe(true);
  });

  it('빈 로컬 버전을 정리하면 서버가 준 버전 번호로 바꾼다', () => {
    store().addCriteriaVersion();
    store().applyParsedCriteria(2, {
      version: 5,
      sourceText: '새로 적은 글',
      standards: ['새 기준'],
      exceptText: null,
    });

    expect(store().draft.criteria.map((v) => v.version)).toEqual([1, 5]);
    expect(store().version).toBe(5);
    expect(latestSavedCriteria(store().draft)?.version).toBe(5);
  });

  it('V1 재정리 요청 중 만든 빈 V2는 서버 V2로 교체한다', () => {
    // V1에서 요청을 시작한 뒤 응답 전에 사이드바의 "+ 새 버전"을 누른 경우입니다.
    store().addCriteriaVersion();
    store().applyParsedCriteria(1, {
      version: 2,
      sourceText: '새로 정리한 글',
      standards: ['새 기준'],
      exceptText: null,
    });

    expect(store().draft.criteria.map((v) => v.version)).toEqual([1, 2]);
    expect(find(2).saved).toBe(true);
    expect(texts(2)).toEqual(['새 기준']);
    expect(store().version).toBe(2);
    expect(pendingVersion(store().draft, 'criteria')).toBeNull();
    expect(selectCriteriaUnsaved(store())).toBe(false);
    store().addCriteriaVersion();
    expect(store().version).toBe(3);
  });

  it.each(['parse', 'save'])('늦은 %s 응답이 최신 서버 버전을 뒤로 밀지 않는다', (operation) => {
    // 정리는 새 빈 버전에서도 요청할 수 있고, 저장 중에도 원문 재정리를 요청할 수 있습니다.
    if (operation === 'parse') store().addCriteriaVersion();
    else store().editCriterion(1, v1().items[0]!.id, '먼저 저장한 문장');
    store().applyParsedCriteria(operation === 'parse' ? 2 : 1, {
      version: 3,
      sourceText: '나중 요청',
      standards: ['최신 기준'],
      exceptText: null,
    });
    const earlier = {
      version: 2,
      sourceText: '먼저 요청',
      standards: ['이전 기준'],
      exceptText: null,
    };
    if (operation === 'parse') store().applyParsedCriteria(1, earlier);
    else store().markCriteriaSaved(1, earlier);

    expect(store().draft.criteria.map((v) => v.version)).toEqual([1, 2, 3]);
    expect(latestSavedCriteria(store().draft)?.version).toBe(3);
    expect(isEditableCriteria(store().draft, find(3))).toBe(true);
    expect(isEditableCriteria(store().draft, find(2))).toBe(false);
    store().addCriteriaVersion();
    expect(store().version).toBe(4);
  });

  it('고친 채로 다시 정리하면 앞 버전은 서버 목록으로 돌아간다 — 저장할 길 없는 고친 내용이 새 버전을 막지 않게', () => {
    // 재현: V1 정리 → 한 줄 고침 → "수정해서 다시 정리" → V2
    store().editCriterion(1, v1().items[0]!.id, '고친 문장');
    store().editCriterion(1, v1().items[0]!.id, '한 번 더 고친 문장');
    store().applyParsedCriteria(1, {
      version: 2,
      sourceText: '새로 적은 글',
      standards: ['새 기준'],
      exceptText: null,
    });

    expect(texts(1)).toEqual(['추임새 5번 이하', '청중 자주 보기']);
    expect(v1().edited).toBe(false);
    expect(v1().savedItems).toBeUndefined();
    // 떠나기 경고 · "+ 새 버전" 막힘이 풀립니다
    expect(selectCriteriaUnsaved(store())).toBe(false);
    expect(pendingVersion(store().draft, 'criteria')).toBeNull();
  });

  it('최신 버전의 항목을 모두 지워도 계속 고칠 수 있다 — 다시 적거나 다시 정리할 수 있게', () => {
    for (const item of v1().items) store().removeCriterion(1, item.id);
    expect(v1().items).toEqual([]);
    expect(v1().edited).toBe(true);
    expect(isEditableCriteria(store().draft, v1())).toBe(true);

    store().addCriterion(1);
    store().editCriterion(1, v1().items[0]!.id, '다시 적은 기준');
    expect(texts(1)).toEqual(['다시 적은 기준']);
    // 빈 목록은 저장하지 않습니다 (SaveBar 가 막습니다)
    expect(criteriaToSave(v1())).toEqual(['다시 적은 기준']);
  });

  it('정리 전 빈 버전은 여전히 고칠 수 없다 — saved 가 거른다', () => {
    store().addCriteriaVersion();
    expect(isEditableCriteria(store().draft, find(2))).toBe(false);
  });

  it('저장하면 적어 둔 서버 목록을 지운다', () => {
    store().editCriterion(1, v1().items[0]!.id, '고친 문장');
    expect(v1().savedItems?.map((i) => i.text)).toEqual(['추임새 5번 이하', '청중 자주 보기']);
    store().markCriteriaSaved(1, {
      version: 2,
      standards: ['고친 문장', '청중 자주 보기'],
      exceptText: null,
      sourceText: '고친 문장\n청중 자주 보기',
    });
    expect(v1().savedItems).toBeUndefined();
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

describe('평가기준 — 서버에서 불러온 버전', () => {
  it('원문이 없는 버전을 "수정하기" 하면 목록을 한 줄씩 이어 원문으로 물려준다', () => {
    // 서버는 원문을 저장하지 않습니다 — loadCriteria 처럼 sourceText 없이 넣습니다
    store().hydrate('edit:pitch1', 'pitch1', {
      ...store().draft,
      criteria: [
        {
          version: 1,
          saved: true,
          items: [
            { id: 'a', text: '추임새 5번 이하' },
            { id: 'b', text: '청중 자주 보기' },
          ],
        },
      ],
    });
    store().reviseCriteria(1);

    expect(find(2).sourceText).toBe('추임새 5번 이하\n청중 자주 보기');
    expect(find(2).items).toEqual([]);
  });
});

describe('평가기준 — 계약 상한', () => {
  it('표시 기준은 한 줄 50자 · 10개까지다 (evaluation-criteria.md 출력 표, standards.title String(50))', () => {
    expect(MAX_CRITERION_CHARS).toBe(50);
    expect(MAX_CRITERIA).toBe(10);
  });
});
