// @vitest-environment jsdom
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChatMessage } from '@/types/chat';
import { LiveAnswer, StoredAnswer, SUPERSEDED_TEXT, type LiveProgress } from './ChatAnswer';
import type { CardActions } from './ChatCards';
import { GENERAL_LABEL, type ShownClaim } from '../rules/chatResponse';
import { ManualClock } from '../api/chatTransport.testkit';
import { createTypingQueue, TYPING_MAX_LAG_MS, type TypingQueue } from '../rules/chatTyping';

const TAKE = '11111111-1111-4111-8111-111111111111';

const noActions: CardActions = {
  decide: async () => 'locked',
  undo: async () => 'locked',
  choose: async () => false,
  canSend: true,
};

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

const render = (node: ReactNode) =>
  act(async () => root.render(<MemoryRouter>{node}</MemoryRouter>));
const buttonByText = (text: string) =>
  [...host.querySelectorAll('button')].find((b) => b.textContent === text);

const claims = [
  { kind: 'RECORD', text: '3장 시선이 낮았어요.', evidence_ids: ['e1'], source_ids: [] },
  { kind: 'GUIDE', text: '첫 낱말만 보고 이어 가 보세요.', evidence_ids: [], source_ids: ['s1'] },
  { kind: 'GENERAL', text: '숨을 고르고 시작해 보세요.', evidence_ids: [], source_ids: [] },
  {
    kind: 'ASSESSMENT',
    assessment_id: 'a1',
    text: '말이 빨랐다는 말은 일부 맞아요.',
    evidence_ids: ['e2'],
    source_ids: [],
  },
].map((c) => ({ assessment_id: null, action_id: null, ...c }));

function storedMessage(over: Partial<ChatMessage> = {}): ChatMessage {
  return {
    message_id: 'm1',
    seq: 2,
    role: 'assistant',
    text: claims.map((c) => c.text).join('\n'),
    status: 'DONE',
    run_id: 'r1',
    again_of_message_id: null,
    created_at: '2026-11-06T00:00:00Z',
    claims,
    cards: [],
    evidence: [
      {
        evidence_id: 'e1',
        origin: 'RECORD',
        take_id: TAKE,
        take_number: 5,
        slide_number: 3,
        value: 0.24,
      },
      // 형식이 맞지 않는 Take ID — 링크를 만들지 않습니다
      {
        evidence_id: 'e2',
        origin: 'RECORD',
        take_id: '../admin',
        take_number: 4,
        slide_number: null,
        value: 180,
      },
    ],
    sources: [{ source_id: 's1', title: '대본 의존 줄이기', quote: '첫 낱말만 보세요.' }],
    assessments: [
      {
        assessment_id: 'a1',
        quote: '말이 빨랐어',
        type: 'MEASURED',
        verdict: 'PARTLY',
        evidence_ids: ['e2'],
        source_ids: [],
        reason: '분당 180자',
        supersedes: [],
        superseded_by: 'a9',
      },
    ],
    basis: null,
    plan: [
      { step: '최근 Take 비교', status: 'DONE', note: null },
      { step: '자료 찾기', status: 'SKIPPED', note: null },
    ],
    trace: [
      {
        seq: 1,
        tool: 'compare_takes',
        args_summary: 'Take 4 · 5 비교',
        result_summary: '3장 시선 둘 다 낮음',
        ms: 30,
        outcome: 'OK',
      },
    ],
    masked: false,
    stale_reasons: [],
    ...over,
  } as unknown as ChatMessage;
}

describe('지난 답 (12-4 F4 · F7 · F13)', () => {
  it('문장마다 근거 칩 — 내 기록은 형식이 맞는 Take 만 리포트로 잇는다', async () => {
    await render(<StoredAnswer message={storedMessage()} actions={noActions} />);
    const link = host.querySelector('a')!;
    expect(link.textContent).toBe('Take 5 · 3장');
    expect(link.getAttribute('href')).toBe(`/takes/${TAKE}`);
    expect(host.querySelectorAll('a')).toHaveLength(1);
    expect(host.textContent).toContain('Take 4');
  });

  it('자료 칩은 펼치면 인용 · 출처 · 라이선스, 빠진 칸은 "정보 없음"', async () => {
    await render(<StoredAnswer message={storedMessage()} actions={noActions} />);
    const chip = buttonByText('자료: 대본 의존 줄이기')!;
    expect(chip.getAttribute('aria-expanded')).toBe('false');
    await act(async () => chip.click());
    expect(chip.getAttribute('aria-expanded')).toBe('true');
    const details = document.getElementById(chip.getAttribute('aria-controls')!)!;
    expect(details.textContent).toContain('첫 낱말만 보세요.');
    expect(details.textContent!.match(/정보 없음/g)).toHaveLength(2);
  });

  it('일반 조언은 칩 없이 글 라벨, 판단은 판정 배지 — 대체된 판단은 흐리게 표시를 붙인다', async () => {
    await render(<StoredAnswer message={storedMessage()} actions={noActions} />);
    const lines = [...host.querySelectorAll('[data-claim]')];
    expect(lines[2]!.textContent).toContain(GENERAL_LABEL);
    expect(lines[2]!.querySelector('ul')).toBeNull();
    expect(lines[3]!.querySelector('[data-verdict]')!.textContent).toBe('일부 맞음');
    expect(lines[3]!.textContent).toContain(SUPERSEDED_TEXT);
    expect(lines[3]!.className).toContain('opacity-60');
    expect(lines[0]!.textContent).not.toContain(SUPERSEDED_TEXT);
  });

  it('할 일은 답 위에 표시와 읽는 글로, 확인한 것은 접어 둔다', async () => {
    await render(<StoredAnswer message={storedMessage()} actions={noActions} />);
    const plan = host.querySelector('ol[aria-label="할 일"]')!;
    expect(plan.textContent).toBe('☑ 끝: 최근 Take 비교— 건너뜀: 자료 찾기');
    const trace = host.querySelector('details')!;
    expect(trace.open).toBe(false);
    expect(trace.querySelector('summary')!.textContent).toBe('확인한 것');
    expect(trace.textContent).toContain('Take 4 · 5 비교 → 3장 시선 둘 다 낮음');
  });

  it('검사에 걸린 답은 걸리기 전 문장만 보이고 안내를 붙인다', async () => {
    const broken = storedMessage({
      claims: [claims[0], { ...claims[1], kind: 'NEW_KIND' }] as never,
    });
    await render(<StoredAnswer message={broken} actions={noActions} />);
    expect(host.querySelectorAll('[data-claim]')).toHaveLength(1);
    expect(host.textContent).toContain('답의 근거를 확인하지 못했으니 다시 물어봐 주세요.');
  });
});

describe('지금 실행의 답 (12-4 F12)', () => {
  const shown = (text: string, over: Partial<ShownClaim> = {}): ShownClaim => ({
    index: 0,
    kind: 'RECORD',
    text,
    chips: [{ type: 'RECORD', key: 'r', takeId: TAKE, takeNumber: 5, slideNumber: 3 }],
    missing: 0,
    general: false,
    assessment: null,
    actionId: null,
    masked: false,
    ...over,
  });
  const progress: LiveProgress = {
    runId: 'r1',
    conversationId: 'c1',
    plan: [{ step: '기록 확인', status: 'DOING', note: null }],
    trace: null,
    contractError: null,
    progressDropped: false,
    cards: [],
    ended: null,
  };

  let clock: ManualClock;
  let queue: TypingQueue<ShownClaim>;
  let announced: string;

  beforeEach(() => {
    clock = new ManualClock();
    announced = '';
    queue = createTypingQueue<ShownClaim>({
      clock,
      reducedMotion: () => false,
      onLineDone: (line) => {
        announced = line.item.text;
      },
    });
  });

  const renderLive = (p: LiveProgress | null = progress) =>
    render(
      <LiveAnswer
        progress={p}
        typing={queue}
        announce={announced}
        waiting={null}
        actions={noActions}
      />,
    );

  it('글자 단위로 보이고, 칩은 문장이 다 보인 뒤에 단다', async () => {
    await renderLive();
    await act(async () => queue.push([shown('3장 시선이 낮았어요.')]));
    const line = () => host.querySelector('[data-claim]')!;
    expect(line().textContent).toBe('');

    await act(async () => clock.advance(100));
    expect(line().textContent!.length).toBeGreaterThan(0);
    expect(line().textContent!.length).toBeLessThan('3장 시선이 낮았어요.'.length);
    expect(host.querySelector('a')).toBeNull();

    await act(async () => clock.advance(TYPING_MAX_LAG_MS));
    await renderLive();
    expect(line().textContent).toContain('3장 시선이 낮았어요.');
    expect(host.querySelector('a')!.textContent).toBe('Take 5 · 3장');
    // 다 보인 문장 하나만 알립니다 — 효과 글 자체에는 aria-live 가 없습니다
    expect(host.querySelector('[data-chat-announce]')!.textContent).toBe('3장 시선이 낮았어요.');
    // 알림 영역은 role="status"(#956 합의) — 진행 단계 줄도 같습니다
    expect(host.querySelector('[data-chat-announce]')!.getAttribute('role')).toBe('status');
    expect(host.querySelector('[data-chat-stage]')!.getAttribute('role')).toBe('status');
    // 글자 효과가 그리는 답 말풍선에는 aria-live 가 없습니다 — 알림은 숨은 알림 영역으로만
    expect(
      host.querySelector('[data-live-run] [aria-live], [data-live-run][aria-live]'),
    ).toBeNull();
  });

  it('할 일은 문장보다 먼저 보이고, 하는 중 표시를 단다 — 단계가 바뀔 때만 알린다', async () => {
    await renderLive();
    expect(host.querySelector('[data-chat-stage]')!.textContent).toBe('하는 중: 기록 확인');
    await renderLive({ ...progress, plan: [{ step: '기록 확인', status: 'DONE', note: null }] });
    expect(host.querySelector('[data-chat-stage]')!.textContent).toBe('');
    await renderLive();
    expect(host.querySelector('[data-plan-status="DOING"]')!.textContent).toBe(
      '⟳ 하는 중: 기록 확인',
    );
  });

  it('진행이 없으면 답 말풍선을 그리지 않는다', async () => {
    await renderLive(null);
    expect(host.querySelector('[data-live-run]')).toBeNull();
  });

  it('계약 오류 · 빠진 진행 정보는 안내를 붙인다', async () => {
    await renderLive({ ...progress, contractError: 'UNKNOWN_KIND', progressDropped: true });
    expect(host.textContent).toContain('답의 근거를 확인하지 못했으니 다시 물어봐 주세요.');
    expect(host.textContent).toContain('일부 진행 정보를 표시하지 못했어요.');
  });

  it('채워지는 중인 글은 화면 읽기 프로그램에서 숨기고, 다 보이면 푼다 (#956 리뷰)', async () => {
    await renderLive();
    await act(async () => queue.push([shown('3장 시선이 낮았어요.')]));
    await act(async () => clock.advance(100));
    const text = () => host.querySelector('[data-claim] > span')!;
    expect(text().getAttribute('aria-hidden')).toBe('true');

    await act(async () => clock.advance(TYPING_MAX_LAG_MS));
    expect(text().textContent).toBe('3장 시선이 낮았어요.');
    expect(text().hasAttribute('aria-hidden')).toBe(false);
  });

  it('[건너뛰기]는 남은 글을 다 보이고 칩을 단다 — 마지막 문장만 알리고, 버튼은 남아 잠긴다', async () => {
    await renderLive();
    await act(async () =>
      queue.push([shown('3장 시선이 낮았어요.'), shown('첫 낱말만 보세요.', { index: 1 })]),
    );
    await act(async () => clock.advance(100));
    const skip = () => host.querySelector<HTMLButtonElement>('[data-chat-skip]')!;
    expect(skip().getAttribute('aria-disabled')).toBe('false');
    expect(host.querySelector('a')).toBeNull();

    skip().focus();
    await act(async () => skip().click());
    await renderLive();
    expect([...host.querySelectorAll('[data-claim] > span')].map((s) => s.textContent)).toEqual([
      '3장 시선이 낮았어요.',
      '첫 낱말만 보세요.',
    ]);
    expect(host.querySelectorAll('a')).toHaveLength(2);
    expect(host.querySelector('[data-chat-announce]')!.textContent).toBe('첫 낱말만 보세요.');
    // 사라지지 않고 잠겨 키보드 초점을 지킵니다
    expect(skip().getAttribute('aria-disabled')).toBe('true');
    expect(document.activeElement).toBe(skip());

    // 잠긴 뒤 다시 눌러도 아무 일도 없습니다
    await act(async () => skip().click());
    expect(host.querySelectorAll('[data-claim]')).toHaveLength(2);
  });

  it('문장이 없으면 [건너뛰기]를 두지 않는다', async () => {
    await renderLive();
    expect(host.querySelector('[data-chat-skip]')).toBeNull();
  });
});
