// @vitest-environment jsdom
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  CardList,
  EXPIRED_PROPOSAL_TEXT,
  MOVED_TO_PRACTICE_TEXT,
  NO_VERDICT,
  REASSESS_CURRENT_TEXT,
  REASSESS_TITLE,
  SCRIPT_SCORE_TEXT,
  UNDO_CONFLICT_TEXT,
  UNDO_FAILED_TEXT,
  type CardActions,
} from './ChatCards';
import { reassessRows } from './chatCardRows';
import { emptyBasis, readCard, UNSUPPORTED_CARD_TEXT } from './chatResponse';
import type { DecideResult, UndoResult } from './chatTransport';

const PITCH = '22222222-2222-4222-8222-222222222222';
const TAKE = '33333333-3333-4333-8333-333333333333';

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

/** 걸어 둔 응답 */
function held<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => (resolve = res));
  return { promise, resolve };
}

function Where() {
  return <p data-where="">{useLocation().pathname}</p>;
}

const render = (node: ReactNode) =>
  act(async () =>
    root.render(
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="*" element={node} />
        </Routes>
        <Where />
      </MemoryRouter>,
    ),
  );

/** 버튼의 보이는 글 — 화면 읽기 프로그램에만 주는 잠긴 이유(sr-only)는 뺍니다 */
const visibleText = (el: Element) => {
  const copy = el.cloneNode(true) as Element;
  copy.querySelectorAll('.sr-only').forEach((s) => s.remove());
  return copy.textContent;
};
const button = (text: string) =>
  [...host.querySelectorAll('button')].find((b) => visibleText(b) === text);
const alerts = () => [...host.querySelectorAll('[role="alert"]')].map((a) => a.textContent);

function actions(over: Partial<CardActions> = {}): CardActions & {
  undo: ReturnType<typeof vi.fn>;
  decide: ReturnType<typeof vi.fn>;
  choose: ReturnType<typeof vi.fn>;
} {
  return {
    decide: vi.fn(async (): Promise<DecideResult> => 'decided'),
    undo: vi.fn(async (): Promise<UndoResult> => 'undone'),
    choose: vi.fn(async () => true),
    canSend: true,
    ...over,
  } as never;
}

const goal = {
  version: 2,
  pitch_id: PITCH,
  created_at: '2026-11-06T00:00:00Z',
  metric: 'audience_ratio',
  slide_number: 3,
  operator: 'gt_base',
  script_version_id: null,
  standard_version: null,
  goal_rule_version: 'g1',
  base: {
    take_id: TAKE,
    take_number: 2,
    kind: 'SLIDE',
    revision: 1,
    value: 0.31,
    metric_rule_version: 'm1',
    range: null,
  },
  min_delta: 0.05,
  status: 'OPEN',
};

const savedCard = {
  type: 'GOAL_SAVED',
  action_id: 'a-1',
  goal,
  reason: '3장에서 청중을 덜 봤어요.',
  evidence_ids: [],
  undo: true,
};

const show = (...cards: unknown[]) => cards.map((c) => readCard(c, emptyBasis()));

describe('목표 저장됨 (12-4 F6)', () => {
  it('목표를 지표 · 장 · 비교로 보이고, [되돌리기]는 그 실행 · 행동으로 한 번만 보낸다', async () => {
    const answer = held<UndoResult>();
    const a = actions({ undo: vi.fn(() => answer.promise) });
    await render(<CardList cards={show(savedCard)} runId="r-1" expired={false} actions={a} />);
    expect(host.textContent).toContain('대화에서 정한 목표');
    expect(host.textContent).toContain('청중 응시 · 3장 · Take 2 31%보다 높게');

    await act(async () => button('되돌리기')!.click());
    expect(a.undo).toHaveBeenCalledWith('r-1', 'a-1');
    expect(button('되돌리기')!.getAttribute('aria-disabled')).toBe('true');
    await act(async () => button('되돌리기')!.click());
    expect(a.undo).toHaveBeenCalledTimes(1);

    await act(async () => answer.resolve('undone'));
    // 되돌린 뒤에는 다시 누르지 않습니다 — "목표를 되돌렸어요." 는 BE 가 대화에 남깁니다
    expect(button('되돌리기')!.getAttribute('aria-disabled')).toBe('true');
    await act(async () => button('되돌리기')!.click());
    expect(a.undo).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['conflict', UNDO_CONFLICT_TEXT],
    ['failed', UNDO_FAILED_TEXT],
  ] as const)('%s 면 안내를 보이고 다시 누를 수 있다', async (result, text) => {
    const a = actions({ undo: vi.fn(async () => result) });
    await render(<CardList cards={show(savedCard)} runId="r-1" expired={false} actions={a} />);
    await act(async () => button('되돌리기')!.click());
    expect(alerts()).toEqual([text]);
    expect(button('되돌리기')!.getAttribute('aria-disabled')).toBe('false');
  });

  it('실행을 모르면 [되돌리기]를 두지 않는다', async () => {
    await render(
      <CardList cards={show(savedCard)} runId={null} expired={false} actions={actions()} />,
    );
    expect(button('되돌리기')).toBeUndefined();
  });
});

describe('목표 제안 (12-4 F6)', () => {
  const proposal = {
    type: 'GOAL_PROPOSAL',
    proposal_id: 'p-1',
    options: [
      {
        label: 'USER',
        goal: null,
        reason: '잴 수 없는 목표예요.',
        evidence_ids: [],
        content_hash: null,
      },
      {
        label: 'EXPERT',
        goal: { ...goal, version: undefined, status: undefined, created_at: undefined },
        reason: '3장 청중 응시를 먼저 올려 보세요.',
        evidence_ids: [],
        content_hash: 'h-1',
      },
    ],
  };

  it('저장 전 표시, 잴 수 없는 안은 설명만 — 고를 수 있는 안만 [이 안으로]', async () => {
    const a = actions();
    await render(<CardList cards={show(proposal)} runId="r-1" expired={false} actions={a} />);
    expect(host.textContent).toContain('저장 전');
    const user = host.querySelector('[data-option="USER"]')!;
    expect(user.textContent).toContain('사용자 안');
    expect(user.querySelector('button')).toBeNull();
    const expert = host.querySelector('[data-option="EXPERT"]')!;
    expect(expert.textContent).toContain('추천 안');
    await act(async () => expert.querySelector('button')!.click());
    expect(a.choose).toHaveBeenCalledWith('p-1', 'EXPERT');
  });

  it('보낼 수 없을 때는 잠그고 보내지 않는다', async () => {
    const a = actions({ canSend: false });
    await render(<CardList cards={show(proposal)} runId="r-1" expired={false} actions={a} />);
    const pick = button('이 안으로')!;
    expect(pick.getAttribute('aria-disabled')).toBe('true');
    await act(async () => pick.click());
    expect(a.choose).not.toHaveBeenCalled();
  });

  it('만료 · 취소된 실행이면 버튼을 끄고 "시간이 지나 다시 물어봐 주세요."', async () => {
    await render(<CardList cards={show(proposal)} runId="r-1" expired actions={actions()} />);
    expect(button('이 안으로')).toBeUndefined();
    expect(host.textContent).toContain(EXPIRED_PROPOSAL_TEXT);
  });
});

describe('참고 결과 (12-4 F6)', () => {
  const reassess = (over: Record<string, unknown> = {}) => ({
    type: 'REASSESS_RESULT',
    take_id: TAKE,
    change: { script_used: true },
    before: {
      score: 72,
      dimensions: { audience_gaze: 'PRIORITY', pacing_rhythm: 'STABLE' },
      criteria_results: [
        { display: '3분 안에 끝내기', status: 'FAILED' },
        { display: '군더더기 5회 이하', status: 'ACHIEVED' },
      ],
    },
    after: {
      score: 78,
      dimensions: { audience_gaze: null, pacing_rhythm: 'STABLE' },
      criteria_results: [
        { display: '3분 안에 끝내기', status: 'ACHIEVED' },
        { display: '군더더기 5회 이하', status: 'ACHIEVED' },
      ],
    },
    changed_fields: ['score', 'dimensions.audience_gaze', 'criteria_results'],
    official_differs: false,
    ...over,
  });

  it('점수 · 차원 · 기준 판정을 나란히, 바뀐 칸은 강조하고 null 은 "판정 없음"', async () => {
    await render(
      <CardList cards={show(reassess())} runId="r-1" expired={false} actions={actions()} />,
    );
    expect(host.textContent).toContain(REASSESS_TITLE);
    const rows = [...host.querySelectorAll('tbody tr')].map((tr) => [
      tr.querySelector('th')!.textContent,
      ...[...tr.querySelectorAll('td')].map((td) => td.textContent),
      tr.hasAttribute('data-changed'),
    ]);
    expect(rows).toEqual([
      ['점수 (바뀜)', '72', '78', true],
      ['청중 응시 (바뀜)', '먼저 고칠 것', NO_VERDICT, true],
      ['속도 · 리듬', '유지', '유지', false],
      ['3분 안에 끝내기 (바뀜)', '미달성', '달성', true],
      // AI 는 기준이 바뀌었는지만 한 덩어리로 알려 주지만, 바뀌지 않은 줄은 강조하지 않습니다
      ['군더더기 5회 이하', '달성', '달성', false],
    ]);
    // 대본을 보고 한 발표로 점수가 오름
    expect(host.textContent).toContain(SCRIPT_SCORE_TEXT);
    expect(host.textContent).not.toContain(REASSESS_CURRENT_TEXT);
  });

  it('공식 리뷰와 다르면 "지금 기록 기준이에요", 대본 다시 평가가 아니면 점수 안내를 붙이지 않는다', async () => {
    await render(
      <CardList
        cards={show(reassess({ official_differs: true, change: { slide: 3 } }))}
        runId="r-1"
        expired={false}
        actions={actions()}
      />,
    );
    expect(host.textContent).toContain(REASSESS_CURRENT_TEXT);
    expect(host.textContent).not.toContain(SCRIPT_SCORE_TEXT);
  });
});

describe('연습 준비 · 승인 확인 · 결과 카드', () => {
  it('[연습 준비로]는 누를 때만 고정 경로로 옮기고, 옮겼다고 남긴다', async () => {
    await render(
      <CardList
        cards={show({ type: 'START_PRACTICE', pitch_id: PITCH })}
        runId="r-1"
        expired={false}
        actions={actions()}
      />,
    );
    expect(host.querySelector('[data-where]')!.textContent).toBe('/');
    await act(async () => button('연습 준비로')!.click());
    expect(host.querySelector('[data-where]')!.textContent).toBe(`/pitch/${PITCH}/device-check`);
    expect(host.textContent).toContain(MOVED_TO_PRACTICE_TEXT);
  });

  it('승인 확인은 바뀌는 내용 · 안내 · 만료 시각을 보이고, 카드의 args_hash 로 한 번만 보낸다', async () => {
    const answer = held<DecideResult>();
    const a = actions({ decide: vi.fn(() => answer.promise) });
    const ask = {
      type: 'ASK_CONFIRM',
      action_id: 'a-9',
      action_type: 'REASSESS',
      title: '다시 평가할까요?',
      summary: '대본을 본 발표로 다시 평가해요.',
      before: { script_used: false },
      after: { script_used: true },
      notes: ['참고 결과이고 공식 결과는 그대로예요'],
      expires_at: '2026-11-06T05:30:00Z',
      args_hash: 'sha-1',
    };
    await render(<CardList cards={show(ask)} runId="r-1" expired={false} actions={a} />);
    expect(host.textContent).toContain('다시 평가할까요?');
    // 칸 이름 · 참 거짓을 우리말로, Take ID 는 보이지 않습니다
    const rows = host.querySelector('[data-change-rows]')!.textContent;
    expect(rows).toBe('대본 보며 발표아니요 → 예');
    expect(host.textContent).not.toContain('script_used');
    expect(host.textContent).toContain('참고 결과이고 공식 결과는 그대로예요');
    expect(host.textContent).toContain('까지 확인해 주세요.');

    await act(async () => button('승인')!.click());
    expect(a.decide).toHaveBeenCalledWith('a-9', 'APPROVE', 'sha-1');
    expect(button('거절')!.getAttribute('aria-disabled')).toBe('true');
    await act(async () => button('거절')!.click());
    expect(a.decide).toHaveBeenCalledTimes(1);
    await act(async () => answer.resolve('decided'));
    // 받아들여졌으면 카드가 사라질 때까지 잠근 채로 둡니다
    expect(button('승인')!.getAttribute('aria-disabled')).toBe('true');
    await act(async () => button('거절')!.click());
    expect(a.decide).toHaveBeenCalledTimes(1);
  });

  it('목표 저장 확인은 전 · 후를 목표 문장으로 — 목표 모양이 아니면 받은 칸 그대로', async () => {
    const draft = { ...goal, version: undefined, status: undefined, created_at: undefined };
    const ask = (before: unknown) => ({
      type: 'ASK_CONFIRM',
      action_id: 'a-1',
      action_type: 'SAVE_GOAL',
      title: '대화 목표를 저장할까요?',
      summary: '3장 청중 응시',
      before,
      after: draft,
      notes: [],
      expires_at: '2026-11-06T05:30:00Z',
      args_hash: 'h',
    });
    const change = () => host.querySelector('[data-goal-change]')?.textContent ?? null;

    await render(
      <CardList cards={show(ask(null))} runId="r-1" expired={false} actions={actions()} />,
    );
    expect(change()).toBe('지금 목표없음새 목표청중 응시 · 3장 · Take 2 31%보다 높게');

    const previous = {
      ...draft,
      slide_number: null,
      operator: 'lt_base',
      base: { ...goal.base, value: 0.4 },
    };
    await render(
      <CardList cards={show(ask(previous))} runId="r-1" expired={false} actions={actions()} />,
    );
    expect(change()).toBe(
      '지금 목표청중 응시 · Take 2 40%보다 낮게새 목표청중 응시 · 3장 · Take 2 31%보다 높게',
    );

    await render(
      <CardList
        cards={show(ask({ note: '예전 모양' }))}
        runId="r-1"
        expired={false}
        actions={actions()}
      />,
    );
    expect(change()).toBeNull();
    expect(host.textContent).toContain('예전 모양');
  });

  it('승인 · 거절이 받아들여지지 않으면 다시 누를 수 있다', async () => {
    const a = actions({ decide: vi.fn(async (): Promise<DecideResult> => 'failed') });
    const ask = {
      type: 'ASK_CONFIRM',
      action_id: 'a-9',
      action_type: 'REASSESS',
      title: '다시 평가할까요?',
      summary: 's',
      before: null,
      after: { script_used: true },
      notes: [],
      expires_at: '2026-11-06T05:30:00Z',
      args_hash: 'sha-1',
    };
    await render(<CardList cards={show(ask)} runId="r-1" expired={false} actions={a} />);
    await act(async () => button('승인')!.click());
    expect(button('승인')!.getAttribute('aria-disabled')).toBe('false');
  });

  it('다시 평가 확인의 바뀌는 내용 — 대상은 Take 번호로, Take ID 는 숨기고, 조건은 우리말로', () => {
    const take = { take_id: TAKE, take_number: 5 };
    expect(reassessRows({ ...take, script_used: false }, { ...take, script_used: true })).toEqual([
      { key: 'take_number', label: '대상', value: 'Take 5' },
      { key: 'script_used', label: '대본 보며 발표', value: '아니요 → 예' },
    ]);
    expect(
      reassessRows({ ...take, targets: [{}, {}] }, { ...take, targets: [{}, {}, {}] }),
    ).toEqual([
      { key: 'take_number', label: '대상', value: 'Take 5' },
      { key: 'targets', label: '내 평가 기준', value: '2개 → 3개' },
    ]);
    // 모르는 칸은 받은 그대로 — 바뀌는 내용을 숨기지 않습니다
    expect(reassessRows(null, { ...take, new_field: 'x' })).toEqual([
      { key: 'take_number', label: '대상', value: 'Take 5' },
      { key: 'new_field', label: 'new_field', value: 'x' },
    ]);
  });

  it('목표 확인 결과는 버튼 없이(기억했어요의 [지우기]는 로그인 사용자를 알 때만), 모르는 카드는 대체 표시 — 다른 카드는 그대로', async () => {
    await render(
      <CardList
        cards={show(
          {
            type: 'GOAL_RESULT',
            goal_version: 2,
            take_id: TAKE,
            status: 'ACHIEVED',
            observed: 0.41,
            reason: '3장 청중 응시가 41%예요.',
          },
          { type: 'MEMORY_SAVED', item_id: 'i-1', key: 'TONE', value: '짧게' },
          { type: 'NEW_CARD' },
        )}
        runId="r-1"
        expired={false}
        actions={actions()}
      />,
    );
    expect(host.querySelector('[data-goal-result]')!.textContent).toBe('이룸');
    expect(host.textContent).toContain('3장 청중 응시가 41%예요.');
    expect(host.textContent).toContain('기억했어요');
    expect(host.textContent).toContain('답 말투 · 길이: 짧게');
    expect(host.querySelector('[data-card="UNSUPPORTED"]')!.textContent).toBe(
      UNSUPPORTED_CARD_TEXT,
    );
    expect(host.querySelectorAll('button')).toHaveLength(0);
  });
});
