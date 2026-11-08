import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, expect, it } from 'vitest';
import { FIRST_TAKE_SCRIPT, FIRST_TAKE_TITLE } from '@/features/pitch/tutorial/firstTakeMaterials';
import { firstTakeHandlers, recordTutorialPresentation, resetTutorialMock } from './firstTake';
import { homeHandlers } from './home';
import { pitchEditHandlers } from './pitchEdit';
import { pitchHandlers } from './pitch';
import { scriptHandlers } from './scripts';

const server = setupServer(
  ...firstTakeHandlers,
  ...pitchEditHandlers,
  ...homeHandlers,
  ...pitchHandlers,
  ...scriptHandlers,
);
const base = 'https://pitchcoach.test';

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  resetTutorialMock();
});
afterAll(() => server.close());

const create = (title: string) =>
  fetch(base + '/api/pitches/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      title,
      time_limit_sec: 180,
      lower_deviation: 30,
      upper_deviation: 60,
      presentation_date: null,
    }),
  });

it('First Take 목은 생성·목록·자료 버전·장치 점검을 같은 피치로 연결한다', async () => {
  const response = await create(FIRST_TAKE_TITLE);
  expect(response.status).toBe(200);
  const { pitch_id: pitchId } = (await response.json()) as { pitch_id: string };

  const list = await fetch(base + '/api/pitches/');
  expect(((await list.json()) as { pitches: { pitch_id: string }[] }).pitches).toEqual(
    expect.arrayContaining([expect.objectContaining({ pitch_id: pitchId })]),
  );
  const empty = await fetch(base + '/api/pitches/' + pitchId + '/resources');
  expect(await empty.json()).toMatchObject({
    pitch_id: pitchId,
    presentation_versions: [],
    script_versions: [],
  });

  // PDF 업로드 핸들러는 기록한 버전 정보를 공유합니다.
  recordTutorialPresentation(pitchId, 'sample-pdf', 1, 5);
  const script = await fetch(base + '/api/pitches/' + pitchId + '/scripts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content: FIRST_TAKE_SCRIPT }),
  });
  expect(script.status).toBe(202);
  const { script_version_id: scriptId } = (await script.json()) as {
    script_version_id: string;
  };

  const resources = await fetch(base + '/api/pitches/' + pitchId + '/resources');
  expect(await resources.json()).toMatchObject({
    presentation_versions: [{ id: 'sample-pdf', version: 1, page_count: 5 }],
    script_versions: [{ id: scriptId, version: 1 }],
  });

  const prepare = await fetch(base + '/api/pitches/' + pitchId + '/prepare');
  expect(await prepare.json()).toMatchObject({
    pitch_id: pitchId,
    title: FIRST_TAKE_TITLE,
    presentation: { id: 'sample-pdf', page_count: 5 },
    script: { id: scriptId, slide_count: 5 },
    next_take_number: 1,
    missions: null,
  });

  const edit = await fetch(base + '/api/pitches/' + pitchId);
  expect(await edit.json()).toMatchObject({ pitch_id: pitchId, title: FIRST_TAKE_TITLE });

  const removal = await fetch(base + '/api/pitches/delete/' + pitchId, { method: 'DELETE' });
  expect(removal.ok).toBe(true);
  const after = await fetch(base + '/api/pitches/');
  expect(((await after.json()) as { pitches: { pitch_id: string }[] }).pitches).not.toEqual(
    expect.arrayContaining([expect.objectContaining({ pitch_id: pitchId })]),
  );
});

it('일반 피치 생성은 기존 목 핸들러를 그대로 사용한다', async () => {
  const result = await create('일반 피치');
  expect(result.ok).toBe(true);
  expect(await result.json()).toEqual({
    message: 'Pitch added successfully',
    pitch_id: expect.any(String),
  });
});
