// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { PrepareResponse, PracticeCombo } from '@/types/api';
import { DeviceCheckPage } from './DeviceCheckPage';
import { usePrepareStore } from './prepareStore';
import { setSessionTicket } from '../lib/db';

const fixture = vi.hoisted(() => ({
  live: true,
  micOk: true,
  gazeStatus: 'DONE',
  voiceStatus: 'IDLE',
  data: null as unknown as PrepareResponse,
  request: vi.fn(),
  gazeStart: vi.fn(),
  voiceStart: vi.fn(),
  create: vi.fn(),
  activeTake: vi.fn(),
  endActiveTake: vi.fn(),
}));
const ref = () => ({ current: null });
const stream = {
  getVideoTracks: () => [{ getSettings: () => ({ deviceId: 'camera-a' }) }],
  getAudioTracks: () => [{ getSettings: () => ({ deviceId: 'mic-a' }) }],
};
vi.mock('../media/useCameraStream', () => ({
  DEVICE_ERROR_MESSAGE: {},
  useCameraStream: () => ({
    stream: fixture.live ? stream : null,
    error: null,
    request: fixture.request,
  }),
}));
vi.mock('../media/useVideoStream', () => ({
  useVideoStream: () => ({ videoRef: ref(), live: fixture.live }),
}));
vi.mock('../media/useMicLevel', () => ({
  useMicLevel: () => ({
    meterRef: ref(),
    echoMeterRef: ref(),
    rowRef: ref(),
    silentRef: ref(),
    micOk: fixture.micOk,
    audioState: 'running',
    meterError: null,
    voiceRecord: vi.fn(),
  }),
}));
vi.mock('./useGazeSetup', () => ({
  CALIBRATION_FAIL_MESSAGE: {},
  useGazeSetup: () => ({
    status: fixture.gazeStatus,
    advice: null,
    failReason: null,
    unavailableMessage: null,
    overlay: false,
    frameRef: ref(),
    boxRef: ref(),
    start: fixture.gazeStart,
    cancel: vi.fn(),
  }),
}));
vi.mock('./useBaseVoice', () => ({
  useBaseVoice: () => ({
    status: fixture.voiceStatus,
    stage: 'WAITING',
    reason: null,
    heardSec: null,
    levelDb: null,
    start: fixture.voiceStart,
    cancel: vi.fn(),
    finish: vi.fn(),
  }),
}));
vi.mock('./activeTake', () => ({
  activeTakeOrNone: fixture.activeTake,
  endActiveTake: fixture.endActiveTake,
}));
vi.mock('@/shared/api/prepare', () => ({
  usePrepare: () => ({ data: fixture.data, isError: false }),
  useCreateTake: () => ({ mutateAsync: fixture.create }),
  postCalibration: vi.fn(),
}));
vi.mock('../lib/db', () => ({
  startSession: vi.fn().mockResolvedValue('client-session'),
  setTakeId: vi.fn().mockResolvedValue(undefined),
  setPrepareContext: vi.fn().mockResolvedValue(undefined),
  setSessionTicket: vi.fn().mockResolvedValue(undefined),
}));

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: {
      enumerateDevices: async () => [
        { kind: 'videoinput', deviceId: 'camera-a', label: '카메라 A' },
        { kind: 'videoinput', deviceId: 'camera-b', label: '카메라 B' },
        { kind: 'audioinput', deviceId: 'mic-a', label: '마이크 A' },
      ],
    },
  });
  fixture.live = true;
  fixture.micOk = true;
  fixture.gazeStatus = 'DONE';
  fixture.voiceStatus = 'IDLE';
  fixture.request.mockReset().mockResolvedValue(undefined);
  fixture.create.mockReset().mockResolvedValue({ takeId: 't1', takeNumber: 4 });
  fixture.gazeStart.mockReset();
  fixture.voiceStart.mockReset();
  fixture.activeTake.mockReset().mockResolvedValue(null);
  fixture.endActiveTake.mockReset().mockResolvedValue(undefined);
  usePrepareStore.setState(usePrepareStore.getInitialState(), true);
  fixture.data = {
    pitch_id: 'p1',
    title: '발표',
    time_limit_sec: 300,
    presentation: { id: 'latest-pv', version: 9, page_count: 12 },
    script: { id: 'latest-sv', version: 9, slide_count: 12 },
    newer_script: null,
    next_take_number: 4,
    default_script_mode: 'HIGHLIGHT',
    missions: {
      source_take_number: 2,
      items: Array.from({ length: 20 }, (_, i) => ({
        mission_id: 'm' + i,
        priority: i * 2 + 1,
        area: 'GAZE',
        slide_number: null,
        description: '미션 ' + (i + 1),
        target: { metric: 'camera_ratio', operator: 'GTE', value: 0.5 },
        reason: null,
      })),
    },
  };
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});
async function render(combo?: PracticeCombo) {
  await act(async () =>
    root.render(
      <MemoryRouter
        initialEntries={[{ pathname: '/pitch/p1/device-check', state: { practice: combo } }]}
      >
        <Routes>
          <Route path="/pitch/:pitchId/device-check" element={<DeviceCheckPage />} />
          <Route path="/takes/:takeId/rehearsal" element={<p>연습 시작</p>} />
          <Route path="/takes/:takeId/exam" element={<p>실전 시작</p>} />
        </Routes>
      </MemoryRouter>,
    ),
  );
}
const findButton = (text: string) =>
  [...host.querySelectorAll<HTMLButtonElement>('button')].find(
    (node) => node.textContent === text,
  )!;

it('starts with the chosen server version IDs while the optional voice task is still untouched', async () => {
  const combo: PracticeCombo = {
    title: '고른 발표',
    presentationVersionId: 'chosen-pv',
    scriptVersionId: 'chosen-sv',
    slideVersion: 2,
    scriptVersion: 3,
    goalTimeSec: 180,
  };
  await render(combo);
  expect(host.querySelectorAll('.prepare-camera select, .prepare-camera button')).toHaveLength(0);
  expect(findButton('Take 4 시작하기').disabled).toBe(false);
  await act(async () => findButton('Take 4 시작하기').click());
  expect(fixture.create).toHaveBeenCalledWith(
    expect.objectContaining({
      body: expect.objectContaining({
        presentation_version_id: 'chosen-pv',
        script_version_id: 'chosen-sv',
        client_session_id: 'client-session',
      }),
    }),
  );
  expect(setSessionTicket).toHaveBeenCalledWith(
    'client-session',
    expect.objectContaining({
      presentationVersionId: 'chosen-pv',
      scriptVersionId: 'chosen-sv',
      timeLimitSec: 180,
    }),
  );
  expect(host.textContent).toContain('연습 시작');
});

it('keeps device selection and calibration as separate actions and blocks starting before calibration', async () => {
  fixture.gazeStatus = 'IDLE';
  await render();
  expect(findButton('Take 4 시작하기').disabled).toBe(true);
  const camera = host.querySelector<HTMLSelectElement>('select')!;
  await act(async () => {
    camera.value = 'camera-b';
    camera.dispatchEvent(new Event('change', { bubbles: true }));
  });
  expect(fixture.request).toHaveBeenCalledWith({
    videoDeviceId: 'camera-b',
    audioDeviceId: 'mic-a',
  });
  expect(fixture.gazeStart).not.toHaveBeenCalled();
  await act(async () => findButton('시선 기준 잡기').click());
  expect(fixture.gazeStart).toHaveBeenCalledOnce();
});

it('retains all mission access and the start action during optional voice reading', async () => {
  fixture.voiceStatus = 'READING';
  await render();
  expect(host.querySelector('.prepare-mission-list')).toBeNull();
  expect(findButton('전체 미션 보기')).toBeTruthy();
  expect(findButton('다 읽었어요')).toBeTruthy();
  expect(findButton('그만두기')).toBeTruthy();
  expect(findButton('Take 4 시작하기').disabled).toBe(false);
  expect(findButton('다시 잡기').disabled).toBe(true);
});

it('locks settings while confirming another take and restores start focus on cancel', async () => {
  fixture.activeTake.mockResolvedValue({
    take_id: 'other-take',
    pitch_id: 'other-pitch',
    pitch_title: '다른 발표',
    take_number: 2,
  });
  await render();
  await act(async () => findButton('Take 4 시작하기').click());
  expect(host.querySelector<HTMLDialogElement>('dialog')!.open).toBe(true);
  expect(fixture.create).not.toHaveBeenCalled();
  expect(
    [...host.querySelectorAll<HTMLButtonElement>('[role="radio"]')].every((b) => b.disabled),
  ).toBe(true);
  expect([...host.querySelectorAll<HTMLSelectElement>('select')].every((s) => s.disabled)).toBe(
    true,
  );
  await act(async () => findButton('취소').click());
  expect(host.querySelector('dialog')).toBeNull();
  expect(document.activeElement).toBe(findButton('Take 4 시작하기'));
  expect(fixture.endActiveTake).not.toHaveBeenCalled();
});

it('ends another take only after confirmation before creating the new take', async () => {
  const other = { take_id: 'other-take', pitch_id: 'p2', pitch_title: '다른 발표', take_number: 2 };
  fixture.activeTake.mockResolvedValue(other);
  await render();
  await act(async () => findButton('Take 4 시작하기').click());
  expect(fixture.create).not.toHaveBeenCalled();
  await act(async () => findButton('그 연습 끝내고 시작').click());
  expect(fixture.endActiveTake).toHaveBeenCalledWith(other);
  expect(fixture.create).toHaveBeenCalledOnce();
  expect(host.textContent).toContain('연습 시작');
});
