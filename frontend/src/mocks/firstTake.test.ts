import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, expect, it } from 'vitest';
import { firstTakeHandlers, resetTutorialMock, setTutorialReportReceived } from './firstTake';
import { homeHandlers } from './home';
import { pitchEditHandlers } from './pitchEdit';

// 기본 Take 목 자리. 샘플 피치 목이 넘긴 요청이 여기로 오는지 봅니다
const takeCreated = http.post('*/api/pitches/:pitchId/takes/', () =>
  HttpResponse.json({ message: 'Take created successfully', take_id: 't1', take_number: 1 }),
);
const server = setupServer(
  ...firstTakeHandlers,
  ...pitchEditHandlers,
  ...homeHandlers,
  takeCreated,
);
const base = 'https://pitchcoach.test';

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  resetTutorialMock();
});
afterAll(() => server.close());

const startFirstTake = async () => {
  const response = await fetch(base + '/api/pitches/tutorials/first-take', { method: 'POST' });
  expect(response.status).toBe(200);
  return (await response.json()) as { pitch_id: string; report_received: boolean };
};
const homePitches = async () =>
  ((await (await fetch(base + '/api/pitches/')).json()) as { pitches: { pitch_id: string }[] })
    .pitches;
const createTake = (pitchId: string) =>
  fetch(base + '/api/pitches/' + pitchId + '/takes/', { method: 'POST' });

it('샘플 피치는 한 번만 만들고 다시 부르면 같은 피치를 준다', async () => {
  const first = await startFirstTake();
  const again = await startFirstTake();
  expect(first).toEqual({ pitch_id: expect.any(String), report_received: false });
  expect(again.pitch_id).toBe(first.pitch_id);
  expect((await homePitches()).filter((p) => p.pitch_id === first.pitch_id)).toEqual([
    expect.objectContaining({
      is_demo: true,
      pitch_title: '[튜토리얼] 치치와 함께하는 First Take',
    }),
  ]);
});

it('발표 준비 정보 · 자료 · 대본이 같은 샘플 피치로 이어진다', async () => {
  const { pitch_id: pitchId } = await startFirstTake();

  const prepare = await (await fetch(base + `/api/pitches/${pitchId}/prepare`)).json();
  expect(prepare).toMatchObject({
    pitch_id: pitchId,
    presentation: { version: 1, page_count: 5 },
    script: { version: 1, slide_count: 5 },
    next_take_number: 1,
    missions: null,
  });

  const resources = await (await fetch(base + `/api/pitches/${pitchId}/resources`)).json();
  expect(resources).toMatchObject({
    presentation_versions: [{ id: prepare.presentation.id, page_count: 5 }],
    script_versions: [{ id: prepare.script.id }],
  });

  const script = await fetch(base + `/api/pitches/${pitchId}/scripts/${prepare.script.id}`);
  expect(await script.json()).toMatchObject({ parse_status: 'DONE', segmented: true });
  const presentation = await fetch(
    base + `/api/pitches/${pitchId}/presentations/${prepare.presentation.id}`,
  );
  expect(await presentation.json()).toMatchObject({ page_count: 5, file_url: expect.any(String) });
});

it('지우면 홈 목록에서 빠지고 다시 부르면 새 샘플 피치를 만든다', async () => {
  const { pitch_id: pitchId } = await startFirstTake();
  const removal = await fetch(base + '/api/pitches/delete/' + pitchId, { method: 'DELETE' });
  expect(removal.ok).toBe(true);
  expect((await homePitches()).map((p) => p.pitch_id)).not.toContain(pitchId);
  expect((await startFirstTake()).pitch_id).not.toBe(pitchId);
});

it('리포트를 받은 계정은 샘플 피치로 Take 를 만들지 못하고, 그 전에는 기본 Take 목이 받는다', async () => {
  const { pitch_id: pitchId } = await startFirstTake();
  expect((await createTake(pitchId)).status).toBe(200);

  setTutorialReportReceived(true);
  expect(await startFirstTake()).toEqual({ pitch_id: pitchId, report_received: true });
  const refused = await createTake(pitchId);
  expect(refused.status).toBe(409);
  expect(await refused.json()).toMatchObject({ code: 'TUTORIAL_REPORT_USED' });
});
