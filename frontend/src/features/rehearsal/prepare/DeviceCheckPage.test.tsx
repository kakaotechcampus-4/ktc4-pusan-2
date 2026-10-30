// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { PrepareResponse, PracticeCombo } from '@/types/api';
import type { CalibrationFailReason } from '@/workers/gaze.contract';
import type { DeviceError } from '../media/useCameraStream';
import { DeviceCheckPage } from './DeviceCheckPage';
import { usePrepareStore } from './prepareStore';
import { setSessionTicket } from '../lib/db';

const fixture = vi.hoisted(() => ({
  live: true,
  micOk: true,
  gazeStatus: 'DONE',
  gazeAdvice: null as CalibrationFailReason | null,
  deviceError: null as DeviceError | null,
  voiceStatus: 'IDLE',
  data: null as unknown as PrepareResponse,
  request: vi.fn(),
  gazeStart: vi.fn(),
  voiceStart: vi.fn(),
  create: vi.fn(),
  active: vi.fn(),
  endActive: vi.fn(),
}));
vi.mock('./activeTake', () => ({
  activeTakeOrNone: fixture.active,
  endActiveTake: fixture.endActive,
}));
const ref = () => ({ current: null });
const stream = {
  getVideoTracks: () => [{ getSettings: () => ({ deviceId: 'camera-a' }) }],
  getAudioTracks: () => [{ getSettings: () => ({ deviceId: 'mic-a' }) }],
};
vi.mock('../media/useCameraStream', () => ({
  DEVICE_ERROR_MESSAGE: { PERMISSION_DENIED: '카메라와 마이크 사용을 허용해 주세요.' },
  useCameraStream: () => ({
    stream: fixture.live ? stream : null,
    error: fixture.deviceError,
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
    advice: fixture.gazeAdvice,
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
  fixture.gazeAdvice = null;
  fixture.deviceError = null;
  fixture.voiceStatus = 'IDLE';
  fixture.request.mockReset().mockResolvedValue(undefined);
  fixture.create.mockReset().mockResolvedValue({ takeId: 't1', takeNumber: 4 });
  fixture.active.mockReset().mockResolvedValue(null);
  fixture.endActive.mockReset().mockResolvedValue(undefined);
  fixture.gazeStart.mockReset();
  fixture.voiceStart.mockReset();
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
  const meter = host.querySelector('[role="meter"]')!;
  expect(meter.getAttribute('aria-label')).toBe('마이크 입력 레벨');
  expect(meter.closest('.prepare-camera-column')).not.toBeNull();
  expect(meter.textContent).toBe('');
  expect(meter.querySelectorAll('.prepare-input-track > span')).toHaveLength(48);
  expect(meter.querySelectorAll('.prepare-input-fill > span')).toHaveLength(48);
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

it('restores the normal mission list after voice reading completes', async () => {
  fixture.voiceStatus = 'READING';
  await render();
  expect(host.querySelector('.prepare-mission-list')).toBeNull();
  fixture.voiceStatus = 'DONE';
  await render();
  expect(host.querySelectorAll('.prepare-mission-list li')).toHaveLength(3);
  expect(host.querySelector('.prepare-missions-compact')).toBeNull();
});

it('keeps missions visible and starting available when calibration has non-blocking advice', async () => {
  fixture.gazeAdvice = 'LOW_LOO_ACCURACY';
  await render();
  expect(host.querySelector('.prepare-equipment')!.textContent).toContain('시선 기준이 흐릿해요.');
  expect(host.querySelectorAll('.prepare-mission-list li')).toHaveLength(3);
  expect(host.querySelector('.prepare-missions-compact')).toBeNull();
  expect(findButton('Take 4 시작하기').disabled).toBe(false);
});

it.each(['PENDING', 'FAILED'] as const)(
  'keeps missions visible while a newer script is %s',
  async (status) => {
    fixture.data.newer_script = {
      id: 'new-script',
      version: 10,
      parse_status: status,
      error_code: null,
    };
    await render();
    expect(host.querySelector('.prepare-pitch')!.textContent).toContain('새 대본(v10)');
    expect(host.querySelectorAll('.prepare-mission-list li')).toHaveLength(3);
    expect(host.querySelector('.prepare-missions-compact')).toBeNull();
    expect(findButton('Take 4 시작하기').disabled).toBe(false);
  },
);

it('still summarizes missions for a device permission error and keeps full mission access', async () => {
  fixture.live = false;
  fixture.deviceError = 'PERMISSION_DENIED';
  await render();
  expect(host.querySelector('.prepare-equipment')!.textContent).toContain('사용을 허용해 주세요.');
  expect(host.querySelector('.prepare-missions-compact')).not.toBeNull();
  expect(host.querySelector('.prepare-mission-list')).toBeNull();
  expect(findButton('전체 미션 보기')).toBeTruthy();
});

it('still summarizes missions after starting fails and keeps retry available', async () => {
  fixture.create.mockRejectedValueOnce(new Error('시작 실패'));
  await render();
  await act(async () => findButton('Take 4 시작하기').click());
  expect(host.querySelector('.prepare-footer')!.textContent).toContain('요청을 처리하지 못했어요.');
  expect(host.querySelector('.prepare-missions-compact')).not.toBeNull();
  expect(findButton('전체 미션 보기')).toBeTruthy();
  expect(findButton('Take 4 시작하기').disabled).toBe(false);
});

it('locks device and script choices while checking for an existing Take, and restores focus after cancellation', async () => {
  fixture.active.mockResolvedValue({
    take_id: 'other',
    pitch_title: '다른 발표',
    pitch_id: 'other-pitch',
    take_number: 2,
  });
  await render();
  await act(async () => findButton('Take 4 시작하기').click());
  expect(fixture.create).not.toHaveBeenCalled();
  expect(
    [...host.querySelectorAll<HTMLSelectElement>('select')].every((select) => select.disabled),
  ).toBe(true);
  expect(
    [...host.querySelectorAll<HTMLButtonElement>('[role="radio"]')].every(
      (button) => button.disabled,
    ),
  ).toBe(true);
  expect(host.querySelector('dialog')).not.toBeNull();
  const cancel = [...host.querySelectorAll<HTMLButtonElement>('dialog button')].find((button) =>
    button.textContent?.includes('취소'),
  )!;
  await act(async () => cancel.click());
  expect(host.querySelector('dialog')).toBeNull();
  expect(findButton('Take 4 시작하기').disabled).toBe(false);
  expect(document.activeElement).toBe(findButton('Take 4 시작하기'));
});
