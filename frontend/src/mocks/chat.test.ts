import { setupServer } from 'msw/node';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
vi.mock('./auth', () => ({ currentMockUserId: () => null }));
import { createChatHandlers, type MockChatControls } from './chat';
import { chatPaths } from '@/shared/api/chatPaths';
import type {
  ActionDecisionResponse,
  Card,
  ConversationList,
  MessagePage,
  MemorySettings,
  RunView,
  SendMessageAccepted,
} from '@/types/chat';

let user: string | null = 'a';
let controls: MockChatControls;
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterAll(() => server.close());
beforeEach(() => {
  user = 'a';
  const chat = createChatHandlers(() => user);
  controls = chat.controls;
  server.resetHandlers(...chat.handlers);
});

const url = (path: string) => `http://localhost${path}`;
const json = <T>(r: Response) => r.json() as Promise<T>;
const call = (path: string, method = 'GET', body?: unknown) =>
  fetch(url(path), {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

let key = 0;
const message = (text: string, extra: Record<string, unknown> = {}) => ({
  conversation_id: null,
  scope_pitch_id: null,
  text,
  page: { route: '/', pitch_id: null, take_id: null, screen: null },
  client_request_id: `req-${++key}`,
  again_of_message_id: null,
  choice: null,
  ...extra,
});
const send = (body: unknown) => call(chatPaths.send, 'POST', body);
const accepted = async (text: string, extra?: Record<string, unknown>) => {
  const r = await send(message(text, extra));
  expect(r.status).toBe(202);
  return json<SendMessageAccepted>(r);
};
const poll = (runId: string, afterSeq = 0) =>
  call(`${chatPaths.run(runId)}?after_seq=${afterSeq}`).then(json<RunView>);
/** 끝나거나 승인 대기가 될 때까지 조회합니다 */
async function settle(runId: string) {
  for (let i = 0; i < 20; i++) {
    const view = await poll(runId);
    if (!['QUEUED', 'RUNNING'].includes(view.status)) return view;
  }
  throw new Error('실행이 끝나지 않음');
}
const messages = (conversationId: string) =>
  call(chatPaths.messages(conversationId)).then(json<MessagePage>);
const askOf = (view: RunView) =>
  view.cards.find((c) => c.type === 'ASK_CONFIRM') as Extract<Card, { type: 'ASK_CONFIRM' }>;

describe('사용 가능 여부', () => {
  it('켜짐 · 꺼짐 · 응답 없음을 고를 수 있다', async () => {
    expect(await call(chatPaths.availability).then(json)).toEqual({ enabled: true });
    controls.disable();
    expect(await call(chatPaths.availability).then(json)).toEqual({ enabled: false });
    controls.unreachable();
    await expect(call(chatPaths.availability)).rejects.toThrow();
  });
});

describe('보내기 → 조회', () => {
  it('조회할 때마다 한 단계씩 나아가고, 문장은 index 순서로 한 번씩 온다', async () => {
    const { run_id, conversation_id } = await accepted('지난번보다 청중을 더 봤어?');
    let cursor = 0;
    const events: RunView['events'] = [];
    let view: RunView;
    do {
      view = await poll(run_id, cursor);
      events.push(...view.events);
      cursor = events.at(-1)?.event_seq ?? cursor;
    } while (!['DONE', 'PARTIAL', 'FAILED'].includes(view.status));

    expect(events.map((e) => e.event_seq)).toEqual(events.map((_, i) => i + 1));
    const claims = events.flatMap((e) => (e.event === 'claim' ? [e.data] : []));
    expect(claims.map((c) => c.index)).toEqual([0, 1, 2]);
    expect(claims.map((c) => c.claim.kind)).toEqual(['RECORD', 'GUIDE', 'GENERAL']);
    // 계산 근거는 입력 근거와 함께 온다
    const computed = claims[0]!.evidence.find((e) => e.origin === 'COMPUTED')!;
    expect(
      computed.inputs!.every((id) => claims[0]!.evidence.some((e) => e.evidence_id === id)),
    ).toBe(true);
    expect(claims[2]!.claim.evidence_ids).toEqual([]);

    const answer = (await messages(conversation_id)).items.at(-1)!;
    expect(answer).toMatchObject({ role: 'assistant', status: 'DONE', run_id });
    expect(answer.text).toBe(claims.map((c) => c.claim.text).join('\n'));
  });

  it('같은 커서로 다시 조회하면 그 뒤 이벤트를 다시 준다 — 순번은 바뀌지 않는다', async () => {
    const { run_id } = await accepted('비교해 줘');
    await poll(run_id);
    await poll(run_id);
    const first = await poll(run_id, 1);
    const again = await poll(run_id, 1);
    expect(again.events.slice(0, first.events.length)).toEqual(first.events);
  });

  it('같은 키 · 같은 요청은 같은 실행, 다른 요청은 409 AGENT_REQUEST_CONFLICT', async () => {
    const body = message('비교해 줘');
    const a = await send(body).then(json<SendMessageAccepted>);
    const b = await send({ ...body, page: { ...body.page } }).then(json<SendMessageAccepted>);
    expect(b).toEqual(a);
    const conflict = await send({ ...body, text: '다른 질문' });
    expect(conflict.status).toBe(409);
    expect(await conflict.json()).toMatchObject({ code: 'AGENT_REQUEST_CONFLICT' });
  });

  it('공백뿐 · 2,000자(코드 포인트) 초과는 422, 딱 2,000자는 받는다', async () => {
    expect((await send(message('   '))).status).toBe(422);
    expect((await send(message('😀'.repeat(2001)))).status).toBe(422);
    expect((await send(message('😀'.repeat(2000)))).status).toBe(202);
  });

  it('실행 중인 대화에 또 보내면 409 AGENT_RUN_IN_PROGRESS', async () => {
    const { conversation_id } = await accepted('비교해 줘');
    const r = await send(message('하나 더', { conversation_id }));
    expect(r.status).toBe(409);
    expect(await r.json()).toMatchObject({ code: 'AGENT_RUN_IN_PROGRESS' });
  });

  it('붐빔 · 하루 한도는 429 와 Retry-After 를 준다', async () => {
    controls.fail('AGENT_BUSY');
    const busy = await send(message('비교해 줘'));
    expect([busy.status, busy.headers.get('Retry-After')]).toEqual([429, '5']);
    controls.fail('AGENT_DAILY_LIMIT');
    const limit = await send(message('비교해 줘'));
    const wait = Number(limit.headers.get('Retry-After'));
    expect(limit.status).toBe(429);
    expect(wait > 0 && wait <= 86_400).toBe(true);
    // 걸어 둔 실패는 한 번만
    expect((await send(message('비교해 줘'))).status).toBe(202);
  });

  it('실행 실패를 걸면 첫 문장 뒤 FAILED 와 오류 코드로 끝나고 받은 문장은 남는다', async () => {
    controls.runFail('AGENT_TIMEOUT');
    const { run_id, conversation_id } = await accepted('비교해 줘');
    const view = await settle(run_id);
    expect(view).toMatchObject({ status: 'FAILED', error: { code: 'AGENT_TIMEOUT' } });
    const answer = (await messages(conversation_id)).items.at(-1)!;
    expect([answer.status, answer.claims.length]).toEqual(['FAILED', 1]);
  });

  it('[취소]하면 CANCELLED 로 끝나고 다시 취소해도 같다', async () => {
    const { run_id } = await accepted('비교해 줘');
    await poll(run_id);
    const cancelled = await call(chatPaths.cancel(run_id), 'POST').then(json<RunView>);
    expect(cancelled.status).toBe('CANCELLED');
    expect((await call(chatPaths.cancel(run_id), 'POST').then(json<RunView>)).status).toBe(
      'CANCELLED',
    );
    expect((await poll(run_id)).status).toBe('CANCELLED');
  });
});

describe('승인 카드', () => {
  it('승인 대기에서 [승인]하면 목표를 저장하고, 같은 결정은 처음 결과를 준다', async () => {
    const { run_id, conversation_id } = await accepted('3장 시선을 목표로 잡아 줘');
    const waiting = await settle(run_id);
    expect(waiting.status).toBe('WAITING_APPROVAL');
    const ask = askOf(waiting);
    expect(ask).toMatchObject({ action_type: 'SAVE_GOAL' });

    const wrong = await call(chatPaths.decision(ask.action_id), 'POST', {
      decision: 'APPROVE',
      args_hash: 'x',
    });
    expect(wrong.status).toBe(409);

    const body = { decision: 'APPROVE', args_hash: ask.args_hash };
    const first = await call(chatPaths.decision(ask.action_id), 'POST', body).then(
      json<ActionDecisionResponse>,
    );
    const again = await call(chatPaths.decision(ask.action_id), 'POST', body).then(
      json<ActionDecisionResponse>,
    );
    expect(again).toEqual(first);

    expect((await settle(run_id)).status).toBe('DONE');
    const answer = (await messages(conversation_id)).items.at(-1)!;
    expect(answer.claims.at(-1)!.text).toBe('목표를 저장했어요.');
    expect(answer.cards.map((c) => c.type)).toEqual(['GOAL_SAVED']);
    // 목표 저장됨은 승인한 그 행동의 결과다 — 같은 action_id
    expect((answer.cards[0] as Extract<Card, { type: 'GOAL_SAVED' }>).action_id).toBe(
      ask.action_id,
    );
  });

  it('[거절]하면 AI 를 다시 부르지 않고 거절 문장으로 끝난다', async () => {
    const { run_id, conversation_id } = await accepted('목표로 잡아 줘');
    const ask = askOf(await settle(run_id));
    const r = await call(chatPaths.decision(ask.action_id), 'POST', {
      decision: 'REJECT',
      args_hash: ask.args_hash,
    });
    expect(await r.json()).toMatchObject({ action_status: 'REJECTED', run_status: 'DONE' });
    const answer = (await messages(conversation_id)).items.at(-1)!;
    expect(answer.claims.at(-1)!.text).toBe('목표를 저장하지 않았어요.');
  });

  it('승인 대기 중 새 메시지는 앞 확인을 취소하고 받는다 — 그 뒤 승인은 AGENT_CANCELLED', async () => {
    const { run_id, conversation_id } = await accepted('목표로 잡아 줘');
    const ask = askOf(await settle(run_id));
    await accepted('아니 그냥 비교해 줘', { conversation_id });
    expect((await poll(run_id)).status).toBe('CANCELLED');
    const texts = (await messages(conversation_id)).items.map((m) => m.text);
    expect(texts.some((t) => t.endsWith('새 메시지를 받아 앞의 확인 요청은 취소했어요.'))).toBe(
      true,
    );
    const late = await call(chatPaths.decision(ask.action_id), 'POST', {
      decision: 'APPROVE',
      args_hash: ask.args_hash,
    });
    expect(await late.json()).toMatchObject({ code: 'AGENT_CANCELLED' });
  });

  it('만료되면 만료 문장으로 끝나고 그 뒤 승인은 AGENT_EXPIRED', async () => {
    const { run_id } = await accepted('목표로 잡아 줘');
    const ask = askOf(await settle(run_id));
    controls.expire();
    expect((await poll(run_id)).status).toBe('EXPIRED');
    const late = await call(chatPaths.decision(ask.action_id), 'POST', {
      decision: 'APPROVE',
      args_hash: ask.args_hash,
    });
    expect(await late.json()).toMatchObject({ code: 'AGENT_EXPIRED' });
  });

  it('[되돌리기]는 처음 결과를 다시 주고 대화에 시스템 메시지를 남긴다', async () => {
    const { run_id, conversation_id } = await accepted('목표로 잡아 줘');
    const ask = askOf(await settle(run_id));
    await call(chatPaths.decision(ask.action_id), 'POST', {
      decision: 'APPROVE',
      args_hash: ask.args_hash,
    });
    await settle(run_id);
    const saved = (await messages(conversation_id)).items.at(-1)!.cards[0] as Extract<
      Card,
      { type: 'GOAL_SAVED' }
    >;

    const first = await call(chatPaths.undo(saved.action_id), 'POST').then(json);
    expect(await call(chatPaths.undo(saved.action_id), 'POST').then(json)).toEqual(first);
    expect((await messages(conversation_id)).items.at(-1)).toMatchObject({
      role: 'system',
      text: '목표를 되돌렸어요.',
    });
  });
});

describe('기억', () => {
  it('"기억해" 는 기억했어요 카드를 만들고 설정에 항목이 생긴다 — 지우면 사라진다', async () => {
    const { run_id, conversation_id } = await accepted('앞으로 짧게 설명해 줘, 기억해');
    await settle(run_id);
    const card = (await messages(conversation_id)).items.at(-1)!.cards[0] as Extract<
      Card,
      { type: 'MEMORY_SAVED' }
    >;
    expect(card).toMatchObject({ type: 'MEMORY_SAVED', key: 'TONE' });
    const memory = await call(chatPaths.memory).then(json<MemorySettings>);
    expect(memory.items.map((i) => i.item_id)).toEqual([card.item_id]);

    await call(chatPaths.memoryItem(card.item_id), 'DELETE');
    expect((await call(chatPaths.memory).then(json<MemorySettings>)).items).toEqual([]);
  });

  it("'기억 없이' 대화에서는 카드를 만들지 않는다", async () => {
    const created = await call(chatPaths.conversations, 'POST', {
      scope_pitch_id: null,
      page: message('').page,
      memory_off: true,
    }).then(json<{ conversation_id: string }>);
    const { run_id } = await accepted('짧게 설명해 줘, 기억해', {
      conversation_id: created.conversation_id,
    });
    await settle(run_id);
    const answer = (await messages(created.conversation_id)).items.at(-1)!;
    expect(answer.cards).toEqual([]);
  });
});

describe('사용자 · 대화 목록', () => {
  it('처음 보는 사용자에게 최신 아님 배지가 있는 지난 대화가 하나 있다', async () => {
    const list = await call(chatPaths.conversations).then(json<ConversationList>);
    expect(list.items).toHaveLength(1);
    const old = await messages(list.items[0]!.conversation_id);
    expect(old.items.at(-1)!.stale_reasons).toEqual(['NEW_TAKE']);
  });

  it('로그인 전은 401, 다른 사용자의 대화는 404', async () => {
    const { conversation_id } = await accepted('비교해 줘');
    user = 'b';
    expect((await call(chatPaths.messages(conversation_id))).status).toBe(404);
    user = null;
    expect((await call(chatPaths.conversations)).status).toBe(401);
  });

  it('실행 중에는 범위를 바꿀 수 없다', async () => {
    const { conversation_id } = await accepted('비교해 줘');
    const r = await call(chatPaths.scope(conversation_id), 'PUT', { pitch_id: null });
    expect(await r.json()).toMatchObject({ code: 'AGENT_RUN_IN_PROGRESS' });
  });
});

describe('그 밖의 시나리오', () => {
  const cardsOf = async (conversationId: string) =>
    (await messages(conversationId)).items.at(-1)!.cards;

  it('다시 평가는 승인 뒤 참고 결과 카드를 낸다', async () => {
    const { run_id, conversation_id } = await accepted('대본 보고 한 발표였어');
    const ask = askOf(await settle(run_id));
    expect(ask.action_type).toBe('REASSESS');
    expect(ask.notes).toContain('참고 결과이고 공식 결과는 그대로예요');
    await call(chatPaths.decision(ask.action_id), 'POST', {
      decision: 'APPROVE',
      args_hash: ask.args_hash,
    });
    await settle(run_id);
    expect((await cardsOf(conversation_id)).map((c) => c.type)).toEqual(['REASSESS_RESULT']);
  });

  it('"목표 제안" 은 제안 카드가 먼저 맞고, 고르면 승인 없이 목표가 저장된다', async () => {
    const { run_id, conversation_id } = await accepted('목표 제안해 줘');
    expect((await settle(run_id)).status).toBe('DONE');
    const proposal = (await cardsOf(conversation_id))[0] as Extract<
      Card,
      { type: 'GOAL_PROPOSAL' }
    >;
    expect(proposal.type).toBe('GOAL_PROPOSAL');

    const chosen = await accepted('추천 안으로 목표를 잡아 줘', {
      conversation_id,
      choice: { proposal_id: proposal.proposal_id, option: 'EXPERT' },
    });
    const view = await settle(chosen.run_id);
    expect(view.status).toBe('DONE');
    expect((await cardsOf(conversation_id)).map((c) => c.type)).toEqual(['GOAL_SAVED']);
  });

  /** 제안 카드를 받은 대화와 그 제안 */
  async function proposalConversation() {
    const { run_id, conversation_id } = await accepted('목표 제안해 줘');
    await settle(run_id);
    const proposal = (await cardsOf(conversation_id))[0] as Extract<
      Card,
      { type: 'GOAL_PROPOSAL' }
    >;
    return { conversation_id, proposal };
  }

  it('PARTIAL 로 끊겨도 카드 없는 "목표를 저장했어요" 는 없다 — 저장 전 근거 문장에서 끊긴다', async () => {
    const { conversation_id, proposal } = await proposalConversation();
    controls.partial();
    const { run_id } = await accepted('추천 안으로 잡아 줘', {
      conversation_id,
      choice: { proposal_id: proposal.proposal_id, option: 'EXPERT' },
    });
    expect((await settle(run_id)).status).toBe('PARTIAL');
    const answer = (await messages(conversation_id)).items.at(-1)!;
    expect(answer.claims.map((c) => c.kind)).toEqual(['RECORD']);
    expect(answer.cards).toEqual([]);
  });

  it('고른 안의 목표를 저장하고, 목표 카드의 근거는 같은 메시지에 있다', async () => {
    const { conversation_id, proposal } = await proposalConversation();
    const { run_id } = await accepted('내 안으로 잡아 줘', {
      conversation_id,
      choice: { proposal_id: proposal.proposal_id, option: 'USER' },
    });
    await settle(run_id);
    const answer = (await messages(conversation_id)).items.at(-1)!;
    const saved = answer.cards[0] as Extract<Card, { type: 'GOAL_SAVED' }>;
    // 사용자 안은 장이 없는 목표라 기준도 전체 장 값
    expect(saved.goal).toMatchObject({
      slide_number: null,
      status: 'OPEN',
      version: 1,
      base: { value: 0.35 },
    });
    const ids = answer.evidence.map((e) => e.evidence_id);
    expect(saved.evidence_ids.every((id) => ids.includes(id))).toBe(true);
  });

  it('이 대화에 없는 제안 · 대화 없는 고르기는 404', async () => {
    const { conversation_id } = await proposalConversation();
    const unknown = await send(
      message('골라 줘', { conversation_id, choice: { proposal_id: 'nope', option: 'EXPERT' } }),
    );
    expect(unknown.status).toBe(404);
    const orphan = await send(
      message('골라 줘', { choice: { proposal_id: 'nope', option: 'USER' } }),
    );
    expect(orphan.status).toBe(404);
  });

  it('PARTIAL 을 걸면 첫 문장만 남긴 채 PARTIAL 로 끝난다', async () => {
    controls.partial();
    const { run_id, conversation_id } = await accepted('비교해 줘');
    expect(await settle(run_id)).toMatchObject({ status: 'PARTIAL', error: null });
    expect((await messages(conversation_id)).items.at(-1)!.claims).toHaveLength(1);
  });

  it('"연습 준비" 는 [연습 준비로] 버튼, "날씨" 는 REFUSED', async () => {
    const practice = await accepted('바로 발표할래');
    await settle(practice.run_id);
    expect(await cardsOf(practice.conversation_id)).toEqual([
      { type: 'START_PRACTICE', pitch_id: expect.any(String) },
    ]);
    const refused = await accepted('오늘 날씨 어때?');
    expect((await settle(refused.run_id)).status).toBe('REFUSED');
  });

  it('범위를 바꾸면 그 범위로 대화 목록을 거를 수 있다', async () => {
    const { run_id, conversation_id } = await accepted('비교해 줘');
    await settle(run_id);
    const list = await call(chatPaths.conversations).then(json<ConversationList>);
    const pitchId = list.items.find((c) => c.scope_pitch_id)!.scope_pitch_id!;
    const changed = await call(chatPaths.scope(conversation_id), 'PUT', { pitch_id: pitchId });
    expect(await changed.json()).toMatchObject({ scope_pitch_id: pitchId });
    const filtered = await call(`${chatPaths.conversations}?pitch_id=${pitchId}`).then(
      json<ConversationList>,
    );
    expect(filtered.items.map((c) => c.conversation_id)).toContain(conversation_id);
  });

  it('기억을 끄면 카드를 만들지 않고, 전체 지우기는 항목을 모두 지운다', async () => {
    const first = await accepted('짧게 설명해 줘, 기억해');
    await settle(first.run_id);
    await call(chatPaths.memoryItems, 'DELETE');
    expect((await call(chatPaths.memory).then(json<MemorySettings>)).items).toEqual([]);

    await call(chatPaths.memory, 'PUT', { enabled: false });
    const off = await accepted('짧게 설명해 줘, 기억해');
    await settle(off.run_id);
    expect(await cardsOf(off.conversation_id)).toEqual([]);
  });
});

describe('대기 · 범위 · 판정 · 목표 확인 · 화면 값', () => {
  it('접수 직후 첫 조회는 QUEUED 그대로, 다음 조회부터 RUNNING', async () => {
    const { run_id } = await accepted('비교해 줘');
    expect(await poll(run_id)).toMatchObject({ status: 'QUEUED', events: [] });
    expect((await poll(run_id)).status).toBe('RUNNING');
  });

  it('없는 피치를 범위로 보내거나 새 대화를 만들면 404', async () => {
    expect((await send(message('비교해 줘', { scope_pitch_id: 'nope' }))).status).toBe(404);
    const created = await call(chatPaths.conversations, 'POST', {
      scope_pitch_id: 'nope',
      page: message('').page,
      memory_off: false,
    });
    expect(created.status).toBe(404);
  });

  it('"봤지" 는 판단 문장과 판단을 함께 내고, 다시 물으면 앞 판단을 대체한다', async () => {
    const first = await accepted('이번엔 청중 많이 봤지?');
    const view = await settle(first.run_id);
    expect(view.status).toBe('DONE');
    const second = await accepted('그래도 청중 많이 봤지?', {
      conversation_id: first.conversation_id,
    });
    await settle(second.run_id);

    const answers = (await messages(first.conversation_id)).items.filter(
      (m) => m.role === 'assistant',
    );
    const [old, next] = answers.map((m) => m.assessments[0]!);
    expect(answers[0]!.claims[0]).toMatchObject({
      kind: 'ASSESSMENT',
      assessment_id: old!.assessment_id,
    });
    expect(old).toMatchObject({ quote: '이번엔 청중 많이 봤지', verdict: 'CONTRADICTED' });
    // quote 는 사용자 말 원문의 부분 문자열
    expect('이번엔 청중 많이 봤지?'.includes(old!.quote)).toBe(true);
    expect(next!.supersedes).toEqual([old!.assessment_id]);
    expect([old!.superseded_by, next!.superseded_by]).toEqual([next!.assessment_id, null]);
  });

  it('저장된 목표는 자동 확인 결과를 사용자 말 없이 그 대화에 남긴다', async () => {
    const { run_id, conversation_id } = await accepted('목표로 잡아 줘');
    const ask = askOf(await settle(run_id));
    await call(chatPaths.decision(ask.action_id), 'POST', {
      decision: 'APPROVE',
      args_hash: ask.args_hash,
    });
    await settle(run_id);
    const before = (await messages(conversation_id)).items.length;

    controls.goalCheck('NOT_ACHIEVED');
    const items = (await messages(conversation_id)).items;
    expect(items).toHaveLength(before + 1);
    expect(items.at(-1)!.cards[0]).toMatchObject({
      type: 'GOAL_RESULT',
      goal_version: 1,
      status: 'NOT_ACHIEVED',
    });
  });

  it('피치 만들기 화면에서 물으면 화면 값 근거로 답한다 — 결과 판이 아니라 스냅샷을 가리킨다', async () => {
    const screen = {
      name: 'pitch_create',
      snapshot_id: 'ss-1',
      step: 'info',
      version_label: null,
      version_id: null,
      saved: null,
      draft: {
        title: '졸업 발표',
        slide_count: 12,
        script_chars: 1850,
        time_limit_sec: 300,
        criteria_text: null,
      },
      changed: ['time_limit_sec'],
    };
    const { run_id, conversation_id } = await accepted('제한 시간은 어디서 바꿔?', {
      page: { route: '/pitch/new', pitch_id: null, take_id: null, screen },
    });
    await settle(run_id);
    const answer = (await messages(conversation_id)).items.at(-1)!;
    expect(answer.evidence[0]).toMatchObject({
      origin: 'SCREEN',
      screen_snapshot_id: 'ss-1',
      revision: null,
      value: 300,
      saved: false,
    });
    expect(answer.claims.map((c) => c.kind)).toEqual(['RECORD', 'GUIDE']);
  });

  it('목 데이터가 홈 목과 맞는다 — 지난 대화는 Take 2 기준, 다시 평가는 점수가 있는 Take 2', async () => {
    const list = await call(chatPaths.conversations).then(json<ConversationList>);
    const old = (await messages(list.items[0]!.conversation_id)).items.at(-1)!;
    expect(old.basis_label).toMatch(/^Take 2 /);
    expect(old.evidence.map((e) => e.take_number).filter(Boolean)).toEqual([1, 2]);

    const { run_id, conversation_id } = await accepted('대본 보고 한 발표였어');
    const ask = askOf(await settle(run_id));
    await call(chatPaths.decision(ask.action_id), 'POST', {
      decision: 'APPROVE',
      args_hash: ask.args_hash,
    });
    await settle(run_id);
    const result = (await messages(conversation_id)).items.at(-1)!.cards[0] as Extract<
      Card,
      { type: 'REASSESS_RESULT' }
    >;
    expect(result.before.score).toBe(72);
  });
});

describe('계약 경계', () => {
  it('긴 말에 "봤지" 가 붙어도 판정 문장은 300자 안이고, quote 는 원문의 부분 문자열이다', async () => {
    const text = `${'가'.repeat(400)} 청중 많이 봤지?`;
    const { run_id, conversation_id } = await accepted(text);
    await settle(run_id);
    const answer = (await messages(conversation_id)).items.at(-1)!;
    expect([...answer.claims[0]!.text].length).toBeLessThanOrEqual(300);
    expect(text.includes(answer.assessments[0]!.quote)).toBe(true);
  });

  it('다시 묻기는 이 대화에 있는 메시지만 받는다 — 없거나 대화가 없으면 404', async () => {
    const { run_id, conversation_id } = await accepted('비교해 줘');
    await settle(run_id);
    const old = (await messages(conversation_id)).items.at(-1)!;
    expect(
      (await send(message('다시', { conversation_id, again_of_message_id: 'nope' }))).status,
    ).toBe(404);
    expect((await send(message('다시', { again_of_message_id: old.message_id }))).status).toBe(404);
    const before = (await call(chatPaths.conversations).then(json<ConversationList>)).items.length;
    expect(
      (await send(message('다시', { conversation_id, again_of_message_id: old.message_id })))
        .status,
    ).toBe(202);
    // 404 로 거른 요청은 빈 대화를 남기지 않는다
    expect((await call(chatPaths.conversations).then(json<ConversationList>)).items).toHaveLength(
      before,
    );
  });

  it('자동 목표 확인은 같은 목표에 한 번만 남는다', async () => {
    const { run_id, conversation_id } = await accepted('목표로 잡아 줘');
    const ask = askOf(await settle(run_id));
    await call(chatPaths.decision(ask.action_id), 'POST', {
      decision: 'APPROVE',
      args_hash: ask.args_hash,
    });
    await settle(run_id);
    controls.goalCheck();
    controls.goalCheck();
    const results = (await messages(conversation_id)).items.filter((m) =>
      m.cards.some((c) => c.type === 'GOAL_RESULT'),
    );
    expect(results).toHaveLength(1);
  });

  it('실행 오류가 붐빔이면 retry_after_s 5, 다른 오류면 null', async () => {
    controls.runFail('AGENT_BUSY');
    const busy = await accepted('비교해 줘');
    expect(await settle(busy.run_id)).toMatchObject({
      error: { code: 'AGENT_BUSY' },
      retry_after_s: 5,
    });
    controls.runFail('AGENT_TIMEOUT');
    const timeout = await accepted('비교해 줘');
    expect((await settle(timeout.run_id)).retry_after_s).toBeNull();
  });

  it('범위 바꾸기의 형식이 틀리면 422, 없는 피치면 404', async () => {
    const { run_id, conversation_id } = await accepted('비교해 줘');
    await settle(run_id);
    expect((await call(chatPaths.scope(conversation_id), 'PUT', { pitch_id: 1 })).status).toBe(422);
    expect((await call(chatPaths.scope(conversation_id), 'PUT', { pitch_id: 'nope' })).status).toBe(
      404,
    );
  });
});
