import { setupServer } from 'msw/node';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
vi.mock('./auth', () => ({ currentMockUserId: () => null }));
import { createChatHandlers, type MockChatControls } from './chat';
import { chatPaths } from '@/shared/api/chatPaths';
import type {
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
/** 형식은 맞지만 없는 ID — 없음(404)과 형식 오류(422)를 나눠 보려고 씁니다 */
const MISSING = '99999999-0000-4000-8000-000000000000';
const message = (text: string, extra: Record<string, unknown> = {}) => ({
  conversation_id: null,
  scope_pitch_id: null,
  text,
  page: { route: '/', pitch_id: null, take_id: null, screen: null },
  client_request_id: `00000000-0000-4000-8000-${String(++key).padStart(12, '0')}`,
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
/** 메시지 목록 — 비어(null) 올 수 있는 칸은 빈 목록으로 채워 읽기 쉽게. `page` 는 받은 그대로 */
async function messages(conversationId: string, query = '') {
  const page = await call(`${chatPaths.messages(conversationId)}${query}`).then(json<MessagePage>);
  const items = page.messages.map((msg) => ({
    ...msg,
    claims: msg.claims ?? [],
    cards: msg.cards ?? [],
    assessments: msg.assessments ?? [],
    evidence: msg.evidence ?? [],
    sources: msg.sources ?? [],
    plan: msg.plan ?? [],
    trace: msg.trace ?? [],
  }));
  return { page, items };
}
type MessageItem = Awaited<ReturnType<typeof messages>>['items'][number];
const conversations = (query = '') =>
  call(`${chatPaths.conversations}${query}`).then(json<ConversationList>);
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
    const claims = events.flatMap((e) => (e.event === 'claim' && e.data ? [e.data] : []));
    expect(claims.map((c) => c.index)).toEqual([0, 1, 2, 3]);
    // 일반 조언 묶음 바로 앞에는 고정 안내가 붙습니다(7-10)
    expect(claims.map((c) => c.claim.kind)).toEqual(['RECORD', 'GUIDE', 'SYSTEM', 'GENERAL']);
    expect(claims[2]!.claim.text).toBe('자료에서 확인한 내용은 아니고 일반적인 조언이에요.');
    // 계산 근거는 입력 근거와 함께 온다
    const computed = claims[0]!.evidence.find((e) => e.origin === 'COMPUTED')!;
    expect(
      computed.inputs!.every((id) => claims[0]!.evidence.some((e) => e.evidence_id === id)),
    ).toBe(true);
    expect(claims[3]!.claim.evidence_ids).toEqual([]);

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

  it('붐빔 · 하루 한도는 429 와 다음 자정까지의 Retry-After 를 준다', async () => {
    // 보내기의 AGENT_BUSY 는 BE 에서 서비스 전체 하루 상한 — 5초가 아니라 자정까지입니다
    controls.fail('AGENT_BUSY');
    const busy = await send(message('비교해 줘'));
    const busyWait = Number(busy.headers.get('Retry-After'));
    expect(busy.status).toBe(429);
    expect(busyWait > 0 && busyWait <= 86_400).toBe(true);
    controls.fail('AGENT_DAILY_LIMIT');
    const limit = await send(message('비교해 줘'));
    const wait = Number(limit.headers.get('Retry-After'));
    expect(limit.status).toBe(429);
    expect(wait > 0 && wait <= 86_400).toBe(true);
    // 걸어 둔 실패는 한 번만
    expect((await send(message('비교해 줘'))).status).toBe(202);
  });

  it('받은 문장 없이 실패하면 FAILED — 답 메시지는 남지 않는다', async () => {
    controls.runFail('AGENT_TIMEOUT');
    const { run_id, conversation_id } = await accepted('비교해 줘');
    const view = await settle(run_id);
    expect(view).toMatchObject({ status: 'FAILED', error: { code: 'AGENT_TIMEOUT' }, events: [] });
    expect((await messages(conversation_id)).items.map((m) => m.role)).toEqual(['user']);
  });

  it('사용자 말에는 그 말이 시작한 실행(run_id)을 단다 — BE accept.py 처럼', async () => {
    const { run_id, conversation_id } = await accepted('비교해 줘');
    await settle(run_id);
    const { items } = await messages(conversation_id);
    expect(items.filter((m) => m.role !== 'system').map((m) => [m.role, m.run_id])).toEqual([
      ['user', run_id],
      ['assistant', run_id],
    ]);
  });

  it('받은 문장이 있으면 PARTIAL 로 남긴다 (12-3 "앞서 받은 답이 있으면 그것으로 PARTIAL")', async () => {
    controls.partial('AGENT_TIMEOUT');
    const { run_id, conversation_id } = await accepted('비교해 줘');
    expect(await settle(run_id)).toMatchObject({
      status: 'PARTIAL',
      error: { code: 'AGENT_TIMEOUT' },
    });
    const answer = (await messages(conversation_id)).items.at(-1)!;
    expect([answer.status, answer.claims.length]).toEqual(['PARTIAL', 1]);
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

  it('[취소]는 BE 처럼 오류 코드 AGENT_CANCELLED 만 남기고 답 메시지를 만들지 않는다', async () => {
    const { run_id, conversation_id } = await accepted('비교해 줘');
    await poll(run_id);
    await poll(run_id);
    const cancelled = await call(chatPaths.cancel(run_id), 'POST').then(json<RunView>);
    expect(cancelled.error).toEqual({ code: 'AGENT_CANCELLED' });
    expect((await messages(conversation_id)).items.map((m) => m.role)).toEqual(['user']);
  });
});

describe('승인 카드', () => {
  it('승인 대기에서 [승인]하면 목표를 저장하고, 같은 결정은 처음 결과를 준다', async () => {
    const { run_id, conversation_id } = await accepted('3장 시선을 목표로 잡아 줘');
    const waiting = await settle(run_id);
    expect(waiting.status).toBe('WAITING_APPROVAL');
    const ask = askOf(waiting);
    expect(ask).toMatchObject({ action_type: 'SAVE_GOAL' });

    const wrong = await call(chatPaths.decision(run_id, ask.action_id), 'POST', {
      decision: 'APPROVE',
      args_hash: 'x',
    });
    expect(wrong.status).toBe(409);

    const body = { decision: 'APPROVE', args_hash: ask.args_hash };
    // BE 처럼 이벤트 없이 실행 상태만 — 승인이면 RUNNING. 같은 결정은 그때의 상태를 줍니다
    const first = await call(chatPaths.decision(run_id, ask.action_id), 'POST', body).then(
      json<RunView>,
    );
    expect(first).toMatchObject({ run_id, status: 'RUNNING', events: [], next_after_seq: null });
    const again = await call(chatPaths.decision(run_id, ask.action_id), 'POST', body).then(
      json<RunView>,
    );
    expect(again).toEqual(first);
    // 다른 실행의 경로로는 그 카드를 찾지 못합니다
    const other = await accepted('비교해 줘');
    expect((await call(chatPaths.decision(other.run_id, ask.action_id), 'POST', body)).status).toBe(
      404,
    );
    expect(conversation_id).not.toBe(other.conversation_id);

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
    const r = await call(chatPaths.decision(run_id, ask.action_id), 'POST', {
      decision: 'REJECT',
      args_hash: ask.args_hash,
    });
    expect(await r.json()).toMatchObject({ run_id, status: 'DONE', events: [], error: null });
    // BE `close_waiting` 처럼 — 고정 문장은 메시지에만, 카드는 비우고, 거절이면 메시지 상태 DONE
    const answer = (await messages(conversation_id)).items.at(-1)!;
    expect(answer.claims.at(-1)!.text).toBe('목표를 저장하지 않았어요.');
    expect([answer.status, answer.cards]).toEqual(['DONE', []]);
    const events = (await poll(run_id)).events;
    expect(
      events.some((e) => e.event === 'claim' && e.data?.claim.text === '목표를 저장하지 않았어요.'),
    ).toBe(false);
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
    const late = await call(chatPaths.decision(run_id, ask.action_id), 'POST', {
      decision: 'APPROVE',
      args_hash: ask.args_hash,
    });
    expect(await late.json()).toMatchObject({ code: 'AGENT_CANCELLED' });
  });

  it('만료되면 만료 문장으로 끝나고 그 뒤 승인은 AGENT_EXPIRED', async () => {
    const { run_id } = await accepted('목표로 잡아 줘');
    const ask = askOf(await settle(run_id));
    controls.expire();
    const expired = await poll(run_id);
    expect([expired.status, expired.error]).toEqual(['EXPIRED', { code: 'AGENT_EXPIRED' }]);
    const late = await call(chatPaths.decision(run_id, ask.action_id), 'POST', {
      decision: 'APPROVE',
      args_hash: ask.args_hash,
    });
    expect(await late.json()).toMatchObject({ code: 'AGENT_EXPIRED' });
  });

  it('만료 · 승인 대기 중 [취소]는 BE 처럼 메시지 상태 PARTIAL · 카드 없음 · 행동별 문장', async () => {
    const expired = await accepted('목표로 잡아 줘');
    await settle(expired.run_id);
    controls.expire();
    const afterExpire = (await messages(expired.conversation_id)).items.at(-1)!;
    expect([afterExpire.status, afterExpire.cards, afterExpire.claims.at(-1)!.text]).toEqual([
      'PARTIAL',
      [],
      '확인 시간이 지나 실행하지 않았어요. 필요하면 다시 말해 주세요.',
    ]);

    const waiting = await accepted('목표로 잡아 줘');
    await settle(waiting.run_id);
    const cancelled = await call(chatPaths.cancel(waiting.run_id), 'POST').then(json<RunView>);
    expect([cancelled.status, cancelled.error, cancelled.events]).toEqual([
      'CANCELLED',
      { code: 'AGENT_CANCELLED' },
      [],
    ]);
    const afterCancel = (await messages(waiting.conversation_id)).items.at(-1)!;
    expect([afterCancel.status, afterCancel.claims.at(-1)!.text]).toEqual([
      'PARTIAL',
      '목표를 저장하지 않았어요.',
    ]);
  });

  it('조회는 다음 커서를 늘 주고, 남은 이벤트가 없으면 has_more 는 false', async () => {
    const { run_id } = await accepted('비교해 줘');
    const view = await poll(run_id);
    expect(view.has_more).toBe(false);
    expect(view.next_after_seq).toBe(view.events.at(-1)?.event_seq ?? 0);
    const later = await poll(run_id, 99);
    expect([later.events, later.next_after_seq]).toEqual([[], 99]);
  });

  it('[되돌리기]는 처음 결과를 다시 주고 대화에 시스템 메시지를 남긴다', async () => {
    const { run_id, conversation_id } = await accepted('목표로 잡아 줘');
    const ask = askOf(await settle(run_id));
    await call(chatPaths.decision(run_id, ask.action_id), 'POST', {
      decision: 'APPROVE',
      args_hash: ask.args_hash,
    });
    await settle(run_id);
    const saved = (await messages(conversation_id)).items.at(-1)!.cards[0] as Extract<
      Card,
      { type: 'GOAL_SAVED' }
    >;

    const first = await call(chatPaths.undo(run_id, saved.action_id), 'POST').then(json);
    expect(await call(chatPaths.undo(run_id, saved.action_id), 'POST').then(json)).toEqual(first);
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
      pitch_id: null,
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
    expect(list.conversations).toHaveLength(1);
    const old = await messages(list.conversations[0]!.conversation_id);
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
    const r = await call(chatPaths.scope(conversation_id), 'PATCH', { pitch_id: null });
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
    await call(chatPaths.decision(run_id, ask.action_id), 'POST', {
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
      message('골라 줘', { conversation_id, choice: { proposal_id: MISSING, option: 'EXPERT' } }),
    );
    expect(unknown.status).toBe(404);
    const orphan = await send(
      message('골라 줘', { choice: { proposal_id: MISSING, option: 'USER' } }),
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
    const pitchId = list.conversations.find((c) => c.scope_pitch_id)!.scope_pitch_id!;
    const changed = await call(chatPaths.scope(conversation_id), 'PATCH', { pitch_id: pitchId });
    expect(await changed.json()).toMatchObject({ scope_pitch_id: pitchId });
    const filtered = await call(`${chatPaths.conversations}?pitch_id=${pitchId}`).then(
      json<ConversationList>,
    );
    expect(filtered.conversations.map((c) => c.conversation_id)).toContain(conversation_id);
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
    expect((await send(message('비교해 줘', { scope_pitch_id: MISSING }))).status).toBe(404);
    const created = await call(chatPaths.conversations, 'POST', { pitch_id: MISSING });
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
    await call(chatPaths.decision(run_id, ask.action_id), 'POST', {
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
    const old = (await messages(list.conversations[0]!.conversation_id)).items.at(-1)!;
    expect(old.basis?.pitches[0]!.takes[0]!.take_number).toBe(2);
    expect(old.evidence.map((e) => e.take_number).filter(Boolean)).toEqual([1, 2]);

    const { run_id, conversation_id } = await accepted('대본 보고 한 발표였어');
    const ask = askOf(await settle(run_id));
    await call(chatPaths.decision(run_id, ask.action_id), 'POST', {
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
      (await send(message('다시', { conversation_id, again_of_message_id: MISSING }))).status,
    ).toBe(404);
    expect((await send(message('다시', { again_of_message_id: old.message_id }))).status).toBe(404);
    const before = (await call(chatPaths.conversations).then(json<ConversationList>)).conversations
      .length;
    expect(
      (await send(message('다시', { conversation_id, again_of_message_id: old.message_id })))
        .status,
    ).toBe(202);
    // 404 로 거른 요청은 빈 대화를 남기지 않는다
    expect(
      (await call(chatPaths.conversations).then(json<ConversationList>)).conversations,
    ).toHaveLength(before);
  });

  it('자동 목표 확인은 같은 목표에 한 번만 남는다', async () => {
    const { run_id, conversation_id } = await accepted('목표로 잡아 줘');
    const ask = askOf(await settle(run_id));
    await call(chatPaths.decision(run_id, ask.action_id), 'POST', {
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
    expect((await call(chatPaths.scope(conversation_id), 'PATCH', { pitch_id: 1 })).status).toBe(
      422,
    );
    expect(
      (await call(chatPaths.scope(conversation_id), 'PATCH', { pitch_id: MISSING })).status,
    ).toBe(404);
    // BE 처럼 — UUID 가 아니거나 pitch_id 가 빠졌거나 모르는 칸이 있으면 저장 전 422
    for (const body of [{ pitch_id: 'nope' }, {}, { pitch_id: null, title: 'x' }])
      expect((await call(chatPaths.scope(conversation_id), 'PATCH', body)).status).toBe(422);
  });
});

describe('독립 리뷰 반영', () => {
  async function approveGoal(conversationId: string | null = null) {
    const { run_id, conversation_id } = await accepted('목표로 잡아 줘', {
      conversation_id: conversationId,
    });
    const ask = askOf(await settle(run_id));
    await call(chatPaths.decision(run_id, ask.action_id), 'POST', {
      decision: 'APPROVE',
      args_hash: ask.args_hash,
    });
    await settle(run_id);
    const answer = (await messages(conversation_id)).items.at(-1)!;
    return { conversation_id, run_id, ask, answer };
  }
  const savedOf = (answer: MessageItem) =>
    answer.cards.find((c) => c.type === 'GOAL_SAVED') as Extract<Card, { type: 'GOAL_SAVED' }>;

  it('목표 판 번호는 대화마다 0 에서 시작하고, 다른 대화의 저장이 되돌리기를 막지 않는다', async () => {
    const a = await approveGoal();
    const b = await approveGoal();
    expect([savedOf(a.answer).goal.version, savedOf(b.answer).goal.version]).toEqual([1, 1]);
    const undo = await call(chatPaths.undo(a.run_id, savedOf(a.answer).action_id), 'POST');
    // BE #935 `GoalUndoneResponse` — 처음 저장을 되돌렸으니 목표 내용은 null
    expect(await undo.json()).toEqual({
      run_id: a.run_id,
      action_id: savedOf(a.answer).action_id,
      goal_version: 2,
      goal: null,
    });
    // 다른 실행의 경로로는 그 행동을 찾지 못합니다
    expect((await call(chatPaths.undo(b.run_id, savedOf(a.answer).action_id), 'POST')).status).toBe(
      404,
    );
  });

  it('없는 행동의 되돌리기는 404, 같은 대화에서 다른 목표로 바뀐 뒤면 409', async () => {
    expect((await call(chatPaths.undo('nope', 'nope'), 'POST')).status).toBe(404);
    const first = await approveGoal();
    await approveGoal(first.conversation_id);
    const r = await call(chatPaths.undo(first.run_id, savedOf(first.answer).action_id), 'POST');
    expect([r.status, (await r.json()).code]).toEqual([409, 'AGENT_STATE_INVALID']);
  });

  it('행동 성공 문장은 그 행동의 action_id 를 싣는다 — 승인 · 고르기 모두', async () => {
    const { ask, answer } = await approveGoal();
    const success = answer.claims.find((c) => c.kind === 'ACTION_RESULT')!;
    expect([success.action_id, savedOf(answer).action_id]).toEqual([ask.action_id, ask.action_id]);

    const { run_id, conversation_id } = await accepted('목표 제안해 줘');
    await settle(run_id);
    const proposal = (await messages(conversation_id)).items.at(-1)!.cards[0] as Extract<
      Card,
      { type: 'GOAL_PROPOSAL' }
    >;
    const chosen = await accepted('추천 안으로', {
      conversation_id,
      choice: { proposal_id: proposal.proposal_id, option: 'EXPERT' },
    });
    await settle(chosen.run_id);
    const picked = (await messages(conversation_id)).items.at(-1)!;
    const claim = picked.claims.find((c) => c.kind === 'ACTION_RESULT')!;
    expect(claim.action_id).toBe(savedOf(picked).action_id);
    expect(claim.action_id).not.toBeNull();
  });

  it('사용자 안을 고르면 카드 이유 · 자동 확인이 그 안(전체 장)으로 말한다', async () => {
    const { run_id, conversation_id } = await accepted('목표 제안해 줘');
    await settle(run_id);
    const proposal = (await messages(conversation_id)).items.at(-1)!.cards[0] as Extract<
      Card,
      { type: 'GOAL_PROPOSAL' }
    >;
    const user = proposal.options.find((o) => o.label === 'USER')!;
    const chosen = await accepted('내 안으로', {
      conversation_id,
      choice: { proposal_id: proposal.proposal_id, option: 'USER' },
    });
    await settle(chosen.run_id);
    const saved = savedOf((await messages(conversation_id)).items.at(-1)!);
    expect([saved.reason, saved.evidence_ids]).toEqual([user.reason, user.evidence_ids]);
    controls.goalCheck();
    const check = (await messages(conversation_id)).items.at(-1)!;
    expect(check.claims[0]!.text.startsWith('전체 장')).toBe(true);
    // 목표 판정 문장은 계산 근거를 가리키고, 그 근거는 같은 메시지에 있다
    const ids = check.evidence.map((e) => e.evidence_id);
    expect(check.claims[0]!.evidence_ids.every((id) => ids.includes(id))).toBe(true);
    expect(check.evidence.find((e) => e.origin === 'COMPUTED')!.inputs).not.toBeNull();
  });

  it('새 피치(저장값 없음)의 화면 값은 저장된 값이 아니다', async () => {
    const screen = {
      name: 'pitch_create',
      snapshot_id: 'ss-2',
      step: 'info',
      version_label: null,
      version_id: null,
      saved: null,
      draft: {
        title: null,
        slide_count: null,
        script_chars: null,
        time_limit_sec: 600,
        criteria_text: null,
      },
      changed: [],
    };
    const { run_id, conversation_id } = await accepted('제한 시간 어디서 바꿔?', {
      page: { route: '/pitch/new', pitch_id: null, take_id: null, screen },
    });
    await settle(run_id);
    const answer = (await messages(conversation_id)).items.at(-1)!;
    expect(answer.evidence[0]).toMatchObject({ origin: 'SCREEN', saved: false });
  });

  it('꺼지면 대화 API 가 503 AGENT_DISABLED, 켜면 다시 받는다', async () => {
    controls.disable();
    const r = await send(message('비교해 줘'));
    expect([r.status, (await r.json()).code]).toEqual([503, 'AGENT_DISABLED']);
    expect((await call(chatPaths.conversations)).status).toBe(503);
    controls.enable();
    expect((await send(message('비교해 줘'))).status).toBe(202);
  });

  it('fail 은 코드마다 문서의 HTTP 상태로 거절한다', async () => {
    controls.fail('NOT_FOUND');
    expect((await send(message('비교해 줘'))).status).toBe(404);
    controls.fail('AGENT_DISABLED');
    expect((await send(message('비교해 줘'))).status).toBe(503);
  });

  it('끝난 답의 할 일은 모두 끝이고, 자료 문장 앞에 자료 찾기 기록이 있다', async () => {
    for (const text of ['비교해 줘', '바로 발표할래', '짧게 설명해 줘, 기억해']) {
      const { run_id, conversation_id } = await accepted(text);
      await settle(run_id);
      const answer = (await messages(conversation_id)).items.at(-1)!;
      expect(answer.plan.every((p) => p.status === 'DONE')).toBe(true);
      const kinds = answer.claims.map((c) => c.kind);
      if (kinds.includes('GUIDE') || kinds.includes('GENERAL'))
        expect(answer.trace.some((t) => t.tool === 'search_guides')).toBe(true);
    }
  });

  it('근거 배지는 Take 기록을 읽은 답에만 붙는다', async () => {
    const refused = await accepted('오늘 날씨 어때?');
    await settle(refused.run_id);
    expect((await messages(refused.conversation_id)).items.at(-1)!.basis).toBeNull();
    const compared = await accepted('비교해 줘');
    await settle(compared.run_id);
    // 근거 표시는 글자가 아니라 읽은 피치 · Take 와 버전 (6-8 `basis`) — 배지 글자는 화면이 만듭니다
    expect((await messages(compared.conversation_id)).items.at(-1)!.basis).toMatchObject({
      pitches: [{ standard_version: 4, takes: [{ take_number: 3 }] }],
    });
  });

  it('ID 칸의 형식이 틀리면 저장 전 422 — 숫자도, UUID 가 아닌 글자도', async () => {
    expect((await send(message('비교해 줘', { conversation_id: 123 }))).status).toBe(422);
    expect((await send(message('비교해 줘', { scope_pitch_id: 42 }))).status).toBe(422);
    for (const id of ['nope', '1234', `${MISSING}x`]) {
      expect((await send(message('비교해 줘', { scope_pitch_id: id }))).status).toBe(422);
      expect((await send(message('비교해 줘', { conversation_id: id }))).status).toBe(422);
      expect((await send(message('비교해 줘', { again_of_message_id: id }))).status).toBe(422);
    }
    const goodPage = message('').page;
    for (const page of [
      { ...goodPage, take_id: 'abc' },
      { ...goodPage, pitch_id: 'abc' },
      { ...goodPage, route: '' },
      { ...goodPage, route: '/'.repeat(65) },
      { ...goodPage, extra: 1 },
    ])
      expect((await send(message('비교해 줘', { page }))).status).toBe(422);
    expect(
      (await send(message('골라 줘', { choice: { proposal_id: 'gp-1', option: 'USER' } }))).status,
    ).toBe(422);
  });

  it('BE 처럼 모르는 칸 · 공백을 더해 2,000자를 넘는 글은 422', async () => {
    expect((await send(message('비교해 줘', { memory_off: true }))).status).toBe(422);
    expect((await send(message(` ${'가'.repeat(2000)}`))).status).toBe(422);
    expect((await send(message('가'.repeat(2000)))).status).toBe(202);
    expect((await call(chatPaths.conversations, 'POST', { pitch_id: 'nope' })).status).toBe(422);
  });
});

describe('AI 코드(schemas.py)와 같은 모양', () => {
  const screenOf = (name: 'pitch_create' | 'pitch_edit') => ({
    name,
    snapshot_id: `ss-${name}`,
    step: 'info',
    version_label: null,
    version_id: null,
    saved: null,
    draft: {
      title: null,
      slide_count: null,
      script_chars: null,
      time_limit_sec: 300,
      criteria_text: null,
    },
    changed: [],
  });

  it('새 피치 화면의 근거는 pitch_id 가 null, 피치 수정 화면은 그 피치', async () => {
    const pitchIds = [];
    for (const name of ['pitch_create', 'pitch_edit'] as const) {
      const { run_id, conversation_id } = await accepted('제한 시간 어디서 바꿔?', {
        page: { route: '/', pitch_id: null, take_id: null, screen: screenOf(name) },
      });
      await settle(run_id);
      const answer = (await messages(conversation_id)).items.at(-1)!;
      pitchIds.push(answer.evidence.find((e) => e.origin === 'SCREEN')!.pitch_id);
    }
    expect(pitchIds[0]).toBeNull();
    expect(typeof pitchIds[1]).toBe('string');
  });

  it('잴 수 없는 사용자 안은 goal · content_hash 가 null 이고 고를 수 없다', async () => {
    const { run_id, conversation_id } = await accepted('자신감 있게 하는 걸 목표로 제안해 줘');
    await settle(run_id);
    const proposal = (await messages(conversation_id)).items.at(-1)!.cards[0] as Extract<
      Card,
      { type: 'GOAL_PROPOSAL' }
    >;
    const user = proposal.options.find((o) => o.label === 'USER')!;
    expect([user.goal, user.content_hash]).toEqual([null, null]);
    const pick = (option: 'USER' | 'EXPERT') =>
      send(
        message('골라 줘', {
          conversation_id,
          choice: { proposal_id: proposal.proposal_id, option },
        }),
      );
    expect((await pick('USER')).status).toBe(422);
    expect((await pick('EXPERT')).status).toBe(202);
  });

  it('다시 평가 결과의 change 는 객체, criteria_results 는 배열', async () => {
    const { run_id, conversation_id } = await accepted('대본 보고 한 발표였어');
    const ask = askOf(await settle(run_id));
    await call(chatPaths.decision(run_id, ask.action_id), 'POST', {
      decision: 'APPROVE',
      args_hash: ask.args_hash,
    });
    await settle(run_id);
    const result = (await messages(conversation_id)).items.at(-1)!.cards[0] as Extract<
      Card,
      { type: 'REASSESS_RESULT' }
    >;
    expect(result.change).toEqual({ script_used: true });
    expect([
      Array.isArray(result.before.criteria_results),
      Array.isArray(result.after.criteria_results),
    ]).toEqual([true, true]);
  });
});

describe('대화의 실행 조회', () => {
  it('마지막 실행을 끝났어도 돌려주고(BE `latest_run_status`), 실행을 나아가게 하지 않는다', async () => {
    const { run_id, conversation_id } = await accepted('비교해 줘');
    const lookup = () =>
      call(chatPaths.conversationRun(conversation_id)).then(json<RunView | null>);
    expect(await lookup()).toMatchObject({ run_id, conversation_id, status: 'QUEUED' });
    expect((await lookup())?.status).toBe('QUEUED');
    await settle(run_id);
    expect(await lookup()).toMatchObject({ run_id, status: 'DONE' });
    expect((await call(chatPaths.conversationRun('nope'))).status).toBe(404);
  });

  it('실행이 한 번도 없던 대화는 null', async () => {
    const created = await call(chatPaths.conversations, 'POST', { pitch_id: null }).then(
      json<{ conversation_id: string }>,
    );
    const r = await call(chatPaths.conversationRun(created.conversation_id));
    expect([r.status, await r.json()]).toEqual([200, null]);
  });
});

describe('BE #933 모양', () => {
  it('새 대화는 201 과 대화 하나 — 본문은 `pitch_id` 만(목은 `memory_off` 도) 받는다', async () => {
    const r = await call(chatPaths.conversations, 'POST', { pitch_id: null });
    expect(r.status).toBe(201);
    const created = (await r.json()) as Record<string, unknown>;
    expect(Object.keys(created).sort()).toEqual(
      [
        'conversation_id',
        'created_at',
        'last_user_activity_at',
        'scope_pitch_id',
        'scope_pitch_title',
        'title',
      ].sort(),
    );
    expect((await call(chatPaths.conversations, 'POST', { pitch_id: 42 })).status).toBe(422);
    expect(
      (await call(chatPaths.conversations, 'POST', { pitch_id: null, memory_off: 'yes' })).status,
    ).toBe(422);
  });

  it('대화 목록은 `conversations` 에, 최근 사용자 활동순', async () => {
    const { run_id } = await accepted('비교해 줘');
    await settle(run_id);
    const list = await conversations();
    expect(list.conversations).toHaveLength(2);
    const times = list.conversations.map((c) => c.last_user_activity_at);
    expect([...times].sort().reverse()).toEqual(times);
  });

  it('범위 바꾸기는 PATCH 이고 바뀐 대화를 돌려준다', async () => {
    const { run_id, conversation_id } = await accepted('비교해 줘');
    await settle(run_id);
    const pitchId = (await conversations()).conversations.find(
      (c) => c.scope_pitch_id,
    )!.scope_pitch_id!;
    const r = await call(chatPaths.scope(conversation_id), 'PATCH', { pitch_id: pitchId });
    expect(r.status).toBe(200);
    expect(await r.json()).toMatchObject({
      conversation_id,
      scope_pitch_id: pitchId,
      scope_pitch_title: expect.any(String),
    });
  });

  it('메시지 목록은 최근부터 한 페이지 — 순번 · `next_before_seq` 로 앞을 읽는다', async () => {
    const { run_id, conversation_id } = await accepted('비교해 줘');
    await settle(run_id);
    const all = (await messages(conversation_id)).page;
    expect(all.next_before_seq).toBeNull();
    const seqs = all.messages.map((m) => m.seq);
    expect(seqs).toEqual(seqs.map((_, i) => i + 1));

    const last = await messages(conversation_id, '?limit=1');
    expect(last.page.messages.map((m) => m.seq)).toEqual([seqs.at(-1)]);
    expect(last.page.next_before_seq).toBe(seqs.at(-1));
    const earlier = await messages(conversation_id, `?limit=1&before_seq=${seqs.at(-1)}`);
    expect(earlier.page.messages.map((m) => m.seq)).toEqual([seqs.at(-2)]);
    expect((await call(`${chatPaths.messages(conversation_id)}?limit=0`)).status).toBe(422);
  });

  it('사용자 메시지의 답 칸은 null, 가림 표시는 false', async () => {
    const { run_id, conversation_id } = await accepted('비교해 줘');
    await settle(run_id);
    const user = (await messages(conversation_id)).page.messages.find((m) => m.role === 'user')!;
    expect(user).toMatchObject({
      claims: null,
      cards: null,
      assessments: null,
      basis: null,
      masked: false,
      again_of_message_id: null,
    });
  });

  it('보내기 키가 UUID 가 아니면 저장 전 422', async () => {
    expect((await send(message('비교해 줘', { client_request_id: 'req-1' }))).status).toBe(422);
  });
});
// ── 1~5단계 최종 점검 반영 — 목을 BE a6264abc 에 맞춤 ───────────────

describe('목이 BE 와 같게', () => {
  it('문장을 낸 뒤 실패하면 BE `turn.fail` 처럼 PARTIAL 로 끝나고 메시지 목록에 그 문장이 남는다', async () => {
    controls.partial('AGENT_TIMEOUT');
    const { run_id, conversation_id } = await accepted('비교해 줘');
    const view = await settle(run_id);
    expect([view.status, view.error]).toEqual(['PARTIAL', { code: 'AGENT_TIMEOUT' }]);
    const events = (await poll(run_id)).events.filter((e) => e.event === 'claim');
    expect(events.length).toBeGreaterThan(0);
    expect((await messages(conversation_id)).items.map((m) => [m.role, m.status])).toEqual([
      ['user', null],
      ['assistant', 'PARTIAL'],
    ]);
  });

  it('사용자 메시지에도 그 말이 시작한 실행 ID 가 있다', async () => {
    const { run_id, conversation_id } = await accepted('비교해 줘');
    await settle(run_id);
    const items = (await messages(conversation_id)).items;
    expect(items.map((m) => [m.role, m.run_id])).toEqual([
      ['user', run_id],
      ['assistant', run_id],
    ]);
  });

  it('메시지 목록 limit 은 1~50, before_seq 는 1 이상 정수 — 아니면 422', async () => {
    const { conversation_id } = await accepted('비교해 줘');
    for (const query of ['?limit=51', '?limit=0', '?before_seq=1.5', '?before_seq=0'])
      expect((await call(`${chatPaths.messages(conversation_id)}${query}`)).status).toBe(422);
    expect((await call(`${chatPaths.messages(conversation_id)}?limit=50`)).status).toBe(200);
  });

  it('대화의 실행 조회는 after_seq 뒤의 이벤트만 준다', async () => {
    const { run_id, conversation_id } = await accepted('비교해 줘');
    await settle(run_id);
    const view = await call(
      `${chatPaths.conversationRun(conversation_id)}?after_seq=2147483647`,
    ).then(json<RunView>);
    expect([view.events, view.next_after_seq]).toEqual([[], 2147483647]);
  });

  it('대화 제목은 공백을 하나로 줄이고 40자를 넘으면 39자 + …', async () => {
    const long = `가   나 ${'다'.repeat(60)}`;
    const { conversation_id } = await accepted(long);
    const title = (await conversations()).conversations.find(
      (c) => c.conversation_id === conversation_id,
    )!.title;
    expect([...title]).toHaveLength(40);
    expect(title.startsWith('가 나 ')).toBe(true);
    expect(title.endsWith('…')).toBe(true);
  });

  it('범위 바꾸기는 본문 검사(422)가 대화 찾기(404)보다 먼저다', async () => {
    expect((await call(chatPaths.scope(MISSING), 'PATCH', { pitch_id: 'nope' })).status).toBe(422);
    expect((await call(chatPaths.scope(MISSING), 'PATCH', { pitch_id: null })).status).toBe(404);
  });

  it('결정 경로의 action_id 는 64자까지', async () => {
    const { run_id } = await accepted('목표로 잡아 줘');
    const r = await call(chatPaths.decision(run_id, 'a'.repeat(65)), 'POST', {
      decision: 'APPROVE',
      args_hash: 'h',
    });
    expect(r.status).toBe(422);
  });

  it('승인 대기를 끝낸 메시지에는 근거 표시가 없다', async () => {
    const { run_id, conversation_id } = await accepted('목표로 잡아 줘');
    const ask = askOf(await settle(run_id));
    await call(chatPaths.decision(run_id, ask.action_id), 'POST', {
      decision: 'REJECT',
      args_hash: ask.args_hash,
    });
    expect((await messages(conversation_id)).items.at(-1)!.basis).toBeNull();
  });
});
