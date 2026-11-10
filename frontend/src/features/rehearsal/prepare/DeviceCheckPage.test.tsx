// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { PrepareResponse, PracticeCombo } from '@/types/api';
import type { CalibrationFailReason } from '@/workers/gaze.contract';
import type { DeviceError } from '../media/useCameraStream';
import { DeviceCheckPage } from './DeviceCheckPage';
import { usePrepareStore } from './prepareStore';
import { setSessionTicket } from '../lib/db';
import {
  BASE_VOICE_REUSE_MS,
  forgetBaseVoice,
  keepBaseVoice,
  recallBaseVoice,
} from '../lib/baseVoiceMemory';
import { ApiFailure } from '@/shared/api/tokenStore';
import { takeScreenSnapshot } from '@/shared/lib/chatScreen';

const fixture = vi.hoisted(() => ({
  live: true,
  micOk: true,
  gazeStatus: 'DONE',
  gazeAdvice: null as CalibrationFailReason | null,
  deviceError: null as DeviceError | null,
  /** 기준 목소리 읽기 (#1179). 시작 조건이라 기본은 잰 상태(DONE)입니다 */
  voiceStatus: 'DONE',
  voiceLevelDb: -30 as number | null,
  voiceShorts: 0,
  /** 크기를 잰 트랙 (`useMicLevel` `measuredWith`) */
  measuredWith: { deviceId: 'mic-a', autoGainOff: true } as {
    deviceId: string;
    autoGainOff: boolean | null;
  } | null,
  postCalibration: vi.fn(),
  data: null as unknown as PrepareResponse,
  /** 버전 목록에서 꺼낸 가장 최근 자료 (`useLatestSlide`) */
  latestSlide: {
    data: null as { id: string; version: number; pageCount: number | null } | null | undefined,
    isPending: false,
    isError: false,
  },
  /** 최신 자료를 다시 받는 중인지 · [다시 확인]이 부르는 것 */
  latestFetching: false,
  refetchLatest: vi.fn(),
  request: vi.fn(),
  gazeStart: vi.fn(),
  voiceStart: vi.fn(),
  create: vi.fn(),
  active: vi.fn(),
  endActive: vi.fn(),
  /** 화면이 useMicLevel 에 넘긴 옵션. 무음 경고를 언제부터 세는지(silenceFrom)를 봅니다 */
  micOptions: null as { silenceFrom?: () => number | null } | null,
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
  useMicLevel: (_stream: unknown, options: { silenceFrom?: () => number | null }) => {
    fixture.micOptions = options;
    return {
      meterRef: ref(),
      echoMeterRef: ref(),
      rowRef: ref(),
      silentRef: ref(),
      micOk: fixture.micOk,
      audioState: 'running',
      meterError: null,
      voiceRecord: vi.fn(),
      measuredWith: fixture.measuredWith,
    };
  },
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
    heardSec: fixture.voiceStatus === 'DONE' ? 9.5 : null,
    levelDb: fixture.voiceStatus === 'DONE' ? fixture.voiceLevelDb : null,
    shorts: fixture.voiceShorts,
    start: fixture.voiceStart,
    cancel: vi.fn(),
    finish: vi.fn(),
  }),
}));
vi.mock('@/shared/api/prepare', () => ({
  usePrepare: () => ({ data: fixture.data, isError: false }),
  useLatestSlide: () => ({
    ...fixture.latestSlide,
    isFetching: fixture.latestFetching,
    refetch: fixture.refetchLatest,
  }),
  useCreateTake: () => ({ mutateAsync: fixture.create }),
  postCalibration: fixture.postCalibration,
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
  fixture.voiceStatus = 'DONE';
  fixture.voiceLevelDb = -30;
  fixture.voiceShorts = 0;
  fixture.measuredWith = { deviceId: 'mic-a', autoGainOff: true };
  fixture.postCalibration.mockReset().mockResolvedValue({});
  forgetBaseVoice();
  fixture.latestSlide = { data: null, isPending: false, isError: false };
  fixture.latestFetching = false;
  fixture.refetchLatest.mockReset().mockResolvedValue(undefined);
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
  vi.unstubAllEnvs();
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});
/** 무대 자리. 장치 점검이 넘긴 state 중 목소리 기준 저장 여부를 표시로 남깁니다 (#1179) */
function RehearsalProbe() {
  const state = useLocation().state as { baseVoiceUnconfirmed?: boolean } | null;
  return <p data-unconfirmed={String(state?.baseVoiceUnconfirmed ?? false)}>연습 시작</p>;
}
async function render(combo?: PracticeCombo, pitchId = 'p1') {
  await act(async () =>
    root.render(
      // 피치를 바꿔 다시 그릴 때 새 주소로 열리도록 key 를 둡니다
      <MemoryRouter
        key={pitchId}
        initialEntries={[
          { pathname: `/pitch/${pitchId}/device-check`, state: { practice: combo } },
        ]}
      >
        <Routes>
          <Route path="/pitch/:pitchId/device-check" element={<DeviceCheckPage />} />
          <Route path="/takes/:takeId/rehearsal" element={<RehearsalProbe />} />
          <Route path="/takes/:takeId/exam" element={<p>실전 시작</p>} />
          <Route path="/pitch/:id/edit" element={<p>피치 수정</p>} />
        </Routes>
      </MemoryRouter>,
    ),
  );
}
const findButton = (text: string) =>
  [...host.querySelectorAll<HTMLButtonElement>('button')].find(
    (node) => node.textContent === text,
  )!;
const scriptOption = (label: string) =>
  [...host.querySelectorAll<HTMLButtonElement>('[role="radio"]')].find((node) =>
    node.textContent?.includes(label),
  )!;

it('starts with the chosen server version IDs once the base voice is measured', async () => {
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
      // 조합의 목표 시간(180)이 아니라 피치의 지금 설정(300)입니다 — BE 가 Take 를 그 값으로 기록합니다
      timeLimitSec: 300,
      // 실패해 장치 점검으로 돌아갈 때 이 조합을 다시 넘깁니다 (deviceCheckLink)
      practice: combo,
    }),
  );
  expect(host.textContent).toContain('연습 시작');
});

it('넘겨받은 조합보다 서버 최신 조합이 새 것이면 알리고, [최신으로]를 누르면 최신 조합으로 Take 를 만든다', async () => {
  const old: PracticeCombo = {
    title: '고른 발표',
    presentationVersionId: 'old-pv',
    scriptVersionId: 'old-sv',
    slideVersion: 2,
    scriptVersion: 3,
    goalTimeSec: 180,
  };
  await render(old);
  const notice = host.querySelector('[data-newer-combo]')!;
  expect(notice.textContent).toContain('최신 조합이 있어요: 자료 v9 · 대본 v9');

  // 안내는 늘 그려 둔 live 영역에 들어가고, 버튼은 그 밖에 있습니다
  const live = [...host.querySelectorAll('[role="status"]')].find((n) =>
    n.textContent?.includes('최신 조합이 있어요'),
  )!;
  expect(live.contains(findButton('최신으로'))).toBe(false);

  const latestButton = findButton('최신으로');
  latestButton.focus();
  await act(async () => latestButton.click());
  expect(host.querySelector('[data-newer-combo]')).toBeNull();
  // 누른 버튼이 사라져도 초점이 body 로 빠지지 않고 시작 버튼으로 가며, 바꿨다고 알립니다
  expect(document.activeElement).toBe(findButton('Take 4 시작하기'));
  expect(live.textContent).toBe('최신 조합(자료 v9 · 대본 v9)으로 바꿨어요');
  await act(async () => findButton('Take 4 시작하기').click());
  expect(fixture.create).toHaveBeenCalledWith(
    expect.objectContaining({
      body: expect.objectContaining({
        presentation_version_id: 'latest-pv',
        script_version_id: 'latest-sv',
      }),
    }),
  );
  // 예전 조합을 티켓에 남기지 않습니다 — 실패해 돌아와도 최신 조합으로 엽니다 (deviceCheckLink)
  expect(setSessionTicket).toHaveBeenCalledWith(
    'client-session',
    expect.objectContaining({ practice: null }),
  );
});

it('조합 없이 들어오면 최신 조합 안내를 띄우지 않는다', async () => {
  await render();
  expect(host.querySelector('[data-newer-combo]')).toBeNull();
});

/** 멘토 리뷰 동선: 자료 v1(3쪽) · 대본(3장)에 v2(4쪽)를 올린 뒤 리포트 · 대화에서 조합 없이 들어온다 */
function olderSlidePicked() {
  fixture.data = {
    ...fixture.data,
    presentation: { id: 'pv1', version: 1, page_count: 3 },
    script: { id: 'sv1', version: 1, slide_count: 3 },
  };
  fixture.latestSlide = {
    data: { id: 'pv2', version: 2, pageCount: 4 },
    isPending: false,
    isError: false,
  };
}

it('조합 없이 들어왔는데 준비 정보가 지난 자료를 골랐으면 확인받기 전에는 시작하지 않는다', async () => {
  olderSlidePicked();
  await render();
  const notice = host.querySelector('[data-older-slide]')!;
  expect(notice.textContent).toContain(
    '가장 최근 자료(v2 · 4쪽)는 대본(3장)과 장 수가 달라 지난 자료(v1)로 연습하게 돼요',
  );
  expect(findButton('Take 4 시작하기').disabled).toBe(true);

  await act(async () => findButton('지난 자료로 연습').click());
  expect(host.querySelector('[data-older-slide]')).toBeNull();
  expect(
    [...host.querySelectorAll('[role="status"]')].some(
      (n) => n.textContent === '지난 자료(v1)로 연습해요',
    ),
  ).toBe(true);
  await act(async () => findButton('Take 4 시작하기').click());
  expect(fixture.create).toHaveBeenCalledWith(
    expect.objectContaining({
      body: expect.objectContaining({ presentation_version_id: 'pv1', script_version_id: 'sv1' }),
    }),
  );
});

it('[매핑 확인하기]를 누르면 피치 수정으로 가고 Take 를 만들지 않는다', async () => {
  olderSlidePicked();
  await render();
  await act(async () => findButton('매핑 확인하기').click());
  expect(host.textContent).toContain('피치 수정');
  expect(fixture.create).not.toHaveBeenCalled();
});

it('가장 최근 자료를 받는 동안에는 시작하지 않는다', async () => {
  fixture.latestSlide = { data: undefined, isPending: true, isError: false };
  await render();
  expect(findButton('Take 4 시작하기').disabled).toBe(true);
});

const liveText = () =>
  [...host.querySelectorAll('[role="status"]')].find((n) => n.classList.contains('sr-only'))!
    .textContent;

it('가장 최근 자료를 못 받았으면 다시 확인할지 이 자료로 연습할지 고르기 전에는 시작하지 않는다', async () => {
  fixture.latestSlide = { data: undefined, isPending: false, isError: true };
  await render();
  const notice = host.querySelector('[data-latest-unchecked]')!;
  const text = '가장 최근 자료를 확인하지 못해 지금 자료(v9)가 최신인지 알 수 없어요';
  expect(notice.textContent).toContain(text);
  expect(liveText()).toBe(text);
  expect(findButton('Take 4 시작하기').disabled).toBe(true);

  await act(async () => findButton('다시 확인').click());
  expect(fixture.refetchLatest).toHaveBeenCalledTimes(1);
  expect(findButton('Take 4 시작하기').disabled).toBe(true);

  await act(async () => findButton('이 자료로 연습').click());
  expect(host.querySelector('[data-latest-unchecked]')).toBeNull();
  expect(liveText()).toBe('지금 자료(v9)로 연습해요');
  await act(async () => findButton('Take 4 시작하기').click());
  expect(fixture.create).toHaveBeenCalledWith(
    expect.objectContaining({
      body: expect.objectContaining({
        presentation_version_id: 'latest-pv',
        script_version_id: 'latest-sv',
      }),
    }),
  );
});

it('[다시 확인]이 끝나 안내가 사라지면 초점을 시작 버튼으로 옮긴다', async () => {
  fixture.latestSlide = { data: undefined, isPending: false, isError: true };
  await render();
  const recheck = findButton('다시 확인');
  recheck.focus();
  await act(async () => recheck.click());
  // 쿼리가 다시 받는 중으로 바뀐 것을 그립니다
  fixture.latestFetching = true;
  await render();
  // 다시 받는 동안에는 버튼을 막지 않아 초점이 그대로이고, 시작도 막혀 있습니다
  expect(recheck.textContent).toBe('확인하는 중…');
  expect(document.activeElement).toBe(recheck);
  expect(findButton('Take 4 시작하기').disabled).toBe(true);

  fixture.latestFetching = false;
  fixture.latestSlide = {
    data: { id: 'latest-pv', version: 9, pageCount: 12 },
    isPending: false,
    isError: false,
  };
  await render();
  expect(host.querySelector('[data-latest-unchecked]')).toBeNull();
  expect(document.activeElement).toBe(findButton('Take 4 시작하기'));
});

it('[최신으로] 뒤 가장 최근 자료가 대본과 장 수가 다르면 그 안내를 먼저 알리고 초점을 안내로 옮긴다', async () => {
  // 자료 v1 · 대본 v1 로 고른 조합을 들고 왔고, 서버 최신 조합은 v2 · v2(3장), 가장 최근 PDF 는 v3(4쪽)
  fixture.data = {
    ...fixture.data,
    presentation: { id: 'pv2', version: 2, page_count: 3 },
    script: { id: 'sv2', version: 2, slide_count: 3 },
  };
  fixture.latestSlide = {
    data: { id: 'pv3', version: 3, pageCount: 4 },
    isPending: false,
    isError: false,
  };
  await render({
    title: '고른 발표',
    presentationVersionId: 'pv1',
    scriptVersionId: 'sv1',
    slideVersion: 1,
    scriptVersion: 1,
    goalTimeSec: 300,
  });
  // 넘겨받은 조합(v1)도 가장 최근 자료보다 지난 것이라 먼저 고르게 합니다 (#1099 리뷰)
  expect(host.querySelector('[data-older-slide]')!.textContent).toContain(
    '가장 최근 자료(v3) 대신 지난 자료(v1)로 연습하게 돼요',
  );
  expect(findButton('Take 4 시작하기').disabled).toBe(true);

  const latestButton = findButton('최신으로');
  latestButton.focus();
  await act(async () => latestButton.click());
  const notice = host.querySelector<HTMLElement>('[data-older-slide]')!;
  expect(notice).not.toBeNull();
  expect(liveText()).toBe(
    '최신 조합(자료 v2 · 대본 v2)으로 바꿨어요. 가장 최근 자료(v3 · 4쪽)는 대본(3장)과 장 수가 달라 지난 자료(v2)로 연습하게 돼요',
  );
  expect(findButton('Take 4 시작하기').disabled).toBe(true);
  // 막힌 시작 버튼이 아니라 고를 안내로 초점이 갑니다 — body 로 빠지지 않습니다
  expect(document.activeElement).toBe(notice);
});

it('준비 정보나 넘겨받은 조합이 가장 최근 자료면 지난 자료 안내를 띄우지 않는다', async () => {
  fixture.latestSlide = {
    data: { id: 'latest-pv', version: 9, pageCount: 12 },
    isPending: false,
    isError: false,
  };
  await render();
  expect(host.querySelector('[data-older-slide]')).toBeNull();

  olderSlidePicked();
  fixture.latestSlide = {
    data: { id: 'pv1', version: 1, pageCount: 3 },
    isPending: false,
    isError: false,
  };
  await render(
    {
      title: '고른 발표',
      presentationVersionId: 'pv1',
      scriptVersionId: 'sv1',
      slideVersion: 1,
      scriptVersion: 1,
      goalTimeSec: 300,
    },
    'p2',
  );
  expect(host.querySelector('[data-older-slide]')).toBeNull();
  expect(findButton('Take 4 시작하기').disabled).toBe(false);
});

it('조합을 넘겨받은 뒤 다른 탭에서 새 PDF 를 올렸으면, 지난 조합으로 연습할지 고르기 전에는 시작하지 않는다', async () => {
  // V1(3장) 조합으로 들어왔고, 그 사이 V2(4장)가 올라왔습니다. 준비 정보도 장 수가 맞는 V1 을 골라 [최신으로]는 없습니다
  fixture.data = {
    ...fixture.data,
    presentation: { id: 'pv1', version: 1, page_count: 3 },
    script: { id: 'sv1', version: 1, slide_count: 3 },
  };
  fixture.latestSlide = {
    data: { id: 'pv2', version: 2, pageCount: 4 },
    isPending: false,
    isError: false,
  };
  await render({
    title: '고른 발표',
    presentationVersionId: 'pv1',
    scriptVersionId: 'sv1',
    slideVersion: 1,
    scriptVersion: 1,
    goalTimeSec: 300,
  });
  expect(host.querySelector('[data-newer-combo]')).toBeNull();
  expect(host.querySelector('[data-older-slide]')!.textContent).toContain(
    '가장 최근 자료(v2 · 4쪽)는 대본(3장)과 장 수가 달라 지난 자료(v1)로 연습하게 돼요',
  );
  expect(findButton('Take 4 시작하기').disabled).toBe(true);

  await act(async () => findButton('지난 자료로 연습').click());
  expect(host.querySelector('[data-older-slide]')).toBeNull();
  await act(async () => findButton('Take 4 시작하기').click());
  expect(fixture.create).toHaveBeenCalledWith(
    expect.objectContaining({
      body: expect.objectContaining({ presentation_version_id: 'pv1', script_version_id: 'sv1' }),
    }),
  );
});

it('가장 최근 자료를 다시 받는 동안에는 받기 전 결과로 시작하지 않는다 — 버튼 초점은 그대로 둔다', async () => {
  fixture.latestSlide = {
    data: { id: 'latest-pv', version: 9, pageCount: 12 },
    isPending: false,
    isError: false,
  };
  await render();
  const start = findButton('Take 4 시작하기');
  start.focus();
  // 창으로 돌아와 다시 받는 중입니다
  fixture.latestFetching = true;
  await render();
  expect(start.disabled).toBe(false);
  expect(start.getAttribute('aria-disabled')).toBe('true');
  expect(document.activeElement).toBe(start);
  await act(async () => start.click());
  expect(fixture.create).not.toHaveBeenCalled();

  fixture.latestFetching = false;
  await render();
  expect(start.getAttribute('aria-disabled')).toBeNull();
  await act(async () => start.click());
  expect(fixture.create).toHaveBeenCalledTimes(1);
});

it('[다시 확인] 뒤 안내는 사라졌는데 다른 이유로 아직 시작할 수 없으면 초점을 본문으로 옮긴다', async () => {
  fixture.gazeStatus = 'IDLE';
  fixture.latestSlide = { data: undefined, isPending: false, isError: true };
  await render();
  const recheck = findButton('다시 확인');
  recheck.focus();
  await act(async () => recheck.click());
  fixture.latestSlide = {
    data: { id: 'latest-pv', version: 9, pageCount: 12 },
    isPending: false,
    isError: false,
  };
  await render();
  expect(host.querySelector('[data-latest-unchecked]')).toBeNull();
  expect(findButton('Take 4 시작하기').disabled).toBe(true);
  expect(document.activeElement?.id).toBe('main');
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

it('lets the app header skip link move focus to the setup body', async () => {
  await render();
  const main = host.querySelector('main')!;
  expect(main.id).toBe('main');
  expect(main.tabIndex).toBe(-1);
});

// #1179 기준 목소리는 시작 조건이라, 읽는 동안에는 시작할 수 없습니다
it('retains all mission access during voice reading, and waits for the reading before starting', async () => {
  fixture.voiceStatus = 'READING';
  await render();
  expect(host.querySelector('.prepare-mission-list')).toBeNull();
  expect(findButton('전체 미션 보기')).toBeTruthy();
  expect(findButton('다 읽었어요')).toBeTruthy();
  expect(findButton('그만두기')).toBeTruthy();
  expect(findButton('Take 4 시작하기').disabled).toBe(true);
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

it("does not carry one pitch's exam choice into another pitch's server default and Take", async () => {
  await render(undefined, 'p1');
  await act(async () => scriptOption('실전 모드').click());
  expect(scriptOption('실전 모드').getAttribute('aria-checked')).toBe('true');

  fixture.data = { ...fixture.data, pitch_id: 'p2', default_script_mode: 'KEYWORD' };
  await render(undefined, 'p2');
  expect(scriptOption('핵심 키워드').getAttribute('aria-checked')).toBe('true');

  await act(async () => findButton('Take 4 시작하기').click());
  expect(fixture.create).toHaveBeenCalledWith(
    expect.objectContaining({
      pitchId: 'p2',
      body: expect.objectContaining({ mode: 'COACHING', script_mode: 'KEYWORD' }),
    }),
  );
  expect(host.textContent).toContain('연습 시작');
});

it('counts the silence warning only while reading the usual voice, from when reading started', async () => {
  const now = vi.spyOn(performance, 'now').mockReturnValue(1000);
  await render();
  // 읽기 전에는 말할 이유가 없어 경고하지 않습니다
  expect(fixture.micOptions?.silenceFrom?.()).toBeNull();

  now.mockReturnValue(5000);
  fixture.voiceStatus = 'READING';
  await render();
  // 페이지를 연 때(1000)가 아니라 읽기를 누른 때(5000)부터 셉니다
  expect(fixture.micOptions?.silenceFrom?.()).toBe(5000);

  fixture.voiceStatus = 'DONE';
  await render();
  expect(fixture.micOptions?.silenceFrom?.()).toBeNull();
  now.mockRestore();
});

// #925 말 없이 오래 있으면 서버가 끝냅니다. 목표 시간보다 일찍 끝나 놀라지 않게 시작 전에 알립니다
it('tells before starting that a long silence ends the take, apart from the goal time', async () => {
  await render();
  const footer = host.querySelector('.prepare-footer')!;
  expect(footer.textContent).toContain(
    '말이 없는 상태가 2분 넘게 이어지면 연습이 자동으로 끝날 수 있어요.',
  );
  // 바뀌지 않는 말이라 안내 알림 영역 밖에 둡니다 — 안내가 바뀔 때마다 다시 읽히지 않게
  expect(footer.querySelector('[role="status"]')!.textContent).not.toContain('2분 넘게');
});

it('tells the chat panel the current check state when a message is sent (chat step 6)', async () => {
  fixture.micOk = false;
  fixture.gazeStatus = 'RUNNING';
  await render();
  expect(takeScreenSnapshot('/pitch/:pitchId/device-check')).toMatchObject({
    ok: true,
    screen: { name: 'device_check', camera: 'connected', mic: 'checking', gaze_setup: 'RUNNING' },
  });
});

// ── #1179 기준 목소리 ─────────────────────────────────────────────

const footerText = () => host.querySelector('.prepare-footer')!.textContent ?? '';
const voiceRow = () => host.querySelector('.prepare-equipment')!.textContent ?? '';
const findButtonStarting = (prefix: string) =>
  [...host.querySelectorAll<HTMLButtonElement>('button')].find((node) =>
    node.textContent?.startsWith(prefix),
  ) ?? null;
const sentBody = () => fixture.postCalibration.mock.calls[0]?.[2];

it('기준 목소리가 없으면 시작할 수 없고, 항목은 "선택"이 아니라 "기준 목소리"다', async () => {
  fixture.voiceStatus = 'IDLE';
  await render();
  expect(findButton('Take 4 시작하기').disabled).toBe(true);
  expect(footerText()).toContain('기준 목소리를 재야 연습을 시작할 수 있어요');
  expect(voiceRow()).toContain('기준 목소리');
  expect(voiceRow()).not.toContain('평소 목소리');
  expect(voiceRow()).not.toContain('선택');
  expect(findButton('목소리 재기')).toBeTruthy();
});

it('이번에 잰 기준 목소리를 보내고, 시선 보정 요약이 없어도 보낸다', async () => {
  await render();
  await act(async () => findButton('Take 4 시작하기').click());
  expect(fixture.postCalibration).toHaveBeenCalledTimes(1);
  // 시험의 스토어에는 시선 보정 요약이 없습니다 — 그래도 목소리 기준은 갑니다
  expect(sentBody()).toMatchObject({ base_level_db: -30, result: null });
  expect(host.querySelector('[data-unconfirmed]')!.getAttribute('data-unconfirmed')).toBe('false');
});

it('이번에 잰 값은 같은 마이크 · 자동 음량 조절 꺼짐이면 다시 쓰도록 둔다', async () => {
  await render();
  expect(
    recallBaseVoice({ deviceId: 'mic-a', autoGainOff: true }, performance.now()),
  ).toMatchObject({
    levelDb: -30,
  });
});

it('자동 음량 조절 상태를 모르는 트랙으로 잰 값은 다시 쓰도록 두지 않는다', async () => {
  fixture.measuredWith = { deviceId: 'mic-a', autoGainOff: null };
  await render();
  expect(recallBaseVoice({ deviceId: 'mic-a', autoGainOff: true }, performance.now())).toBeNull();
});

it('같은 환경에서 직전에 잰 기준 목소리가 있으면 그대로 쓰기를 골라 시작하고 그 값을 보낸다', async () => {
  keepBaseVoice(-27.5, { deviceId: 'mic-a', autoGainOff: true }, performance.now());
  fixture.voiceStatus = 'IDLE';
  await render();
  expect(findButton('Take 4 시작하기').disabled).toBe(true);
  expect(footerText()).toContain('조금 전에 잰 기준 목소리를 그대로 쓰거나, 다시 재 주세요');
  // 다시 재기와 그대로 쓰기를 함께 보입니다
  expect(findButton('다시 재기')).toBeTruthy();
  const reuse = findButtonStarting('방금 잰 기준 목소리 그대로 쓰기')!;
  expect(reuse.textContent).toContain('같은 자리 · 같은 마이크');
  await act(async () => reuse.click());
  expect(voiceRow()).toContain('조금 전 기준 사용');
  expect(findButton('Take 4 시작하기').disabled).toBe(false);
  await act(async () => findButton('Take 4 시작하기').click());
  expect(sentBody()).toMatchObject({ base_level_db: -27.5 });
});

it('다른 마이크면 직전 기준 목소리를 권하지 않는다', async () => {
  keepBaseVoice(-27.5, { deviceId: 'mic-b', autoGainOff: true }, performance.now());
  fixture.voiceStatus = 'IDLE';
  await render();
  expect(findButtonStarting('방금 잰 기준 목소리 그대로 쓰기')).toBeNull();
  expect(findButton('목소리 재기')).toBeTruthy();
});

it('그대로 쓰기를 고른 뒤 30분이 지나 시작을 누르면 Take 를 만들지 않고 다시 재라고 한다', async () => {
  const measuredAt = performance.now();
  keepBaseVoice(-27.5, { deviceId: 'mic-a', autoGainOff: true }, measuredAt);
  fixture.voiceStatus = 'IDLE';
  await render();
  await act(async () => findButtonStarting('방금 잰 기준 목소리 그대로 쓰기')!.click());
  // 화면을 띄워 둔 채로 시간이 흐름 — 누르는 순간 다시 확인합니다
  const now = vi.spyOn(performance, 'now').mockReturnValue(measuredAt + BASE_VOICE_REUSE_MS + 1);
  await act(async () => findButton('Take 4 시작하기').click());
  expect(fixture.create).not.toHaveBeenCalled();
  expect(footerText()).toContain('조금 전에 잰 기준 목소리를 더 쓸 수 없어요');
  expect(findButton('Take 4 시작하기').disabled).toBe(true);
  // 다시 재기 시작하면 그 문구는 지웁니다 — 남으면 다시 잰 뒤에도 안내가 그 말에 머뭅니다
  await act(async () => findButton('다시 재기').click());
  expect(fixture.voiceStart).toHaveBeenCalledOnce();
  expect(footerText()).not.toContain('더 쓸 수 없어요');
  now.mockRestore();
});

it('그대로 쓰기를 골라 둔 채 30분이 지나면 항목도 "조금 전 기준 사용"을 내려놓는다', async () => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
  try {
    const measuredAt = performance.now();
    keepBaseVoice(-27.5, { deviceId: 'mic-a', autoGainOff: true }, measuredAt);
    fixture.voiceStatus = 'IDLE';
    await render();
    await act(async () => findButtonStarting('방금 잰 기준 목소리 그대로 쓰기')!.click());
    expect(voiceRow()).toContain('조금 전 기준 사용');
    const now = vi.spyOn(performance, 'now').mockReturnValue(measuredAt + BASE_VOICE_REUSE_MS + 1);
    // 화면의 시계는 정해진 간격으로 갱신됩니다
    await act(async () => vi.advanceTimersByTime(30_000));
    expect(voiceRow()).not.toContain('조금 전 기준 사용');
    expect(voiceRow()).toContain('아직 재지 않았어요');
    expect(findButton('Take 4 시작하기').disabled).toBe(true);
    expect(footerText()).toContain('기준 목소리를 재야 연습을 시작할 수 있어요');
    now.mockRestore();
  } finally {
    vi.useRealTimers();
  }
});

it('읽기에 두 번 실패하면 탈출구를 보이고, 고르면 기준 없이(null) 시작하며 다시 쓰기 칸에 넣지 않는다', async () => {
  fixture.voiceStatus = 'SHORT';
  fixture.voiceShorts = 1;
  await render();
  expect(findButton('발표 처음 목소리를 기준으로 시작하기')).toBeUndefined();
  fixture.voiceShorts = 2;
  await render();
  await act(async () => findButton('발표 처음 목소리를 기준으로 시작하기').click());
  expect(voiceRow()).toContain('발표 처음 목소리로');
  expect(findButton('Take 4 시작하기').disabled).toBe(false);
  await act(async () => findButton('Take 4 시작하기').click());
  expect(sentBody()).toMatchObject({ base_level_db: null });
  expect(recallBaseVoice({ deviceId: 'mic-a', autoGainOff: true }, performance.now())).toBeNull();
});

it('다시 읽기 안내는 거리를 바꾸게 하지 않는다', async () => {
  fixture.voiceStatus = 'SHORT';
  await render();
  expect(voiceRow()).not.toContain('가까이');
});

it('목소리 기준을 보냈는데 끝내 응답을 못 받으면 막지 않고 무대에 알리게 넘긴다', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
  // 다시 보내도 같은 답(4xx)이라 바로 멈춥니다 — 다시 보내기는 sendCalibration.test.ts 가 봅니다
  fixture.postCalibration.mockRejectedValue(new ApiFailure(409, 'TAKE_ALREADY_STARTED'));
  await render();
  await act(async () => findButton('Take 4 시작하기').click());
  expect(host.querySelector('[data-unconfirmed]')!.getAttribute('data-unconfirmed')).toBe('true');
});

it('탈출구로 시작했으면 보내지 못해도 무대에 알리지 않는다 — 원래 발표 처음 목소리로 잡습니다', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
  fixture.postCalibration.mockRejectedValue(new ApiFailure(409, 'TAKE_ALREADY_STARTED'));
  fixture.voiceStatus = 'SHORT';
  fixture.voiceShorts = 2;
  await render();
  await act(async () => findButton('발표 처음 목소리를 기준으로 시작하기').click());
  await act(async () => findButton('Take 4 시작하기').click());
  expect(host.querySelector('[data-unconfirmed]')!.getAttribute('data-unconfirmed')).toBe('false');
});

// #1184 리뷰 [P1] — 보정 요청이 답 없이 걸려도 Take 를 만든 뒤 무대로 넘어갑니다
it('보정 요청이 답 없이 걸리면 제한 시간에 끊고 다시 보낸 뒤, 끝내 못 받아도 무대로 넘어가 알린다', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
  try {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const signals: AbortSignal[] = [];
    fixture.postCalibration.mockImplementation(
      (_pitchId: string, _takeId: string, _body: unknown, signal: AbortSignal) => {
        signals.push(signal);
        return new Promise((_resolve, reject) => {
          signal.addEventListener('abort', () =>
            reject(new DOMException('The operation was aborted.', 'AbortError')),
          );
        });
      },
    );
    await render();
    await act(async () => findButton('Take 4 시작하기').click());
    expect(fixture.create).toHaveBeenCalledOnce();
    expect(host.textContent).not.toContain('연습 시작');
    await act(async () => vi.advanceTimersByTimeAsync(20_000));
    expect(signals).toHaveLength(3);
    expect(signals.every((s) => s.aborted)).toBe(true);
    expect(host.querySelector('[data-unconfirmed]')!.getAttribute('data-unconfirmed')).toBe('true');
  } finally {
    vi.useRealTimers();
  }
});
