import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
vi.mock('@/mocks/auth', () => ({ currentMockUserId: () => null }));
import { createChatHandlers } from '@/mocks/chat';
import { readJsonWithin, ResponseTooLargeError } from '@/shared/api/client';
import { chatPaths } from '@/shared/api/chatPaths';
import type {
  Card,
  ChatMessage,
  Evidence,
  MessagePage,
  RunEvent,
  RunView,
  SendMessageAccepted,
} from '@/types/chat';
import { chatApi, fetchMessages } from './chatApi';
import {
  chipLabel,
  chipsFor,
  createRunReader,
  emptyBasis,
  errorText,
  MASKED_TEXT,
  MAX_RESPONSE_BYTES,
  readCard,
  readMessage,
  readPlan,
  readTrace,
  shouldRefetch,
  type Basis,
} from './chatResponse';

// ── 만들기 도구 ───────────────────────────────────────────────

const TAKE = '20000000-0000-4000-8000-000000000003';

const ev = (e: Partial<Evidence> & Pick<Evidence, 'evidence_id'>): Evidence => ({
  origin: 'RECORD',
  pitch_id: null,
  take_id: TAKE,
  take_number: 3,
  kind: 'TAKE_RESULT',
  revision: 1,
  path: 'p',
  metric: 'audience_ratio',
  slide_number: 3,
  value: 0.3,
  unit: null,
  rule_version: null,
  saved: null,
  screen_snapshot_id: null,
  inputs: null,
  calc: null,
  ...e,
});

const guide = {
  source_id: 'g-1',
  title: '대본 의존 줄이기',
  quote: '인용',
  source: '자료',
  license: 'CC',
};

const claim = (
  kind: string,
  text: string,
  evidence_ids: string[] = [],
  source_ids: string[] = [],
) => ({
  kind,
  text,
  assessment_id: null,
  evidence_ids,
  source_ids,
  action_id: null,
});

function basisOf(evidence: Evidence[], sources = [guide]): Basis {
  const basis = emptyBasis();
  for (const e of evidence) basis.evidence.set(e.evidence_id, e);
  for (const s of sources) basis.sources.set(s.source_id, s);
  return basis;
}

function message(fields: Partial<Record<keyof ChatMessage, unknown>>): ChatMessage {
  const claims = (fields.claims ?? []) as { text: string }[];
  return {
    message_id: 'm-1',
    seq: 2,
    role: 'assistant',
    text: claims.map((c) => c.text).join('\n'),
    status: 'DONE',
    run_id: 'r-1',
    again_of_message_id: null,
    created_at: '2026-11-05T00:00:00Z',
    claims: null,
    cards: null,
    assessments: null,
    evidence: null,
    sources: null,
    basis: null,
    plan: null,
    trace: null,
    masked: false,
    stale_reasons: [],
    ...fields,
  } as ChatMessage;
}

const claimEvent = (seq: number, index: number, data: Record<string, unknown>): RunEvent =>
  ({
    event_seq: seq,
    event: 'claim',
    data: { index, evidence: [], sources: [], assessments: [], ...data },
    masked: false,
  }) as unknown as RunEvent;

// ── 진행 ─────────────────────────────────────────────────────

describe('진행 (L3-22)', () => {
  it('할 일은 깨진 항목만 빼고, null 은 빠진 것이 아니다', () => {
    expect(readPlan(null)).toEqual({ items: [], dropped: false });
    expect(readPlan('nope')).toEqual({ items: [], dropped: true });
    const read = readPlan([
      { step: '비교', status: 'DONE', note: null },
      { step: '', status: 'DONE', note: null },
      { step: '자료', status: 'LATER', note: null },
      { step: '확인', status: 'TODO' },
    ]);
    expect(read.items.map((s) => s.step)).toEqual(['비교', '확인']);
    expect(read.items[1]!.note).toBeNull();
    expect(read.dropped).toBe(true);
  });

  it('확인한 것은 같은 순번을 한 번만, 순번순으로', () => {
    const item = (seq: number, tool = 't') => ({
      seq,
      tool,
      args_summary: '',
      result_summary: '',
      ms: 1,
      outcome: 'OK',
    });
    const read = readTrace([item(2), item(1), item(2, 'again'), { ...item(3), outcome: 'WHAT' }]);
    expect(read.items.map((t) => [t.seq, t.tool])).toEqual([
      [1, 't'],
      [2, 't'],
    ]);
    expect(read.dropped).toBe(true);
  });
});

// ── 칩 ───────────────────────────────────────────────────────

describe('근거 칩 (12-4 F4 · L3-24)', () => {
  it('내 기록은 Take · 장, 화면 값은 링크 없이, 자료는 제목으로', () => {
    const basis = basisOf([
      ev({ evidence_id: 'r' }),
      ev({ evidence_id: 's', origin: 'SCREEN', take_id: null, saved: false }),
      ev({ evidence_id: 's2', origin: 'SCREEN', take_id: null, saved: true }),
    ]);
    const { chips, missing } = chipsFor(
      { evidence_ids: ['r', 's', 's2'], source_ids: ['g-1'] },
      basis,
    );
    expect(chips.map(chipLabel)).toEqual([
      'Take 3 · 3장',
      '지금 화면의 입력값',
      '저장된 값',
      '자료: 대본 의존 줄이기',
    ]);
    expect(chips[0]).toMatchObject({ type: 'RECORD', takeId: TAKE });
    expect(chips[1]).not.toHaveProperty('takeId');
    expect(missing).toBe(0);
  });

  it('계산 근거는 입력 근거로 칩을 만들고, 같은 Take · 장은 하나로', () => {
    const basis = basisOf([
      ev({ evidence_id: 'a', take_id: '20000000-0000-4000-8000-000000000002', take_number: 2 }),
      ev({ evidence_id: 'b' }),
      ev({ evidence_id: 'b2' }),
      ev({ evidence_id: 'd', origin: 'COMPUTED', take_id: null, inputs: ['a', 'b'] }),
    ]);
    const { chips } = chipsFor({ evidence_ids: ['d', 'b2'] }, basis);
    expect(chips.map(chipLabel)).toEqual(['Take 2 · 3장', 'Take 3 · 3장']);
  });

  it('없는 근거 · 자료는 칩을 만들지 않고 센다 — 나머지 칩은 그대로', () => {
    const basis = basisOf([ev({ evidence_id: 'r' })]);
    const { chips, missing } = chipsFor(
      { evidence_ids: ['r', 'nope'], source_ids: ['g-nope'] },
      basis,
    );
    expect([chips.length, missing]).toEqual([1, 2]);
  });

  it('서로 가리키는 계산 근거에서 멈추고, 입력 없는 계산 근거는 빠진 것으로 센다', () => {
    const basis = basisOf([
      ev({ evidence_id: 'x', origin: 'COMPUTED', inputs: ['y'] }),
      ev({ evidence_id: 'y', origin: 'COMPUTED', inputs: ['x'] }),
      ev({ evidence_id: 'z', origin: 'COMPUTED', inputs: [] }),
    ]);
    expect(chipsFor({ evidence_ids: ['x', 'z'] }, basis)).toEqual({ chips: [], missing: 2 });
  });

  it('형식이 틀린 Take ID 로는 이동 링크를 만들지 않는다 (L3-26)', () => {
    const basis = basisOf([ev({ evidence_id: 'r', take_id: '../admin' })]);
    expect(chipsFor({ evidence_ids: ['r'] }, basis).chips[0]).toMatchObject({
      takeId: null,
      takeNumber: 3,
    });
  });

  it('Take 번호 · 장이 없으면 "내 기록"', () => {
    const basis = basisOf([ev({ evidence_id: 'r', take_number: null, slide_number: null })]);
    expect(chipLabel(chipsFor({ evidence_ids: ['r'] }, basis).chips[0]!)).toBe('내 기록');
  });
});

// ── 메시지 목록의 답 ──────────────────────────────────────────

describe('메시지 목록의 답 (L3-23 · L3-24 · L3-42)', () => {
  const evidence = [ev({ evidence_id: 'r' })];

  it('문장마다 칩을 달고, 일반 조언은 근거가 실려 와도 칩 없이 라벨만', () => {
    const answer = readMessage(
      message({
        claims: [
          claim('RECORD', '기록 문장', ['r']),
          claim('GENERAL', '일반 조언', ['r'], ['g-1']),
        ],
        evidence,
        sources: [guide],
      }),
    );
    expect(answer.error).toBeNull();
    expect(answer.claims.map((c) => [c.kind, c.chips.length, c.general])).toEqual([
      ['RECORD', 1, false],
      ['GENERAL', 0, true],
    ]);
  });

  it('없는 근거를 가리키면 그 칩만 빼고 문장은 보인다', () => {
    const answer = readMessage(
      message({ claims: [claim('RECORD', '기록 문장', ['r', 'nope'])], evidence }),
    );
    expect(answer.error).toBeNull();
    expect(answer.claims[0]).toMatchObject({ missing: 1 });
    expect(answer.claims[0]!.chips).toHaveLength(1);
  });

  it('모르는 종류의 문장부터 뒤는 막고, 카드(행동)도 막는다 — 앞 문장은 그대로', () => {
    const answer = readMessage(
      message({
        claims: [
          claim('RECORD', '앞 문장', ['r']),
          claim('FUTURE', '모름'),
          claim('GENERAL', '뒤'),
        ],
        evidence,
        cards: [{ type: 'START_PRACTICE', pitch_id: '10000000-0000-4000-8000-000000000001' }],
      }),
    );
    expect(answer.error).toBe('UNKNOWN_KIND');
    expect(answer.claims.map((c) => c.text)).toEqual(['앞 문장']);
    expect(answer.cards).toEqual([]);
  });

  it.each([
    ['빈 글', claim('RECORD', '')],
    ['300자 초과', claim('RECORD', '가'.repeat(301))],
    ['참조가 목록이 아님', { ...claim('RECORD', '문장'), evidence_ids: 'r' }],
    ['문장이 객체가 아님', 'RECORD'],
  ])('문장 모양이 틀리면 계약 오류 — %s', (_, bad) => {
    const answer = readMessage(message({ claims: [claim('SYSTEM', '앞'), bad], text: '앞' }));
    expect([answer.error, answer.claims.length]).toEqual(['BAD_CLAIM', 1]);
  });

  it('300자는 코드 포인트로 센다 — 이모지 300개는 통과', () => {
    const text = '😀'.repeat(300);
    expect(readMessage(message({ claims: [claim('SYSTEM', text)] })).error).toBeNull();
  });

  it('글이 문장들을 이은 값과 다르면 손상, 끝난 답에 문장이 없으면 손상 (L3-23)', () => {
    expect(readMessage(message({ claims: [claim('SYSTEM', '하나')], text: '다름' })).error).toBe(
      'TEXT_MISMATCH',
    );
    expect(readMessage(message({ claims: [], text: '' })).error).toBe('EMPTY_ANSWER');
    expect(readMessage(message({ claims: null, text: '', status: 'FAILED' })).error).toBeNull();
  });

  it('사용자 메시지 · 가린 답은 문장 없이 글을 그대로', () => {
    expect(readMessage(message({ role: 'user', text: '질문' }))).toMatchObject({
      claims: [],
      error: null,
    });
    expect(
      readMessage(message({ masked: true, text: '근거 기록이 지워져 이 답을 가렸어요.' })),
    ).toMatchObject({ claims: [], error: null });
  });

  it('판단 문장은 그 메시지의 판단을 찾고, 없으면 판정을 추정하지 않는다', () => {
    const assessment = {
      assessment_id: 'as-1',
      quote: '많이 봤지',
      type: 'MEASURED',
      verdict: 'CONTRADICTED',
      evidence_ids: ['r'],
      source_ids: [],
      reason: '',
      supersedes: [],
      superseded_by: 'as-2',
    };
    const answer = readMessage(
      message({
        claims: [
          { ...claim('ASSESSMENT', '기록과 달라요', ['r']), assessment_id: 'as-1' },
          { ...claim('ASSESSMENT', '모름', ['r']), assessment_id: 'as-9' },
        ],
        evidence,
        assessments: [assessment],
      }),
    );
    expect(answer.claims[0]!.assessment).toMatchObject({
      verdict: 'CONTRADICTED',
      superseded_by: 'as-2',
    });
    expect(answer.claims[1]!.assessment).toBeNull();
  });
});

// ── 카드 ─────────────────────────────────────────────────────

describe('카드 (L3-28)', () => {
  const basis = basisOf([ev({ evidence_id: 'r' })]);

  it('모르는 종류 · 빠진 칸은 대체 표시, 다른 카드는 그대로', () => {
    const answer = readMessage(
      message({
        claims: [claim('SYSTEM', '안내')],
        cards: [
          { type: 'FUTURE_CARD' },
          { type: 'GOAL_RESULT', goal_version: 1, take_id: TAKE, status: 'ACHIEVED' },
          { type: 'START_PRACTICE', pitch_id: '10000000-0000-4000-8000-000000000001' },
          'nope',
        ],
      }),
    );
    expect(answer.cards.map((c) => (c.ok ? c.card.type : `×${c.type}`))).toEqual([
      '×FUTURE_CARD',
      '×GOAL_RESULT',
      'START_PRACTICE',
      '×null',
    ]);
  });

  it('연습 이동 카드는 피치 ID 형식이 맞을 때만 (L3-32)', () => {
    expect(readCard({ type: 'START_PRACTICE', pitch_id: '../../admin' }, basis).ok).toBe(false);
  });

  it('바꾸기 전 · 참고 결과는 한쪽이라도 없으면 그리지 않는다 (L3-33)', () => {
    const side = { score: 80, dimensions: { GAZE: null }, criteria_results: [] };
    const card = {
      type: 'REASSESS_RESULT',
      take_id: TAKE,
      change: {},
      before: side,
      after: side,
      changed_fields: [],
      official_differs: false,
    };
    expect(readCard(card, basis).ok).toBe(true);
    expect(readCard({ ...card, after: null }, basis).ok).toBe(false);
  });

  it('승인 확인 카드는 만료 시각 · args_hash 가 있어야 한다', () => {
    const ask = {
      type: 'ASK_CONFIRM',
      action_id: 'a-1',
      action_type: 'SAVE_GOAL',
      title: '목표를 저장할까요?',
      summary: '',
      before: null,
      after: {},
      notes: [],
      expires_at: '2026-11-05T00:10:00Z',
      args_hash: 'h',
    };
    expect(readCard(ask, basis).ok).toBe(true);
    expect(readCard({ ...ask, expires_at: '곧' }, basis).ok).toBe(false);
    expect(readCard({ ...ask, args_hash: '' }, basis).ok).toBe(false);
  });

  it('목표 저장됨 · 목표 제안은 근거 칩을 함께 준다', () => {
    const goal = {
      version: 1,
      metric: 'audience_ratio',
      operator: 'gt_base',
      slide_number: 3,
      base: { take_id: TAKE, take_number: 3, value: 0.24 },
    };
    const saved = readCard(
      { type: 'GOAL_SAVED', action_id: 'a', goal, reason: '', evidence_ids: ['r'], undo: true },
      basis,
    );
    expect(saved.ok && saved.chips.map(chipLabel)).toEqual(['Take 3 · 3장']);
    const option = {
      label: 'USER',
      goal: null,
      reason: '',
      evidence_ids: ['r'],
      content_hash: null,
    };
    const proposal = readCard(
      {
        type: 'GOAL_PROPOSAL',
        proposal_id: 'p',
        options: [option, { ...option, label: 'EXPERT', evidence_ids: [] }],
      },
      basis,
    );
    expect(proposal.ok && proposal.optionChips.map((c) => c.length)).toEqual([1, 0]);
    expect(readCard({ type: 'GOAL_PROPOSAL', proposal_id: 'p', options: [] }, basis).ok).toBe(
      false,
    );
  });
});

// ── 실행 중 문장 줄 ───────────────────────────────────────────

describe('실행 중 문장 줄 (L3-46)', () => {
  it('같은 index 는 한 번만, 근거 · 판단은 실행 전체에서 찾는다', () => {
    const reader = createRunReader();
    const assessment = {
      assessment_id: 'as-1',
      quote: 'q',
      type: 'MEASURED',
      verdict: 'SUPPORTED',
      evidence_ids: [],
      source_ids: [],
      reason: '',
      supersedes: [],
    };
    const first = claimEvent(1, 0, {
      claim: claim('RECORD', '첫 문장', ['r']),
      evidence: [ev({ evidence_id: 'r' })],
      assessments: [assessment],
    });
    expect(reader.add([first]).map((c) => c.chips.length)).toEqual([1]);
    // 다시 받은 같은 줄은 그리지 않고, 뒤 문장은 앞 줄의 근거 · 판단을 씁니다
    const later = reader.add([
      first,
      claimEvent(2, 1, {
        claim: { ...claim('ASSESSMENT', '맞아요', ['r']), assessment_id: 'as-1' },
      }),
    ]);
    expect(later.map((c) => [c.index, c.chips.length, c.assessment?.verdict])).toEqual([
      [1, 1, 'SUPPORTED'],
    ]);
    expect(reader.error).toBeNull();
  });

  it('index 를 건너뛰거나 모르는 종류가 오면 멈추고, 그 뒤 문장 · 카드는 막는다', () => {
    const reader = createRunReader();
    expect(reader.add([claimEvent(1, 0, { claim: claim('SYSTEM', '하나') })])).toHaveLength(1);
    expect(reader.add([claimEvent(2, 2, { claim: claim('SYSTEM', '셋') })])).toEqual([]);
    expect(reader.error).toBe('INDEX_GAP');
    expect(reader.add([claimEvent(3, 1, { claim: claim('SYSTEM', '둘') })])).toEqual([]);
    expect(reader.cards([{ type: 'START_PRACTICE', pitch_id: TAKE }])).toEqual([]);

    const other = createRunReader();
    expect(other.add([claimEvent(1, 0, { claim: claim('LATER', '모름') })])).toEqual([]);
    expect(other.error).toBe('UNKNOWN_KIND');
  });

  it('진행 줄은 건너뛰고 문장 줄만 읽는다', () => {
    const reader = createRunReader();
    const plan: RunEvent = { event_seq: 1, event: 'plan', data: { plan: [] }, masked: false };
    expect(reader.add([plan, claimEvent(2, 0, { claim: claim('SYSTEM', '하나') })])).toHaveLength(
      1,
    );
  });
});

// ── 크기 ─────────────────────────────────────────────────────

describe('응답 크기 (L3-22)', () => {
  const read = readJsonWithin(MAX_RESPONSE_BYTES);

  it('2 MiB 이하는 읽고, 넘으면 처리하지 않는다 — 한글은 바이트로 잰다', async () => {
    expect(await read<{ a: number }>(new Response('{"a":1}'))).toEqual({ a: 1 });
    // 한글 한 글자는 UTF-8 로 3바이트 — 글자 수로는 2 MiB 아래지만 바이트로는 넘습니다
    const korean = JSON.stringify('가'.repeat(Math.ceil(MAX_RESPONSE_BYTES / 3) + 1));
    expect(korean.length).toBeLessThan(MAX_RESPONSE_BYTES);
    await expect(read(new Response(korean))).rejects.toBeInstanceOf(ResponseTooLargeError);
  });

  it('Content-Length 가 이미 넘으면 본문을 읽지 않는다', async () => {
    const response = new Response('{}', {
      headers: { 'Content-Length': String(MAX_RESPONSE_BYTES + 1) },
    });
    await expect(read(response)).rejects.toBeInstanceOf(ResponseTooLargeError);
  });
});

// ── 목의 모든 시나리오 ────────────────────────────────────────

describe('목 시나리오를 응답 검사로 읽기', () => {
  const server = setupServer();
  beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
  afterAll(() => server.close());
  beforeEach(() => server.resetHandlers(...createChatHandlers(() => 'u').handlers));

  const base = 'http://localhost';
  let key = 0;
  const call = async <T>(path: string, method = 'GET', body?: unknown) => {
    const r = await fetch(base + path, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return (await r.json()) as T;
  };

  /** 보내고 끝까지 — 승인 대기면 승인합니다. 받은 문장 줄을 실행 읽기로 함께 읽습니다 */
  async function ask(text: string, extra: Record<string, unknown> = {}) {
    const accepted = await call<SendMessageAccepted>(chatPaths.send, 'POST', {
      conversation_id: null,
      scope_pitch_id: null,
      text,
      page: { route: '/', pitch_id: null, take_id: null, screen: null },
      client_request_id: `00000000-0000-4000-8000-${String(++key).padStart(12, '0')}`,
      again_of_message_id: null,
      choice: null,
      ...extra,
    });
    const reader = createRunReader();
    let after = 0;
    for (let i = 0; i < 40; i++) {
      const view = await call<RunView>(`${chatPaths.run(accepted.run_id)}?after_seq=${after}`);
      reader.add(view.events);
      after = view.events.at(-1)?.event_seq ?? after;
      const ask = view.cards.find((c) => c.type === 'ASK_CONFIRM') as
        Extract<Card, { type: 'ASK_CONFIRM' }> | undefined;
      if (ask && reader.cards(view.cards).every((c) => c.ok))
        await call(chatPaths.decision(accepted.run_id, ask.action_id), 'POST', {
          decision: 'APPROVE',
          args_hash: ask.args_hash,
        });
      else if (!['QUEUED', 'RUNNING', 'WAITING_APPROVAL'].includes(view.status)) break;
    }
    const page = await call<MessagePage>(chatPaths.messages(accepted.conversation_id));
    return {
      reader,
      answer: readMessage(page.messages.at(-1)!),
      conversationId: accepted.conversation_id,
    };
  }

  it.each([
    '비교해 줘',
    '오늘 날씨 어때?',
    '목표 제안해 줘',
    '자신감 있게 하는 걸 목표로 제안해 줘',
    '3장 시선을 목표로 잡아 줘',
    '대본 보고 한 발표였어',
    '바로 발표할래',
    '짧게 설명해 줘, 기억해',
    '이번엔 청중 많이 봤지?',
  ])('"%s" — 계약 오류 없이, 빠진 칩 없이 읽힌다', async (text) => {
    const { reader, answer } = await ask(text);
    expect([reader.error, answer.error]).toEqual([null, null]);
    expect(answer.claims.length).toBeGreaterThan(0);
    expect(answer.claims.filter((c) => c.missing > 0)).toEqual([]);
    expect(answer.cards.filter((c) => !c.ok)).toEqual([]);
  });

  it('제안에서 고른 답도 칩 · 카드가 모두 읽힌다', async () => {
    const proposed = await ask('목표 제안해 줘');
    const card = proposed.answer.cards.find((c) => c.ok && c.card.type === 'GOAL_PROPOSAL');
    const proposal = card?.ok ? (card.card as Extract<Card, { type: 'GOAL_PROPOSAL' }>) : null;
    const { answer } = await ask('추천 안으로', {
      conversation_id: proposed.conversationId,
      choice: { proposal_id: proposal!.proposal_id, option: 'EXPERT' },
    });
    expect(answer.error).toBeNull();
    const saved = answer.cards.find((c) => c.ok && c.card.type === 'GOAL_SAVED');
    expect(saved?.ok && saved.chips.length).toBeGreaterThan(0);
  });

  it('"깨진 답" — 문장은 모두 보이고, 없는 근거 칩 · 일반 조언 칩은 빠지고, 모르는 카드는 대체 표시', async () => {
    const { reader, answer } = await ask('깨진 답');
    expect([reader.error, answer.error]).toEqual([null, null]);
    expect(answer.claims.map((c) => [c.kind, c.missing])).toEqual([
      ['RECORD', 1],
      ['GENERAL', 0],
    ]);
    expect(answer.claims[1]!.chips).toEqual([]);
    expect(answer.cards.map((c) => c.ok)).toEqual([false, true]);
  });

  it('"모르는 문장" — 앞 문장만 보이고 뒤 문장 · 카드는 막힌다', async () => {
    const { reader, answer } = await ask('모르는 문장');
    expect([reader.error, answer.error]).toEqual(['UNKNOWN_KIND', 'UNKNOWN_KIND']);
    expect(answer.claims.map((c) => c.kind)).toEqual(['RECORD']);
    expect(answer.cards).toEqual([]);
  });
});

// ── 점검 반영 ─────────────────────────────────────────────────

describe('오류 안내 · 다시 읽기 (L3-23 · L3-24)', () => {
  it('비었으면 "지금은 답을 만들지 못했어요." 와 다시 읽기, 검사 실패는 근거 안내만', () => {
    expect([errorText('EMPTY_ANSWER'), shouldRefetch('EMPTY_ANSWER')]).toEqual([
      '지금은 답을 만들지 못했어요.',
      true,
    ]);
    expect(shouldRefetch('TEXT_MISMATCH')).toBe(true);
    for (const error of ['UNKNOWN_KIND', 'BAD_CLAIM', 'INDEX_GAP'] as const)
      expect([errorText(error), shouldRefetch(error)]).toEqual([
        '답의 근거를 확인하지 못했으니 다시 물어봐 주세요.',
        false,
      ]);
    expect(shouldRefetch(null)).toBe(false);
  });
});

describe('계약 오류 뒤의 카드', () => {
  const goal = {
    version: 1,
    metric: 'audience_ratio',
    operator: 'gt_base',
    slide_number: 3,
    base: { take_id: TAKE, take_number: 3, value: 0.24 },
  };
  const saved = {
    type: 'GOAL_SAVED',
    action_id: 'a',
    goal,
    reason: '',
    evidence_ids: [],
    undo: true,
  };
  const practice = { type: 'START_PRACTICE', pitch_id: '10000000-0000-4000-8000-000000000001' };
  const proposal = {
    type: 'GOAL_PROPOSAL',
    proposal_id: 'p',
    options: [{ label: 'USER', goal: null, reason: '', evidence_ids: [], content_hash: null }],
  };

  it('이미 일어난 결과(목표 저장됨)는 남기고, 새 행동을 부르는 카드 · 대체 표시는 뺀다', () => {
    const answer = readMessage(
      message({
        claims: [claim('SYSTEM', '앞'), claim('FUTURE', '모름')],
        text: '앞\n모름',
        cards: [saved, practice, proposal, { type: 'FUTURE_CARD' }],
      }),
    );
    expect(answer.error).toBe('UNKNOWN_KIND');
    expect(answer.cards.map((c) => c.ok && c.card.type)).toEqual(['GOAL_SAVED']);
  });

  it('실행 중에도 같다 — 승인 확인은 막고 결과 카드는 남긴다', () => {
    const reader = createRunReader();
    reader.add([claimEvent(1, 1, { claim: claim('SYSTEM', '건너뜀') })]);
    const ask = {
      type: 'ASK_CONFIRM',
      action_id: 'a-1',
      action_type: 'SAVE_GOAL',
      title: '저장할까요?',
      summary: '',
      before: null,
      after: {},
      notes: [],
      expires_at: '2026-11-05T00:10:00Z',
      args_hash: 'h',
    };
    expect(reader.cards([ask, saved]).map((c) => c.ok && c.card.type)).toEqual(['GOAL_SAVED']);
  });
});

describe('대화 API 의 크기 제한 연결 (L3-22)', () => {
  afterEach(() => vi.unstubAllGlobals());
  const huge = () => new Response(JSON.stringify({ pad: 'x'.repeat(MAX_RESPONSE_BYTES) }));

  it('실행 조회 · 대화의 실행 조회 · 취소 · 메시지 목록이 2 MiB 를 넘으면 처리하지 않는다', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(huge())),
    );
    for (const call of [
      () => chatApi.run('r-1', 0),
      () => chatApi.conversationRun('c-1', 0),
      () => chatApi.cancel('r-1'),
      () => fetchMessages('c-1'),
    ])
      await expect(call()).rejects.toBeInstanceOf(ResponseTooLargeError);
  });

  it('메시지 목록은 before_seq 로 앞 페이지를 읽는다', async () => {
    const fetchMock = vi.fn((_url: string) =>
      Promise.resolve(Response.json({ messages: [], next_before_seq: null })),
    );
    vi.stubGlobal('fetch', fetchMock);
    await fetchMessages('c-1', 31);
    await fetchMessages('c-1');
    // 앞의 API 주소(VITE_API_BASE)는 환경마다 달라 경로만 봅니다
    expect(fetchMock.mock.calls.map(([url]) => url.slice(url.indexOf('/api/')))).toEqual([
      '/api/chat/conversations/c-1/messages?before_seq=31',
      '/api/chat/conversations/c-1/messages',
    ]);
  });
});

// ── 1~4단계 점검 반영 ────────────────────────────────────────

describe('카드 필수 칸 — AI 스키마에 맞춤', () => {
  const basis = emptyBasis();
  const goal = {
    version: 1,
    metric: 'audience_ratio',
    operator: 'gt_base',
    slide_number: 3,
    base: { take_id: TAKE, take_number: 3, value: 0.24 },
  };
  const saved = (g: unknown) =>
    readCard(
      { type: 'GOAL_SAVED', action_id: 'a', goal: g, reason: '', evidence_ids: [], undo: true },
      basis,
    ).ok;

  it('목표는 비교 방법이 정한 값이고 기준 값이 있어야 한다', () => {
    expect(saved(goal)).toBe(true);
    expect(saved({ ...goal, operator: 'DROP TABLE' })).toBe(false);
    expect(saved({ ...goal, base: undefined })).toBe(false);
    expect(saved({ ...goal, base: { ...goal.base, value: '0.24' } })).toBe(false);
  });

  it('차원 상태는 글자 · null 만, 기억 항목은 정한 키만, 만료 시각은 ISO 형식만', () => {
    const side = (dimensions: unknown) => ({ score: 1, dimensions, criteria_results: [] });
    const reassess = (dimensions: unknown) =>
      readCard(
        {
          type: 'REASSESS_RESULT',
          take_id: TAKE,
          change: {},
          before: side(dimensions),
          after: side({}),
          changed_fields: [],
          official_differs: false,
        },
        basis,
      ).ok;
    expect([reassess({ GAZE: 'GOOD', PACE: null }), reassess({ GAZE: { x: 1 } })]).toEqual([
      true,
      false,
    ]);
    const memory = (key: string) =>
      readCard({ type: 'MEMORY_SAVED', item_id: 'i', key, value: '짧게' }, basis).ok;
    expect([memory('TONE'), memory('PASSWORD')]).toEqual([true, false]);
    const ask = (expires_at: string) =>
      readCard(
        {
          type: 'ASK_CONFIRM',
          action_id: 'a',
          action_type: 'SAVE_GOAL',
          title: 't',
          summary: '',
          before: null,
          after: {},
          notes: [],
          expires_at,
          args_hash: 'h',
        },
        basis,
      ).ok;
    expect([ask('2026-11-05T00:10:00Z'), ask('1'), ask('곧')]).toEqual([true, false, false]);
  });
});

describe('경로의 ID 인코딩', () => {
  it('서버가 준 ID 에 / · ? · .. 이 섞여도 다른 경로가 되지 않는다', () => {
    const path = chatPaths.undo('r-1', '../../../runs/X/cancel?');
    expect(path).toBe('/api/chat/runs/r-1/actions/..%2F..%2F..%2Fruns%2FX%2Fcancel%3F/undo');
    expect(new URL(path, 'http://x').pathname.endsWith('/undo')).toBe(true);
    // 목의 경로 자리표시는 그대로
    expect(chatPaths.run(':runId')).toBe('/api/chat/runs/:runId');
  });
});

describe('메시지 목록의 빠진 칸 (BE 미구현)', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('최신 아님 사유가 빠져 오면 빈 목록으로 채운다', async () => {
    const fromBe = { ...message({ claims: [claim('SYSTEM', '안내')] }) } as Partial<ChatMessage>;
    delete fromBe.stale_reasons;
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.resolve(Response.json({ messages: [fromBe], next_before_seq: null }))),
    );
    expect((await fetchMessages('c-1')).messages[0]!.stale_reasons).toEqual([]);
  });
});

// ── 새 BE (55aa10d0) 에 맞춤 ──────────────────────────────────

describe('가린 문장 줄 · 공백 글', () => {
  it('가린 줄은 순번 하나를 채우는 안내 — 칩 없이, 뒤 문장은 이어서 읽는다', () => {
    const reader = createRunReader();
    const masked = { event_seq: 1, event: 'claim', data: null, masked: true } as RunEvent;
    const shown = reader.add([masked, claimEvent(2, 1, { claim: claim('SYSTEM', '뒤 문장') })]);
    expect(shown.map((c) => [c.index, c.masked, c.text, c.chips.length])).toEqual([
      [0, true, MASKED_TEXT, 0],
      [1, false, '뒤 문장', 0],
    ]);
    expect(reader.error).toBeNull();
  });

  it('공백뿐인 문장도 AI 스키마(min_length=1)처럼 받는다', () => {
    const answer = readMessage(message({ claims: [claim('SYSTEM', ' ')] }));
    expect([answer.error, answer.claims.length]).toEqual([null, 1]);
  });
});
