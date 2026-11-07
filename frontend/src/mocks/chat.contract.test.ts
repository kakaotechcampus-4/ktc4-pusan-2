import { setupServer } from 'msw/node';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
vi.mock('./auth', () => ({ currentMockUserId: () => null }));
import { createChatHandlers, type MockChatControls } from './chat';
import { chatPaths } from '@/shared/api/chatPaths';

/**
 * 계약 시험 — 목이 **실제 BE** 와 같은 칸 · 값 · 상태 코드로 답하는지 봅니다(문서가 아니라 BE 코드 기준).
 *
 * 기준: develop-next `37e3a9df` — BE #933(메시지 · 실행 · 승인 · 대화 목록) + #935(되돌리기) + #1002(기억 · '기억 없이').
 * (#933 브랜치의 옛 기준 `55aa10d0` 는 브랜치가 다시 쓰여 저장소에 남아 있지 않습니다)
 * - 응답 칸: `backend/src/pitch_coach_backend/module/chat/api_dto.py`
 * - 경로 · 상태 코드: `module/chat/controller.py` (새 대화 201 · 메시지 접수 202 · 나머지 200)
 * - 오류 코드: `module/chat/exception.py`, 오류 본문: `core/exceptions.py` `{code, message, detail}`
 *
 * BE 가 바뀌면 이 표부터 그 커밋에 맞춰 고치고, 그다음 목 · 타입을 맞춥니다. 시험이 깨지면 목이 BE 와 어긋난 것입니다.
 * **한계**: 이 표는 FE 쪽 상수라 BE 코드가 바뀌어도 저절로 깨지지 않습니다 — BE 기준 커밋을 올릴 때 사람이 맞춥니다.
 * BE 의 OpenAPI 를 파일로 떠 두고 이 표를 거기서 읽는 방식을 BE 와 정할 예정입니다(#957 리뷰).
 */
const BE = {
  commit: '37e3a9df',
  ConversationResponse: [
    'conversation_id',
    'title',
    'scope_pitch_id',
    'scope_pitch_title',
    'last_user_activity_at',
    'created_at',
    'memory_off',
  ],
  ConversationListItem: [
    'conversation_id',
    'title',
    'scope_pitch_id',
    'scope_pitch_title',
    'last_user_activity_at',
    'created_at',
    'memory_off',
    'stale',
  ],
  ConversationListResponse: ['conversations'],
  /** 요청 본문 — 모르는 칸은 422(extra="forbid") */
  CreateConversationRequest: ['pitch_id', 'memory_off'],
  GoalUndoneResponse: ['run_id', 'action_id', 'goal_version', 'goal'],
  MemorySettingRequest: ['enabled'],
  MemorySettingResponse: ['enabled', 'items'],
  MemoryItemResponse: ['item_id', 'key', 'value'],
  /** `module/chat/dto.py` `ProfileKey` */
  ProfileKey: ['TONE', 'EXPLAIN_STYLE', 'FOCUS_AREA', 'CONTEXT'],
  MessageAcceptedResponse: ['conversation_id', 'message_id', 'run_id', 'status'],
  RunStatusResponse: [
    'run_id',
    'conversation_id',
    'status',
    'plan',
    'trace',
    'events',
    'next_after_seq',
    'has_more',
    'cards',
    'error',
    'retry_after_s',
  ],
  RunEventDTO: ['event_seq', 'event', 'data', 'masked'],
  RunErrorDTO: ['code'],
  MessageListResponse: ['messages', 'next_before_seq'],
  ChatMessageResponse: [
    'message_id',
    'seq',
    'role',
    'text',
    'status',
    'run_id',
    'again_of_message_id',
    'created_at',
    'claims',
    'cards',
    'assessments',
    'evidence',
    'sources',
    'basis',
    'plan',
    'trace',
    'masked',
    'stale_reasons',
  ],
  AvailabilityResponse: ['enabled'],
  ErrorBody: ['code', 'message', 'detail'],
  RunStatus: [
    'QUEUED',
    'RUNNING',
    'WAITING_APPROVAL',
    'DONE',
    'PARTIAL',
    'REFUSED',
    'FAILED',
    'CANCELLED',
    'EXPIRED',
  ],
  RunEvent: ['plan', 'tool_done', 'claim'],
  Role: ['user', 'assistant', 'system'],
} as const;

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

const call = (path: string, method = 'GET', body?: unknown) =>
  fetch(`http://localhost${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

type Json = Record<string, unknown>;
const read = async (response: Response) => (await response.json()) as Json;

/** 칸 이름이 BE 와 정확히 같은지 — 빠진 칸 · 더 있는 칸을 함께 보여 줍니다 */
const keysOf = (value: unknown) => Object.keys(value as Json).sort();
const expectShape = (value: unknown, keys: readonly string[]) =>
  expect(keysOf(value)).toEqual([...keys].sort());

let key = 0;
const message = (text: string, extra: Json = {}) => ({
  conversation_id: null,
  scope_pitch_id: null,
  text,
  page: { route: '/', pitch_id: null, take_id: null, screen: null },
  client_request_id: `00000000-0000-4000-8000-${String(++key).padStart(12, '0')}`,
  again_of_message_id: null,
  choice: null,
  ...extra,
});

async function accept(text: string, extra?: Json) {
  const response = await call(chatPaths.send, 'POST', message(text, extra));
  expect(response.status).toBe(202);
  return read(response);
}

const run = (runId: string, afterSeq = 0) =>
  call(`${chatPaths.run(runId)}?after_seq=${afterSeq}`).then(read);

/** 끝나거나 승인 대기가 될 때까지 조회 — 받은 응답을 모두 돌려줍니다 */
async function settle(runId: string) {
  const views: Json[] = [];
  for (let i = 0; i < 30; i++) {
    const view = await run(runId);
    views.push(view);
    if (!['QUEUED', 'RUNNING'].includes(view.status as string)) return views;
  }
  throw new Error('실행이 끝나지 않음');
}

/** 실행 조회 응답 하나가 BE `RunStatusResponse` 모양인지 */
function expectRunView(view: Json, { eventless = false } = {}) {
  expectShape(view, BE.RunStatusResponse);
  expect(BE.RunStatus).toContain(view.status);
  expect(typeof view.has_more).toBe('boolean');
  expect(Array.isArray(view.events)).toBe(true);
  expect(Array.isArray(view.cards)).toBe(true);
  if (view.error !== null) expectShape(view.error, BE.RunErrorDTO);
  for (const event of view.events as Json[]) {
    expectShape(event, BE.RunEventDTO);
    expect(BE.RunEvent).toContain(event.event);
    expect(Number.isInteger(event.event_seq)).toBe(true);
    expect(typeof event.masked).toBe('boolean');
  }
  // 이벤트를 읽지 않은 응답(취소 · 승인 · 거절)은 이벤트 없이 커서가 null 입니다(api_dto `next_after_seq`)
  if (eventless) expect([view.events, view.next_after_seq]).toEqual([[], null]);
  else expect(Number.isInteger(view.next_after_seq)).toBe(true);
}

/** 오류 응답이 BE 본문 모양 · 상태 · 코드인지 */
async function expectError(response: Response, status: number, code: string) {
  expect(response.status).toBe(status);
  const body = await read(response);
  expectShape(body, BE.ErrorBody);
  expect(body.code).toBe(code);
}

describe(`응답 모양 — BE ${BE.commit} api_dto.py`, () => {
  it('사용 가능 여부', async () => {
    const response = await call(chatPaths.availability);
    expect(response.status).toBe(200);
    expectShape(await read(response), BE.AvailabilityResponse);
  });

  it('새 대화(201) · 대화 목록 · 범위 바꾸기', async () => {
    const created = await call(chatPaths.conversations, 'POST', { pitch_id: null });
    expect(created.status).toBe(201);
    const conversation = await read(created);
    expectShape(conversation, BE.ConversationResponse);

    const list = await read(await call(chatPaths.conversations));
    expectShape(list, BE.ConversationListResponse);
    const items = list.conversations as Json[];
    expect(items.length).toBeGreaterThan(0);
    for (const item of items) {
      expectShape(item, BE.ConversationListItem);
      expect(typeof item.stale).toBe('boolean');
    }

    const changed = await call(chatPaths.scope(conversation.conversation_id as string), 'PATCH', {
      pitch_id: null,
    });
    expect(changed.status).toBe(200);
    expectShape(await read(changed), BE.ConversationResponse);
  });

  it('메시지 접수(202) — 상태는 늘 QUEUED', async () => {
    const accepted = await accept('지난번보다 청중을 더 봤어?');
    expectShape(accepted, BE.MessageAcceptedResponse);
    expect(accepted.status).toBe('QUEUED');
  });

  it('실행 조회 · 대화의 실행 조회 — 진행 내내 같은 모양', async () => {
    const accepted = await accept('지난번보다 청중을 더 봤어?');
    for (const view of await settle(accepted.run_id as string)) expectRunView(view);
    const latest = await read(
      await call(`${chatPaths.conversationRun(accepted.conversation_id as string)}?after_seq=0`),
    );
    expectRunView(latest);
  });

  it('실행이 한 번도 없는 대화의 실행 조회는 null', async () => {
    const created = await read(await call(chatPaths.conversations, 'POST', { pitch_id: null }));
    const response = await call(
      `${chatPaths.conversationRun(created.conversation_id as string)}?after_seq=0`,
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toBeNull();
  });

  it('취소 응답은 이벤트 없는 실행 조회 모양', async () => {
    const accepted = await accept('지난번보다 청중을 더 봤어?');
    const response = await call(chatPaths.cancel(accepted.run_id as string), 'POST');
    expect(response.status).toBe(200);
    expectRunView(await read(response), { eventless: true });
  });

  it('승인 · 거절 응답은 이벤트 없는 실행 조회 모양', async () => {
    const accepted = await accept('3장 시선을 목표로 잡아 줘');
    const views = await settle(accepted.run_id as string);
    const waiting = views.at(-1)!;
    expect(waiting.status).toBe('WAITING_APPROVAL');
    const card = (waiting.cards as Json[])[0]!;
    const response = await call(
      chatPaths.decision(accepted.run_id as string, card.action_id as string),
      'POST',
      { decision: 'REJECT', args_hash: card.args_hash },
    );
    expect(response.status).toBe(200);
    expectRunView(await read(response), { eventless: true });
  });

  it('메시지 목록', async () => {
    const accepted = await accept('지난번보다 청중을 더 봤어?');
    await settle(accepted.run_id as string);
    const page = await read(await call(chatPaths.messages(accepted.conversation_id as string)));
    expectShape(page, BE.MessageListResponse);
    const messages = page.messages as Json[];
    expect(messages.length).toBeGreaterThanOrEqual(2);
    for (const item of messages) {
      expectShape(item, BE.ChatMessageResponse);
      expect(BE.Role).toContain(item.role);
      expect(typeof item.masked).toBe('boolean');
      expect(Array.isArray(item.stale_reasons)).toBe(true);
    }
  });

  it("'기억 없이' 새 대화 — 응답과 목록에 memory_off 가 그대로", async () => {
    const created = await call(chatPaths.conversations, 'POST', {
      pitch_id: null,
      memory_off: true,
    });
    expect(created.status).toBe(201);
    const conversation = await read(created);
    expectShape(conversation, BE.ConversationResponse);
    expect(conversation.memory_off).toBe(true);
    // 빼면 BE 기본값 false
    const plain = await read(await call(chatPaths.conversations, 'POST', { pitch_id: null }));
    expect(plain.memory_off).toBe(false);

    const list = (await read(await call(chatPaths.conversations))).conversations as Json[];
    const listed = list.find((c) => c.conversation_id === conversation.conversation_id)!;
    expect(listed.memory_off).toBe(true);
  });

  it('목표 되돌리기(BE #935) — 승인해 저장한 목표를 되돌린다', async () => {
    const accepted = await accept('3장 시선을 목표로 잡아 줘');
    const runId = accepted.run_id as string;
    const card = ((await settle(runId)).at(-1)!.cards as Json[])[0]!;
    await call(chatPaths.decision(runId, card.action_id as string), 'POST', {
      decision: 'APPROVE',
      args_hash: card.args_hash,
    });
    await settle(runId);
    const response = await call(chatPaths.undo(runId, card.action_id as string), 'POST');
    expect(response.status).toBe(200);
    const undone = await read(response);
    expectShape(undone, BE.GoalUndoneResponse);
    expect(Number.isInteger(undone.goal_version)).toBe(true);
  });

  it('기억 설정 · 항목 지우기(BE #1002) — 네 요청 모두 바뀐 뒤의 설정 전체를 돌려준다', async () => {
    const expectSettings = (body: Json) => {
      expectShape(body, BE.MemorySettingResponse);
      expect(typeof body.enabled).toBe('boolean');
      for (const item of body.items as Json[]) {
        expectShape(item, BE.MemoryItemResponse);
        expect(BE.ProfileKey).toContain(item.key);
      }
    };
    // 한 번도 바꾸지 않았으면 켜짐
    const first = await read(await call(chatPaths.memory));
    expectSettings(first);
    expect(first.enabled).toBe(true);

    // 항목은 사용자 말의 저장으로만 늡니다
    await settle((await accept('짧게 설명해 달라는 걸 기억해 줘')).run_id as string);
    const saved = await read(await call(chatPaths.memory));
    expectSettings(saved);
    const [item] = saved.items as Json[];
    expect(item).toBeDefined();

    const off = await call(chatPaths.memory, 'PUT', { enabled: false });
    expect(off.status).toBe(200);
    const offBody = await read(off);
    expectSettings(offBody);
    expect(offBody.enabled).toBe(false);

    const one = await call(chatPaths.memoryItem(item!.item_id as string), 'DELETE');
    expect(one.status).toBe(200);
    const oneBody = await read(one);
    expectSettings(oneBody);
    expect(oneBody.items).toEqual([]);

    const all = await call(chatPaths.memoryItems, 'DELETE');
    expect(all.status).toBe(200);
    expectSettings(await read(all));
  });
});

describe(`오류 — BE ${BE.commit} exception.py · core/exceptions.py`, () => {
  it('본문 검사는 422 VALIDATION_ERROR — 모르는 칸도 거절한다(extra="forbid")', async () => {
    await expectError(
      await call(chatPaths.send, 'POST', message('안녕', { unknown: 1 })),
      422,
      'VALIDATION_ERROR',
    );
    await expectError(
      await call(chatPaths.scope('00000000-0000-4000-8000-000000000000'), 'PATCH', {}),
      422,
      'VALIDATION_ERROR',
    );
  });

  it('없거나 남의 대상은 404 NOT_FOUND', async () => {
    const missing = '99999999-0000-4000-8000-000000000000';
    await expectError(await call(chatPaths.run(missing)), 404, 'NOT_FOUND');
    await expectError(await call(chatPaths.cancel(missing), 'POST'), 404, 'NOT_FOUND');
  });

  it('같은 대화에 실행이 있으면 409 AGENT_RUN_IN_PROGRESS', async () => {
    const first = await accept('지난번보다 청중을 더 봤어?');
    await expectError(
      await call(
        chatPaths.send,
        'POST',
        message('하나 더', { conversation_id: first.conversation_id }),
      ),
      409,
      'AGENT_RUN_IN_PROGRESS',
    );
    // 실행 중인 대화의 범위 바꾸기도 같은 409 입니다(service/conversation.py `change_scope`)
    await expectError(
      await call(chatPaths.scope(first.conversation_id as string), 'PATCH', { pitch_id: null }),
      409,
      'AGENT_RUN_IN_PROGRESS',
    );
  });

  it('바뀐 카드로 승인하면 409 APPROVAL_HASH_MISMATCH (`ChatCardChanged`)', async () => {
    const accepted = await accept('3장 시선을 목표로 잡아 줘');
    const waiting = (await settle(accepted.run_id as string)).at(-1)!;
    const card = (waiting.cards as Json[])[0]!;
    await expectError(
      await call(chatPaths.decision(accepted.run_id as string, card.action_id as string), 'POST', {
        decision: 'APPROVE',
        args_hash: 'changed',
      }),
      409,
      'APPROVAL_HASH_MISMATCH',
    );
  });

  it('꺼지면 503 AGENT_DISABLED — 보내기 · 조회 · 목록 · 기억 모두', async () => {
    const accepted = await accept('지난번보다 청중을 더 봤어?');
    controls.disable();
    await expectError(await call(chatPaths.send, 'POST', message('안녕')), 503, 'AGENT_DISABLED');
    await expectError(await call(chatPaths.run(accepted.run_id as string)), 503, 'AGENT_DISABLED');
    await expectError(await call(chatPaths.conversations), 503, 'AGENT_DISABLED');
    await expectError(await call(chatPaths.memory), 503, 'AGENT_DISABLED');
    await expectError(await call(chatPaths.memoryItems, 'DELETE'), 503, 'AGENT_DISABLED');
  });

  it('429 는 Retry-After 를 싣는다', async () => {
    controls.fail('AGENT_DAILY_LIMIT');
    const response = await call(chatPaths.send, 'POST', message('안녕'));
    expect(response.headers.get('Retry-After')).not.toBeNull();
    await expectError(response, 429, 'AGENT_DAILY_LIMIT');
  });

  it('새 대화 · 기억 설정은 true · false 만 받는다(StrictBool) — 모르는 칸은 422', async () => {
    for (const body of [
      { pitch_id: null, memory_off: 'true' },
      { pitch_id: null, memory_off: 1 },
      { pitch_id: null, unknown: 1 },
    ])
      await expectError(await call(chatPaths.conversations, 'POST', body), 422, 'VALIDATION_ERROR');
    for (const body of [{ enabled: 'false' }, { enabled: 0 }, {}, { enabled: true, unknown: 1 }])
      await expectError(await call(chatPaths.memory, 'PUT', body), 422, 'VALIDATION_ERROR');
  });

  it('기억 항목 지우기 — 없거나 남의 항목은 404 NOT_FOUND, UUID 가 아니면 422', async () => {
    await expectError(
      await call(chatPaths.memoryItem('99999999-0000-4000-8000-000000000000'), 'DELETE'),
      404,
      'NOT_FOUND',
    );
    await expectError(
      await call(chatPaths.memoryItem('not-a-uuid'), 'DELETE'),
      422,
      'VALIDATION_ERROR',
    );
  });

  it('로그인하지 않으면 401 UNAUTHORIZED', async () => {
    user = null;
    await expectError(await call(chatPaths.conversations), 401, 'UNAUTHORIZED');
  });
});
