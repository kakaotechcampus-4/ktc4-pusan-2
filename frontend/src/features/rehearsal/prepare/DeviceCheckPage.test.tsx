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
import { takeScreenSnapshot } from '@/shared/lib/chatScreen';

const fixture = vi.hoisted(() => ({
  live: true,
  micOk: true,
  gazeStatus: 'DONE',
  gazeAdvice: null as CalibrationFailReason | null,
  deviceError: null as DeviceError | null,
  voiceStatus: 'IDLE',
  data: null as unknown as PrepareResponse,
  preparePending: false,
  prepareError: false,
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
    heardSec: null,
    levelDb: null,
    start: fixture.voiceStart,
    cancel: vi.fn(),
    finish: vi.fn(),
  }),
}));
vi.mock('@/shared/api/prepare', () => ({
  usePrepare: () => ({
    data: fixture.data,
    isPending: fixture.preparePending,
    isError: fixture.prepareError,
    error: fixture.prepareError ? new Error('prepare failed') : null,
  }),
  useLatestSlide: () => ({
    ...fixture.latestSlide,
    isFetching: fixture.latestFetching,
    refetch: fixture.refetchLatest,
  }),
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
let originalActivation: PropertyDescriptor | undefined;

/** SPA에서 홈 버튼을 누르고 넘어온 것처럼 사용자 조작이 있었던 브라우저 상태입니다. */
function activatePage() {
  Object.defineProperty(navigator, 'userActivation', {
    configurable: true,
    value: { hasBeenActive: true },
  });
}

beforeEach(() => {
  originalActivation = Object.getOwnPropertyDescriptor(navigator, 'userActivation');
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
  fixture.preparePending = false;
  fixture.prepareError = false;
  fixture.micOk = true;
  fixture.gazeStatus = 'DONE';
  fixture.gazeAdvice = null;
  fixture.deviceError = null;
  fixture.voiceStatus = 'IDLE';
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
    tutorial_block: null,
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
  if (originalActivation) {
    Object.defineProperty(navigator, 'userActivation', originalActivation);
  } else {
    Reflect.deleteProperty(navigator, 'userActivation');
  }
  vi.unstubAllEnvs();
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});
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
          <Route path="/takes/:takeId/rehearsal" element={<p>연습 시작</p>} />
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

it.each([
  ['TUTORIAL_REPORT_USED', '튜토리얼 리포트는 한 번만 받을 수 있어요', '/pitch/new'],
  ['TUTORIAL_ANALYSIS_RUNNING', '앞 연습을 분석하고 있어요', '/takes?pitch=p1'],
] as const)(
  '튜토리얼 샘플이 막혔으면(%s) 처음부터 알리고, 장비가 준비돼도 시작하지 않는다',
  async (block, text, href) => {
    activatePage();
    fixture.data = { ...fixture.data, tutorial_block: block };
    await render();
    // 홈에서 버튼을 눌러 온 경우에도 자동 장치 권한 요청이 발생하면 안 됩니다.
    expect(fixture.request).not.toHaveBeenCalled();
    const notice = host.querySelector('[data-tutorial-block]')!;
    expect(notice.getAttribute('role')).toBe('alert');
    expect(notice.textContent).toContain(text);
    expect(notice.querySelector('a')?.getAttribute('href')).toBe(href);
    // 장비 · 시선 기준이 모두 준비된 상태(fixture 기본값)에서도 막습니다
    expect(findButton('Take 4 시작하기').disabled).toBe(true);
    expect(fixture.create).not.toHaveBeenCalled();
    // 장치 변경도 권한 요청 경로이므로 같은 제한을 따릅니다.
    const deviceSelectors = [...host.querySelectorAll<HTMLSelectElement>('select')];
    expect(deviceSelectors).toHaveLength(2);
    expect(deviceSelectors.every((select) => select.disabled)).toBe(true);
  },
);

it('튜토리얼 재연습이 막히면 장치를 켜는 수동 버튼도 요청하지 않는다', async () => {
  fixture.data = { ...fixture.data, tutorial_block: 'TUTORIAL_REPORT_USED' };
  fixture.live = false;
  fixture.micOk = false;
  await render();
  const openDevices = findButton('카메라·마이크 켜기');
  expect(openDevices.disabled).toBe(true);
  await act(async () => openDevices.click());
  expect(fixture.request).not.toHaveBeenCalled();
});

it('준비 정보를 받은 뒤에만 자동 카메라 권한을 한 번 요청한다', async () => {
  activatePage();
  const prepared = fixture.data;
  fixture.data = null as unknown as PrepareResponse;
  fixture.preparePending = true;
  fixture.live = false;

  await render();
  expect(fixture.request).not.toHaveBeenCalled();

  fixture.data = { ...prepared, tutorial_block: null };
  fixture.preparePending = false;
  await render();
  expect(fixture.request).toHaveBeenCalledTimes(1);

  await render();
  expect(fixture.request).toHaveBeenCalledTimes(1);
});

it('준비 정보 조회 실패 시 자동·수동 장치 권한 요청을 막는다', async () => {
  activatePage();
  fixture.data = null as unknown as PrepareResponse;
  fixture.prepareError = true;
  fixture.live = false;

  await render();
  expect(fixture.request).not.toHaveBeenCalled();
  const openDevices = findButton('카메라·마이크 켜기');
  expect(openDevices.disabled).toBe(true);
  await act(async () => openDevices.click());
  expect(fixture.request).not.toHaveBeenCalled();
});

it('막히지 않은 샘플이나 일반 피치에는 튜토리얼 안내가 없다', async () => {
  await render();
  expect(host.querySelector('[data-tutorial-block]')).toBeNull();
  expect(findButton('Take 4 시작하기').disabled).toBe(false);
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
