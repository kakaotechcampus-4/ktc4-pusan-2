import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router';
import type { PracticeCombo, RehearsalTicket } from '@/types/api';
import { SILENCE_END_NOTICE } from '@/shared/lib/autoEnd';
import { useShallow } from 'zustand/react/shallow';
import { DEVICE_ERROR_MESSAGE, useCameraStream, type DeviceError } from '../media/useCameraStream';
import { postCalibration, useCreateTake, useLatestSlide, usePrepare } from '@/shared/api/prepare';
import { setPrepareContext, setSessionTicket, setTakeId, startSession } from '../lib/db';
import { noteWriteFailure } from '../lib/writeFailures';
import { toCalibrationRequest } from '../lib/calibrationRequest';
import { codeMessage, toMessage } from '@/shared/api/errorMessage';
import { GazeSetupView } from './GazeSetupView';
import { CheckCard } from './CheckCard';
import { AudioInputMeter } from './AudioInputMeter';
import { LevelBar } from '../media/LevelBar';
import { Button } from '@/shared/ui/Button';
import { ScreenFrame } from './ScreenFrame';
import { ScriptModeChoice } from './ScriptModeChoice';
import { ReplaceTakeDialog } from './ReplaceTakeDialog';
import { activeTakeOrNone, endActiveTake } from './activeTake';
import type { ActiveTake } from '@/types/take';
import { MissionCard } from './MissionCard';
import { DEFAULT_SCRIPT_MODE, practiceModeFor, usePrepareStore } from './prepareStore';
import {
  fromPrepare,
  newerComboNotice,
  newerScriptNotice,
  chosenSlide,
  olderSlideNotice,
  prepareProblem,
  takeChoice,
} from './prepareInfo';
import { CALIBRATION_FAIL_MESSAGE, useGazeSetup, type GazeSetupStatus } from './useGazeSetup';
import { useBaseVoice, type BaseVoiceStatus } from './useBaseVoice';
import { useMicLevel } from '../media/useMicLevel';
import type { ReadStage, ShortReason } from '../media/baseVoice';
import { useVideoStream } from '../media/useVideoStream';
import { useChatScreen } from '@/shared/lib/chatScreen';
import { deviceScreen } from './chatScreen';

/**
 * 평소 목소리로 읽을 문단. 발표를 여는 말투입니다. 처음부터 한 번에 보여 주고, 다 읽으면 사용자가
 * "다 읽었어요"를 누릅니다. '말하는 중'은 실제 읽은 시간의 절반 남짓만 잡혀서(`loudness.ts`), 문장 하나로는
 * 5초를 못 채울 수 있어 두 문장(평소 속도로 10초 안팎)으로 둡니다. 더 줄이면 다 읽어도 모자랄 수 있습니다.
 */
const BASE_VOICE_SENTENCES = [
  '안녕하세요. 지금부터 발표를 시작하겠습니다. 오늘 준비한 내용을 차근차근 말씀드리겠습니다.',
  '먼저 이번 발표에서 다룰 문제를 소개하고, 이어서 해결 방법을 설명드리겠습니다.',
];

/**
 * 이 화면의 시계. 음량 기록은 무대 시계가 없으니 페이지 시계로 쌓고, 읽는 동안의 구간만 꺼냅니다.
 * 모듈 상수라 정체성이 고정입니다 — 렌더마다 새로 만들면 계량기 쪽 이펙트가 다시 돕니다
 */
const pageClock = () => performance.now();

/**
 * 점검 안내 한 줄.
 *
 * **쓴 순서가 곧 우선순위입니다.** 여럿이 동시에 어긋나 있어도 사용자가 지금 할 수 있는
 * 일은 하나뿐이라, 가장 앞을 막고 있는 것만 말합니다.
 *
 * 마이크가 시선 기준보다 앞인 이유 — 시선 기준은 몇 초 동안 화면을 보며 잡아야 하는데,
 * 마이크가 안 되면 기준을 다 잡아도 시작할 수 없습니다. 바로 고칠 수 있는 것부터 말합니다.
 */
function deviceCheckHint({
  deviceError,
  live,
  micOk,
  calStatus,
  calUnavailable,
  calPoor,
  voiceStatus,
}: {
  deviceError: DeviceError | null;
  live: boolean;
  micOk: boolean;
  calStatus: GazeSetupStatus;
  /** 시선 분석을 켤 수 없는 이유와 할 일. UNAVAILABLE 일 때만 있습니다 */
  calUnavailable: string | null;
  /** 기준은 잡혔지만 품질이 낮음 — 막지 않고 권하기만 합니다 */
  calPoor: boolean;
  /** 평소 목소리 읽기 — 선택이라 다른 점검이 다 끝난 뒤에만 말합니다 */
  voiceStatus: BaseVoiceStatus;
}): string {
  // 읽는 중이면 그 안내가 먼저입니다 — 사용자가 지금 하고 있는 일입니다
  if (voiceStatus === 'READING')
    return '아래 문단을 평소 발표하는 목소리로 읽고, 다 읽으면 "다 읽었어요"를 눌러 주세요';
  if (deviceError) return DEVICE_ERROR_MESSAGE[deviceError];
  // 카메라 권한을 묻기 전에 알립니다 — 시선 분석을 못 쓰면 카메라를 켜도 시작할 수 없습니다
  if (calStatus === 'UNAVAILABLE' && calUnavailable) return calUnavailable;
  if (!live) return '카메라를 켜야 점검을 시작할 수 있습니다';
  if (!micOk) return '마이크에 대고 한 마디 해보세요';
  if (calStatus === 'LOADING') return '시선 분석을 준비하는 중…';
  if (calStatus === 'FAILED')
    return '기준을 잡지 못했어요. 얼굴이 화면 안에 있는지 보고 다시 해주세요';
  if (calStatus !== 'DONE') return '시선 기준을 먼저 잡아야 연습을 시작할 수 있습니다';
  if (calPoor) return '시선 기준이 흐릿해요. 다시 잡으면 더 정확해지지만, 이대로 시작해도 됩니다';
  if (voiceStatus !== 'DONE')
    return '점검이 끝났어요. 평소 목소리를 재 두면 작게 말할 때 더 정확히 알려 드려요 (선택)';
  return '점검이 끝났어요';
}

/**
 * 시작을 누른 뒤의 안내. 다른 곳에서 발표 중인 Take 를 끝낼지 묻는 동안은 아직 여는 중이
 * 아닙니다 (#719)
 */
function startingHint(replacing: { busy: boolean } | null): string {
  if (!replacing) return '연습을 여는 중…';
  if (replacing.busy) return '다른 곳의 연습을 끝내는 중…';
  return '다른 곳에서 진행 중인 연습을 끝낼지 골라 주세요';
}

/** 평소 목소리 항목의 문구 */
function baseVoiceLabel(status: BaseVoiceStatus, reason: ShortReason | null): string {
  switch (status) {
    case 'IDLE':
      return '아직 재지 않았어요';
    case 'READING':
      return '크기 재는 중';
    case 'DONE':
      return '확인됨';
    case 'SHORT':
      return SHORT_TITLE[reason ?? 'BRIEF'];
  }
}

/** 다시 읽어야 할 때 항목 문구와 안내. 이유마다 할 일이 달라 나눕니다 (`baseVoice.ts` `ShortReason`) */
const SHORT_TITLE: Record<ShortReason, string> = {
  QUIET: '잘 들리지 않았어요',
  BRIEF: '조금 더 읽어 주세요',
};

const SHORT_GUIDE: Record<ShortReason, string> = {
  QUIET:
    '말소리로 들리지 않았어요. 목소리가 작거나 주변이 시끄러울 수 있어요. "다시 읽기"를 누르고, 마이크에 조금 더 가까이에서 평소 목소리로 읽어 주세요.',
  BRIEF:
    '말한 시간이 모자랐어요. "다시 읽기"를 누르고, 문단을 끝까지 읽은 뒤에 "다 읽었어요"를 눌러 주세요.',
};

/** 평소 목소리 항목의 버튼 문구. 읽는 중에는 그만두기입니다 */
function baseVoiceActionText(status: BaseVoiceStatus): string {
  switch (status) {
    case 'IDLE':
      return '목소리 재기';
    case 'READING':
      return '그만두기';
    case 'DONE':
    case 'SHORT':
      return '다시 읽기';
  }
}

/**
 * 시선 기준점 항목의 버튼 문구.
 *
 * 상태 하나에 대한 분기라 switch 로 둡니다. 상태가 늘면 **여기서 컴파일이 깨져서**
 * 빠뜨린 갈래가 바로 드러납니다 — 삼항으로 이어 붙이면 조용히 마지막 갈래로 떨어집니다.
 *
 * IDLE·FAILED 만 `live` 를 함께 봅니다. 카메라가 없으면 눌러도 시작되지 않는데
 * 버튼이 '누르면 시작'이라고 말하면 안 됩니다.
 */
function calibrationActionText(status: GazeSetupStatus, live: boolean): string {
  switch (status) {
    case 'DONE':
      return '다시 잡기';
    case 'RUNNING':
      return '잡는 중…';
    case 'LOADING':
      return '준비 중…';
    case 'UNAVAILABLE':
      return '사용 불가';
    case 'IDLE':
    case 'FAILED':
      return live ? '시선 기준 잡기' : '카메라 먼저';
  }
}

/** 시선 기준점 항목의 문구 */
function calibrationLabel(cal: Pick<ReturnType<typeof useGazeSetup>, 'status' | 'advice'>): string {
  switch (cal.status) {
    case 'UNAVAILABLE':
      return '시선 분석 사용 불가';
    case 'FAILED':
      return '다시 필요';
    case 'DONE':
      return cal.advice ? '3/3 완료 · 품질 낮음' : '3/3 완료';
    case 'LOADING':
      return '시선 분석 준비 중';
    case 'IDLE':
      return '기준을 잡아 주세요';
    case 'RUNNING':
      return '기준 잡는 중';
  }
}

/**
 * 09 시작 전 세팅 — 리허설 바로 앞. **Take가 생기는 유일한 화면입니다.**
 *
 * 전에는 장치 점검(05)과 리허설 준비(06)가 따로였는데, 시안 09 가 둘을 한 화면으로
 * 그리면서 합쳤습니다. 대본 표시·평가기준이 준비 화면에만 있던 것들입니다.
 *
 * ★ Take 는 아래 `start()` 에서만 생깁니다 (CLAUDE.md 8번). 화면에 들어오는 것만으로는
 *   만들지 않습니다 — 점검하다 그만둔 만큼 빈 Take 가 쌓이고 takeNumber 가 실제
 *   연습 횟수와 어긋납니다. 이 함수를 다른 화면으로 복사하지 마세요.
 *
 * 점검 셋 중 둘은 장치(카메라·마이크)고 하나는 시선 기준점입니다. 셋 다 통과해야 시작합니다.
 */
export function DeviceCheckPage() {
  const { pitchId = '' } = useParams();
  // 피치 생성에서 매핑을 저장하며 고른 조합. 홈에서 바로 왔거나 새로고침했으면 없습니다
  const location = useLocation();
  const combo = (location.state as { practice?: PracticeCombo } | null)?.practice ?? null;
  const navigate = useNavigate();
  const prepare = usePrepare(pitchId);
  const info = useMemo(() => (prepare.data ? fromPrepare(prepare.data) : null), [prepare.data]);
  // 튜토리얼 샘플로는 더 연습할 수 없음 — 리포트를 받았거나 앞 연습을 분석하는 중 (#931). 카메라 점검 ·
  // 시선 보정을 다 하고 나서 시작 단계의 409 로 막히지 않게 처음부터 알리고 시작을 막습니다.
  // 피치 생성에서 넘겨받은 조합(`combo`)이 있어도 봅니다. 그 사이 바뀌면 Take 생성의 409 가 막습니다
  const tutorialBlock = prepare.data?.tutorial_block ?? null;
  // 이번에 쓸 자료가 지난 자료인지 견줄 최신 자료 (멘토 리뷰 #923). 조합을 넘겨받았어도 읽습니다 — 넘겨받은 뒤
  // 다른 탭에서 새 PDF 를 올렸을 수 있습니다 (#1099 리뷰)
  const latestSlide = useLatestSlide(pitchId, true);

  const { stream, error: deviceError, request } = useCameraStream();
  const { videoRef, live } = useVideoStream(stream, 'device-check');
  // 무음 경고는 평소 목소리를 **읽는 동안만**, 읽기를 누른 때부터 셉니다. 읽기 전에는 말할 이유가 없어
  // 페이지를 연 뒤로 세면 가만히 있는 사람에게 경고가 떴습니다. 읽기 상태는 useBaseVoice 뒤에서 이 ref 에
  // 적습니다 — useMicLevel 이 voiceRecord 를 넘겨야 해서 먼저 불리기 때문입니다
  const readingSinceRef = useRef<number | null>(null);
  const silenceFrom = useCallback(() => readingSinceRef.current, []);
  const { meterRef, echoMeterRef, rowRef, silentRef, micOk, audioState, meterError, voiceRecord } =
    useMicLevel(stream, { clock: pageClock, silenceFrom });
  // 평소 목소리 크기. 선택 단계라 시작을 막지 않습니다 — 안 재면 AI 가 Take 첫 발화로 잡습니다
  const baseVoice = useBaseVoice({ stream, clock: pageClock, voiceRecord });
  const reading = baseVoice.status === 'READING';
  useEffect(() => {
    readingSinceRef.current = reading ? performance.now() : null;
  }, [reading]);
  // 대본 표시는 준비 화면과 **같은 스토어**를 씁니다 — 여기서 고른 것이 그대로 이어집니다.
  // 한 번에 묶어 꺼내므로 useShallow 가 필요합니다. 없으면 셀렉터가 매번 새 객체를
  // 돌려줘서 값이 그대로여도 바뀐 것으로 보고 무한히 다시 그립니다
  //
  // 대본 표시는 **이 피치에서 고른 것일 때만** 씁니다. 다른 피치에서 고른 값은 `enterPitch` 가
  // 비우는데, 비우기 전 첫 그림에서도 앞 피치의 선택(예: 실전 모드)이 보이지 않게 여기서도 거릅니다
  const {
    scriptMode,
    setScriptMode,
    calibration,
    scriptModeTouched,
    setChosenDevices,
    enterPitch,
  } = usePrepareStore(
    useShallow((s) => {
      const mine = s.scriptModePitchId === pitchId;
      return {
        scriptMode: mine ? s.scriptMode : DEFAULT_SCRIPT_MODE,
        setScriptMode: s.setScriptMode,
        calibration: s.calibration,
        scriptModeTouched: mine && s.scriptModeTouched,
        setChosenDevices: s.setDevices,
        enterPitch: s.enterPitch,
      };
    }),
  );
  const cal = useGazeSetup({ stream, videoRef, live });
  // 대화창에 지금 점검 상태를 알립니다 — 보내는 순간에만 읽고, 장치 원본 · 식별자는 싣지 않습니다 (12-4 F2)
  useChatScreen('/pitch/:pitchId/device-check', () =>
    deviceScreen({
      live,
      hasStream: stream !== null,
      micOk,
      gazeStatus: cal.status,
      failReason: cal.failReason,
    }),
  );
  const createTake = useCreateTake();

  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  /** 다른 곳에서 발표 중인 Take 를 끝낼지 묻는 중. `busy` 면 끝내는 중입니다 (#719) */
  const [replacing, setReplacing] = useState<{ take: ActiveTake; busy: boolean } | null>(null);
  /**
   * 이 화면을 떠났다 (뒤로 가기 등). 묻거나 끝내는 요청은 화면이 닫혀도 마저 끝나니, 그 뒤에
   * Take 를 만들어 리허설로 끌고 가지 않도록 봅니다
   */
  const leftPage = useRef(false);
  useEffect(() => {
    leftPage.current = false;
    return () => {
      leftPage.current = true;
    };
  }, []);

  /**
   * 확인 창을 닫은 뒤(취소 · Esc · 끝내기 실패) 초점을 시작 버튼으로 돌립니다. 브라우저에 맡기면
   * 안 됩니다 — 창이 열려 있는 동안 시작 버튼이 비활성이라, 브라우저가 초점을 body 로 옮겨 둡니다.
   * 비활성이 풀린 뒤에야 초점을 받으니 창이 내려가고 starting 이 false 가 된 다음에 줍니다
   */
  const startButton = useRef<HTMLButtonElement>(null);
  // [최신으로]로 바꾼 조합 — 바꿨다는 것을 한 번 알립니다
  const [switchedTo, setSwitchedTo] = useState<string | null>(null);
  // [지난 자료로 연습]을 누른 자료 id. 준비 정보가 다른 자료를 고르면 다시 묻습니다
  const [olderSlideOkFor, setOlderSlideOkFor] = useState<string | null>(null);
  // 가장 최근 자료를 확인하지 못한 채 [이 자료로 연습]을 누른 자료 id. 준비 정보가 다른 자료를 고르면 다시 묻습니다
  const [uncheckedOkFor, setUncheckedOkFor] = useState<string | null>(null);
  // [최신으로] · [다시 확인] 뒤 최신 자료 확인이 끝나면 초점을 둘 곳을 정합니다 (아래 이펙트)
  const focusAfterCheck = useRef(false);
  // 고르기가 필요한 안내 상자 — 확인 뒤 시작이 막혔으면 초점을 여기로 옮깁니다
  const choiceBox = useRef<HTMLDivElement>(null);
  const focusStartAfterClose = useRef(false);
  useEffect(() => {
    if (replacing || starting || !focusStartAfterClose.current) return;
    focusStartAfterClose.current = false;
    startButton.current?.focus();
  }, [replacing, starting]);

  // 지금 열려 있는 트랙이 곧 선택값입니다. 따로 상태로 들고 있으면
  // 브라우저가 다른 장치를 열었을 때 화면과 실제가 갈라집니다
  const videoId = stream?.getVideoTracks()[0]?.getSettings().deviceId ?? '';
  const audioId = stream?.getAudioTracks()[0]?.getSettings().deviceId ?? '';

  // 앞 화면의 CTA를 누르고 왔으면 사용자 조작이 살아 있어 바로 열립니다 (SPA라 같은 문서).
  // 이 주소를 직접 열었으면 조작이 없으므로 화면 안의 버튼을 눌러야 합니다 —
  // 맥락 없이 권한 팝업부터 띄우면 대부분 '차단'을 누릅니다.
  const askedRef = useRef(false);
  useEffect(() => {
    if (askedRef.current) return;
    askedRef.current = true;
    const activation = (navigator as Navigator & { userActivation?: { hasBeenActive: boolean } })
      .userActivation;
    if (activation?.hasBeenActive) request().catch(() => undefined);
  }, [request]);

  // 다른 피치에서 왔으면 대본 표시를 비웁니다. 아래 서버 기본값 이펙트보다 **먼저** 돌아야 합니다 —
  // 늦으면 앞 피치의 "사용자가 고름" 표시를 보고 이 피치의 기본값을 건너뜁니다
  useEffect(() => {
    if (pitchId) enterPitch(pitchId);
  }, [pitchId, enterPitch]);

  // 서버 기본값은 **사용자가 고르기 전에만** 넣습니다. 조건 없이 덮으면 응답이 늦게
  // 올 때 사용자가 고른 것이 되돌아갑니다. FULL 이 올 수 있어 3단계로 접습니다.
  // 지난번이 OFF 였으면 실전 모드가 미리 골라집니다 (practiceModeFor 의 대가 참고)
  useEffect(() => {
    if (info && !scriptModeTouched) setScriptMode(info.defaultScriptMode, false);
  }, [info, scriptModeTouched, setScriptMode]);

  // 장치 이름은 권한을 받은 뒤에야 채워집니다. 그 전에는 label이 빈 문자열입니다
  useEffect(() => {
    if (!stream) return;
    navigator.mediaDevices
      .enumerateDevices()
      .then(setDevices)
      .catch(() => undefined);
  }, [stream]);

  const cameras = devices.filter((d) => d.kind === 'videoinput');
  const mics = devices.filter((d) => d.kind === 'audioinput');

  // 저장된 기준을 확인하는 중에는 열지 않습니다 — "완료"로 보이는데 기준이 없으면
  // 리허설에서 시선이 조용히 빠집니다
  //
  // ★ 피치 생성에서 조합을 받았으면 준비 정보(`/prepare`)를 기다리지 않습니다 — Take 에 필요한
  //   버전 id 가 조합에 다 있습니다. 없을 때(홈 · 새로고침)만 준비 정보로 정하고, 거기서 시작할 수
  //   없는 이유(버전 없음 · 쪽수 다름 · 불러오기 실패)가 있으면 그걸 먼저 알립니다
  const choice = takeChoice(combo, info);
  // 조합 없이 왔는데 준비 정보가 가장 최근 자료 대신 지난 자료를 골랐으면, 한 번 확인받기 전에는 시작하지 않습니다.
  // 최신 자료를 받는 동안에도 기다립니다 — 먼저 시작하면 확인 없이 지난 자료로 Take 가 생깁니다.
  const chosen = chosenSlide(info, combo);
  const olderSlide = olderSlideNotice(info, latestSlide.data ?? null, combo);
  const olderSlideBlocked = olderSlide !== null && olderSlideOkFor !== chosen?.id;
  const latestSlidePending = latestSlide.isPending;
  // 처음 받은 뒤 다시 받는 중(창으로 돌아옴 등)에도 시작하지 않습니다 — 받기 전 결과로는 지난 자료인지 모릅니다(#1099 리뷰).
  // 버튼은 `aria-disabled` 로만 막습니다. `disabled` 로 막으면 창으로 돌아올 때마다 쥐고 있던 초점이 body 로 빠집니다(#818)
  const latestRefreshing = latestSlide.isFetching && !latestSlide.isPending;
  // 버전 목록을 못 받았으면 지난 자료인지 알 수 없습니다. 연습을 막아 버리지는 않고, 다시 확인할지 이 자료로
  // 연습할지 한 번 고르게 합니다 — 그냥 열어 두면 확인 없이 지난 자료로 Take 가 생깁니다 (#1099 리뷰).
  // 준비 정보로 시작할 수 없으면(`prepareProblem`) 그 이유가 먼저라 묻지 않습니다
  const latestUnknown = chosen !== null && latestSlide.isError && latestSlide.data === undefined;
  const latestRechecking = latestUnknown && latestSlide.isFetching;
  const latestUnchecked = latestUnknown && uncheckedOkFor !== chosen?.id;
  const latestUncheckedNotice = latestUnchecked
    ? `가장 최근 자료를 확인하지 못해 지금 자료(v${chosen?.version})가 최신인지 알 수 없어요`
    : null;
  const prepareBlocked = (() => {
    if (tutorialBlock) return codeMessage(tutorialBlock);
    if (!combo) {
      if (prepare.isError) return `준비 정보를 불러오지 못했어요. ${toMessage(prepare.error)}`;
      const problem = info ? prepareProblem(info) : null;
      if (problem) return problem;
    }
    if (latestRechecking) return '가장 최근 자료를 다시 확인하는 중이에요.';
    if (latestUnchecked) return '가장 최근 자료를 다시 확인할지, 이 자료로 연습할지 골라 주세요.';
    return olderSlideBlocked ? '지난 자료로 연습할지, 매핑을 다시 확인할지 골라 주세요.' : null;
  })();
  const ready =
    !tutorialBlock &&
    live &&
    micOk &&
    cal.status === 'DONE' &&
    choice !== null &&
    !olderSlideBlocked &&
    !latestSlidePending &&
    !latestUnchecked &&
    !starting;

  /**
   * 시작 버튼. 사용자는 한 번에 연습 하나만 해서, 다른 창 · 기기에서 발표 중인 Take 가 있으면
   * 그것을 끝낼지 먼저 묻습니다 (#719). 없거나 묻지 못했으면 바로 `begin` 입니다.
   */
  const start = async () => {
    if (!choice || starting) return;
    setStarting(true);
    setStartError(null);
    const other = await activeTakeOrNone();
    if (leftPage.current) return;
    if (other) {
      setReplacing({ take: other, busy: false });
      return;
    }
    await begin();
  };

  /** 확인 창의 [그 연습 끝내고 시작]. 끝내지 못하면 시작하지 않습니다 — 새 연결이 거절됩니다 */
  const confirmReplace = async () => {
    if (!replacing || replacing.busy) return;
    setReplacing({ ...replacing, busy: true });
    try {
      await endActiveTake(replacing.take);
    } catch (e) {
      focusStartAfterClose.current = true;
      setReplacing(null);
      setStartError(`다른 곳의 연습을 끝내지 못했어요. ${toMessage(e)}`);
      setStarting(false);
      return;
    }
    if (leftPage.current) return;
    setReplacing(null);
    await begin();
  };

  const cancelReplace = () => {
    focusStartAfterClose.current = true;
    setReplacing(null);
    setStarting(false);
  };

  /**
   * ★ Take 는 여기서만 생깁니다. 이 함수를 다른 화면으로 복사하지 마세요.
   *
   * 순서가 중요합니다 - 세션(clientSessionId)이 먼저입니다. 그 값이 POST /takes 의
   * 멱등 키이자 IndexedDB 에 쌓일 모든 기록의 키입니다 (업로드 재시도도 같은 값).
   */
  const begin = async () => {
    // 확인 창을 거치는 동안 고를 조합이 사라졌을 수 있습니다. 시작 버튼을 잠근 채 두지 않습니다
    if (!choice) {
      setStarting(false);
      return;
    }

    // 드롭다운 값이 아니라 **실제로 열린 트랙**의 장치를 넘깁니다. 기본 장치로 통과했어도
    // 그 장치가 남아서, 그사이 OS 기본값이 바뀌어도 리허설이 같은 장치를 엽니다.
    // '' 는 undefined 로 — exact 에 빈 문자열을 걸면 OverconstrainedError 가 납니다
    setChosenDevices({
      videoDeviceId: videoId || undefined,
      audioDeviceId: audioId || undefined,
    });

    // 시안 09 - 대본 표시 하나로 연습 모드까지 정해집니다
    const practiceMode = practiceModeFor(scriptMode);

    try {
      const clientSessionId = await startSession();

      // ★ 이번 연습의 슬라이드 + 대본 조합이 Take 에 박힙니다 (BE TakeInitRequestDTO).
      //   세션 id 가 멱등 키라, 응답을 잃고 다시 보내도 Take 가 둘 생기지 않습니다 (#296)
      const take = await createTake.mutateAsync({
        pitchId,
        body: {
          mode: practiceMode,
          script_mode: scriptMode,
          presentation_version_id: choice.presentationVersionId,
          script_version_id: choice.scriptVersionId,
          client_session_id: clientSessionId,
        },
      });
      await setTakeId(clientSessionId, take.takeId);
      // 리허설을 새로고침하면 이 스토어가 비어서 세션 행에서 되살립니다. 못 적어도 발표는
      // 할 수 있으니 막지 않습니다 — 새로고침했을 때 시선이 빠지고 기본 장치가 열릴 뿐입니다
      const { calibration: savedCalibration, devices } = usePrepareStore.getState();
      await setPrepareContext(clientSessionId, {
        calibration: savedCalibration,
        devices,
      }).catch((err: unknown) => noteWriteFailure(clientSessionId, 'prepare', err));

      // 리허설이 무대를 열 때 쓸 값. 새로고침해도 남도록 세션에도 적어 둡니다
      const ticket: RehearsalTicket = {
        pitchId,
        title: combo?.title ?? info?.title ?? '',
        presentationVersionId: choice.presentationVersionId,
        scriptVersionId: choice.scriptVersionId,
        timeLimitSec: choice.timeLimitSec,
        mode: practiceMode,
        scriptMode,
        // 준비 정보의 번호는 예상값이라 그사이 다른 Take 가 생기면 어긋납니다. 실제로 매긴 번호를 씁니다
        takeNumber: take.takeNumber,
        // 실패해 장치 점검으로 돌아갈 때 이 조합을 다시 넘깁니다 (`deviceCheckLink`)
        practice: combo,
      };
      await setSessionTicket(clientSessionId, ticket);

      // 요약 수치만 갑니다. 보정 모델은 브라우저에 남습니다 (CLAUDE.md 1번)
      //
      // ★ 여기서 실패해도 연습은 엽니다. Take 는 이미 만들어졌으니, 여기서 멈추면 연습 없이
      //   빈 Take 만 남고 takeNumber 가 실제 횟수와 어긋납니다. 요약은 리포트의 참고값이라
      //   빠져도 발표 기록은 온전합니다 — 원인은 콘솔에 남깁니다
      if (calibration) {
        const body = toCalibrationRequest(calibration, {
          micDetected: micOk,
          baseLevelDb: baseVoice.levelDb,
        });
        await postCalibration(pitchId, take.takeId, body).catch((err: unknown) => {
          console.error('[장치 점검] 보정 요약을 보내지 못했습니다', err);
        });
      }

      navigate(
        practiceMode === 'EXAM' ? `/takes/${take.takeId}/exam` : `/takes/${take.takeId}/rehearsal`,
        {
          state: { clientSessionId, scriptMode, ticket },
        },
      );
    } catch (e) {
      // 여기서 멈춰야 합니다. 실패한 채로 넘어가면 takeId 없이 발표가 시작되고
      // 그 Take 는 어디에도 안 남습니다
      setStartError(toMessage(e));
      setStarting(false);
    }
  };

  // 시작에 실패했으면 그 문구가 먼저입니다 - 점검 안내보다 급합니다
  const hint =
    startError ??
    (starting
      ? startingHint(replacing)
      : (prepareBlocked ??
        deviceCheckHint({
          deviceError,
          live,
          micOk,
          calStatus: cal.status,
          calUnavailable: cal.unavailableMessage,
          calPoor: cal.advice !== null,
          voiceStatus: baseVoice.status,
        })));

  const takeNumber = info?.nextTakeNumber ?? 0;
  // 새 대본을 아직 못 써서 옛 대본으로 연습하면 알립니다. 피치 생성에서 고른 조합이면 해당 없습니다
  const notice = combo || !info ? null : newerScriptNotice(info);
  const subtitle = [choice?.label, notice].filter(Boolean).join(' · ');
  // 넘겨받은 조합보다 서버 최신 조합이 새 것이면 알리고 [최신으로]를 둡니다 (멘토 리뷰 #923)
  const newerCombo = combo && info ? newerComboNotice(combo, info) : null;
  // 조합을 지우면 준비 정보(서버 최신 조합)로 정합니다. 기록을 바꿔(replace) 새로고침 · 앞으로 가기에도 예전 조합이 돌아오지 않습니다
  const switchToLatest = () => {
    if (info?.presentation && info.script)
      setSwitchedTo(`자료 v${info.presentation.version} · 대본 v${info.script.version}`);
    // 누른 버튼이 안내와 함께 사라지면 초점이 body 로 빠집니다 (#818) — 사라지기 전에 본문으로 옮겨 두고,
    // 최신 자료 확인이 끝난 뒤 시작 버튼이나 새로 생긴 안내로 옮깁니다. 지금 시작 버튼에 두면 확인하는 동안
    // 버튼이 막혀 초점이 다시 빠집니다
    document.getElementById('main')?.focus();
    focusAfterCheck.current = true;
    navigate(location.pathname, { replace: true, state: null });
  };
  // [다시 확인] — 최신 자료를 다시 받습니다. 확인이 끝나 안내가 사라지면 초점을 옮깁니다
  const recheckLatest = () => {
    focusAfterCheck.current = true;
    latestSlide.refetch().catch(() => undefined);
  };
  // [이 자료로 연습] — 최신인지 모르는 채로 이 자료로 시작하도록 풉니다
  const keepUncheckedSlide = () => {
    const id = chosen?.id;
    if (!id) return;
    document.getElementById('main')?.focus();
    setUncheckedOkFor(id);
  };
  // [지난 자료로 연습] — 이 자료로 시작하도록 풉니다. 버튼이 사라지기 전에 초점을 옮기는 것은 [최신으로]와 같은 이유입니다
  const keepOlderSlide = () => {
    const id = chosen?.id;
    if (!id) return;
    document.getElementById('main')?.focus();
    setOlderSlideOkFor(id);
  };
  // 고르기 전에는 시작할 수 없는 안내. 둘은 함께 뜨지 않습니다 — 버전 목록을 받았으면 최신인지 알기 때문입니다
  const choiceNotice = latestUncheckedNotice ?? (olderSlideBlocked ? olderSlide : null);
  // 늘 그려 둔 빈 자리에 문구만 바꿔 넣어야 화면 낭독기가 읽습니다 — 버튼은 이 자리 밖에 둡니다.
  // 고르기가 필요한 안내가 먼저입니다. [최신으로] 뒤 새로 생긴 안내를 "바꿨어요"가 가리면 왜 막혔는지 모릅니다 (#1099 리뷰)
  const comboAnnouncement = (() => {
    if (choiceNotice)
      return switchedTo ? `최신 조합(${switchedTo})으로 바꿨어요. ${choiceNotice}` : choiceNotice;
    if (switchedTo) return `최신 조합(${switchedTo})으로 바꿨어요`;
    if (newerCombo) return newerCombo;
    if (olderSlide) return `지난 자료(v${chosen?.version})로 연습해요`;
    if (latestUnknown) return `지금 자료(v${chosen?.version})로 연습해요`;
    return '';
  })();
  // [최신으로] · [다시 확인] 뒤 확인이 끝나면 초점을 옮깁니다 — 고를 것이 생겼으면 그 안내로, 아니면 시작 버튼으로.
  // 이미 안내 안에 있으면(다시 확인이 또 실패) 그대로 둡니다
  useEffect(() => {
    if (!focusAfterCheck.current || latestSlide.isPending || latestSlide.isFetching) return;
    focusAfterCheck.current = false;
    const box = choiceBox.current;
    if (box) {
      if (!box.contains(document.activeElement)) box.focus();
      return;
    }
    const start = startButton.current;
    // 안내는 사라졌는데 다른 이유(장비 · 시선 보정)로 아직 시작할 수 없으면 본문으로 둡니다 — 사라진 버튼에 있던
    // 초점이 body 로 빠지지 않게 (#1099 리뷰)
    if (start && !start.disabled) start.focus();
    else document.getElementById('main')?.focus();
    // [최신으로]는 조합만 바꾸고 최신 자료를 다시 받지 않을 수 있어, 조합이 바뀐 때도 봅니다
  }, [combo, latestSlide.isPending, latestSlide.isFetching, choiceNotice]);
  const startLabel = practiceModeFor(scriptMode) === 'EXAM' ? '실전 모드로 시작' : '시작하기';

  return (
    <ScreenFrame
      title={info?.title ?? combo?.title ?? '불러오는 중…'}
      subtitle={subtitle}
      badge={`TAKE ${takeNumber || '—'}`}
      onBack={() => navigate(-1)}
      hint={hint}
      // 발표 중 말이 오래 없으면 서버가 끝냅니다. 목표 시간보다 일찍 끝나 놀라지 않게 미리 알립니다 (#925)
      note={SILENCE_END_NOTICE}
      actions={
        <>
          <Button
            ref={startButton}
            aria-busy={starting}
            disabled={!ready}
            aria-disabled={ready && latestRefreshing ? true : undefined}
            onClick={() => {
              if (!latestRefreshing) start().catch(() => undefined);
            }}
          >
            {takeNumber > 0 ? `Take ${takeNumber} ${startLabel}` : startLabel}
          </Button>
          {replacing && (
            <ReplaceTakeDialog
              take={replacing.take}
              busy={replacing.busy}
              onConfirm={() => confirmReplace().catch(() => undefined)}
              onCancel={cancelReplace}
            />
          )}
        </>
      }
    >
      <div className="prepare-content grid items-start gap-6 lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)] prepare-compact:gap-4">
        <div className="prepare-camera-column flex w-full min-w-0 flex-col gap-2 max-lg:mx-auto max-lg:max-w-[640px] prepare-low:max-w-[640px]">
          <div className="prepare-camera relative aspect-video w-full">
            <GazeSetupView
              frameRef={cal.frameRef}
              boxRef={cal.boxRef}
              videoRef={videoRef}
              live={live}
              status={cal.status}
              unavailableMessage={cal.unavailableMessage}
              overlay={cal.overlay}
              onCancel={cal.cancel}
            />
          </div>
          <AudioInputMeter meterRef={meterRef} />
        </div>

        <div className="prepare-controls flex min-w-0 flex-col gap-2.5 prepare-compact:gap-1.5 prepare-low:gap-1">
          {tutorialBlock && (
            <div
              role="alert"
              className="flex flex-wrap items-center justify-between gap-2 border border-coral bg-coral-wash px-3 py-2 text-sm text-ink"
              data-tutorial-block=""
            >
              <span>{codeMessage(tutorialBlock)}</span>
              <Link
                to={
                  tutorialBlock === 'TUTORIAL_REPORT_USED'
                    ? '/pitch/new'
                    : `/takes?pitch=${encodeURIComponent(pitchId)}`
                }
                className="inline-flex min-h-8 shrink-0 items-center border border-ink bg-panel px-2.5 text-xs font-semibold hover:bg-cream"
              >
                {tutorialBlock === 'TUTORIAL_REPORT_USED' ? '새 피치 만들기' : 'Take 기록 보기'}
              </Link>
            </div>
          )}
          <p role="status" className="sr-only">
            {comboAnnouncement}
          </p>
          {newerCombo && (
            <div
              className="flex flex-wrap items-center justify-between gap-2 border border-coral bg-coral-wash px-3 py-2 text-sm text-ink"
              data-newer-combo=""
            >
              <span>{newerCombo}</span>
              <button
                type="button"
                disabled={starting}
                onClick={switchToLatest}
                className="inline-flex min-h-8 shrink-0 items-center border border-ink bg-panel px-2.5 text-xs font-semibold hover:bg-cream disabled:cursor-default disabled:border-dashed disabled:border-line-strong"
              >
                최신으로
              </button>
            </div>
          )}
          {latestUncheckedNotice && (
            <div
              ref={choiceBox}
              tabIndex={-1}
              className="flex flex-wrap items-center justify-between gap-2 border border-coral bg-coral-wash px-3 py-2 text-sm text-ink"
              data-latest-unchecked=""
            >
              <span>{latestUncheckedNotice}</span>
              <span className="flex shrink-0 gap-1.5">
                {/* 다시 받는 동안에도 막지 않습니다 — 막으면 누른 버튼의 초점이 body 로 빠집니다. 같은 요청은 한 번만 갑니다 */}
                <button
                  type="button"
                  disabled={starting}
                  aria-busy={latestRechecking}
                  onClick={recheckLatest}
                  className="inline-flex min-h-8 items-center border border-ink bg-ink px-2.5 text-xs font-semibold text-panel disabled:cursor-default disabled:border-dashed disabled:border-line-strong"
                >
                  {latestRechecking ? '확인하는 중…' : '다시 확인'}
                </button>
                <button
                  type="button"
                  disabled={starting}
                  onClick={keepUncheckedSlide}
                  className="inline-flex min-h-8 items-center border border-ink bg-panel px-2.5 text-xs font-semibold hover:bg-cream disabled:cursor-default disabled:border-dashed disabled:border-line-strong"
                >
                  이 자료로 연습
                </button>
              </span>
            </div>
          )}
          {olderSlideBlocked && (
            <div
              ref={choiceBox}
              tabIndex={-1}
              className="flex flex-wrap items-center justify-between gap-2 border border-coral bg-coral-wash px-3 py-2 text-sm text-ink"
              data-older-slide=""
            >
              <span>{olderSlide}</span>
              <span className="flex shrink-0 gap-1.5">
                <button
                  type="button"
                  disabled={starting}
                  onClick={() => navigate(`/pitch/${pitchId}/edit`)}
                  className="inline-flex min-h-8 items-center border border-ink bg-ink px-2.5 text-xs font-semibold text-panel disabled:cursor-default disabled:border-dashed disabled:border-line-strong"
                >
                  매핑 확인하기
                </button>
                <button
                  type="button"
                  disabled={starting}
                  onClick={keepOlderSlide}
                  className="inline-flex min-h-8 items-center border border-ink bg-panel px-2.5 text-xs font-semibold hover:bg-cream disabled:cursor-default disabled:border-dashed disabled:border-line-strong"
                >
                  지난 자료로 연습
                </button>
              </span>
            </div>
          )}
          <CheckCard
            title="장비 준비"
            action={
              !live && (
                <button
                  type="button"
                  className="prepare-action inline-flex min-h-8 shrink-0 items-center justify-center gap-1 border border-ink bg-panel px-2.5 py-1 text-xs leading-[18px] font-semibold text-ink hover:bg-cream disabled:pointer-events-none disabled:cursor-default disabled:border-dashed disabled:border-line-strong disabled:bg-cream disabled:text-ink prepare-low:min-h-7 prepare-low:py-0.5 prepare-action-primary bg-coral-deep! text-white! hover:bg-ink! disabled:bg-cream! disabled:text-ink! disabled:border-line-strong!"
                  disabled={starting}
                  onClick={() => request().catch(() => undefined)}
                >
                  카메라·마이크 켜기
                </button>
              )
            }
            rows={[
              {
                id: 'camera',
                name: '카메라',
                label: live ? '연결됨' : '영상 없음',
                done: live,
                selection: (
                  <DeviceSelect
                    label="카메라"
                    value={videoId}
                    options={cameras}
                    fallback="기본 카메라"
                    disabled={starting || cal.overlay || baseVoice.status === 'READING'}
                    onChange={(id) =>
                      request({ videoDeviceId: id, audioDeviceId: audioId }).catch(() => undefined)
                    }
                  />
                ),
              },
              {
                id: 'mic',
                name: '마이크',
                label: <span ref={rowRef}>{stream ? '마이크 입력 확인 중' : '연결 전'}</span>,
                done: micOk,
                selection: (
                  <DeviceSelect
                    label="마이크"
                    value={audioId}
                    options={mics}
                    fallback="기본 마이크"
                    disabled={starting || cal.overlay || baseVoice.status === 'READING'}
                    onChange={(id) =>
                      request({ videoDeviceId: videoId, audioDeviceId: id }).catch(() => undefined)
                    }
                  />
                ),
              },
              {
                id: 'gaze',
                name: '시선 기준',
                label: calibrationLabel(cal),
                done: cal.status === 'DONE',
                action: {
                  text: calibrationActionText(cal.status, live),
                  onClick: cal.start,
                  disabled:
                    !live ||
                    starting ||
                    baseVoice.status === 'READING' ||
                    ['LOADING', 'RUNNING', 'UNAVAILABLE'].includes(cal.status),
                  primary: cal.status === 'IDLE' || cal.status === 'FAILED',
                },
              },
              {
                id: 'voice',
                name: '평소 목소리',
                label: stream
                  ? baseVoiceLabel(baseVoice.status, baseVoice.reason)
                  : '마이크 연결 후 가능',
                done: baseVoice.status === 'DONE',
                optional: true,
                action: {
                  text: baseVoiceActionText(baseVoice.status),
                  onClick: baseVoice.status === 'READING' ? baseVoice.cancel : baseVoice.start,
                  disabled: !stream || starting || cal.overlay,
                },
              },
            ]}
          >
            {baseVoice.status !== 'IDLE' && (
              <BaseVoicePanel
                status={baseVoice.status}
                stage={baseVoice.stage}
                onFinish={baseVoice.finish}
                reason={baseVoice.reason}
                heardSec={baseVoice.heardSec}
                echoMeterRef={echoMeterRef}
              />
            )}
            <div
              className="prepare-notices text-xs leading-[18px] wrap-anywhere [&>p]:mt-1.5"
              aria-live="polite"
            >
              {deviceError && <p>{DEVICE_ERROR_MESSAGE[deviceError]}</p>}
              {cal.advice && (
                <p>
                  시선 기준이 흐릿해요. {CALIBRATION_FAIL_MESSAGE[cal.advice]} 이대로 시작해도 돼요.
                </p>
              )}
              {cal.status === 'FAILED' && (
                <p>{CALIBRATION_FAIL_MESSAGE[cal.failReason ?? 'ENGINE_ERROR']}</p>
              )}
              <p className="prepare-silence has-[>span:empty]:hidden">
                <span ref={silentRef} />
              </p>
              {audioState && audioState !== 'running' && (
                <p>마이크 소리가 흐르지 않아요. 장치를 다시 연결해 주세요.</p>
              )}
              {meterError && <p>{meterError}</p>}
            </div>
          </CheckCard>

          {info && (
            <MissionCard
              info={info}
              exam={practiceModeFor(scriptMode) === 'EXAM'}
              compact={
                baseVoice.status === 'READING' ||
                Boolean(
                  prepareBlocked ||
                  startError ||
                  deviceError ||
                  cal.status === 'FAILED' ||
                  meterError ||
                  (audioState && audioState !== 'running'),
                )
              }
            />
          )}
          <ScriptModeChoice
            value={scriptMode}
            onChange={(mode) => setScriptMode(mode)}
            disabled={starting}
          />
        </div>
      </div>
    </ScreenFrame>
  );
}

/**
 * 평소 목소리 읽기 — **사용자가 "제대로 듣고 있나"를 확인하는 자리**입니다.
 *
 * 말한 내용은 확인하지 않습니다(음성 인식은 Take 를 만든 뒤에만 됩니다). 그래서 소리로 볼 수 있는 것만 보여 줍니다.
 *   읽는 중  문단 · 지금 들리는 음량 막대 · 단계 체크(들림 → 충분히 들음) · "다 읽었어요" 버튼. 시간 막대 ·
 *           숫자는 두지 않습니다 — "들은 시간 2.5초"로는 잘 되고 있는지 판단할 수 없었습니다.
 *           끝은 버튼으로 받습니다 — 소리로 정하면 읽는 도중에 끝나거나 문장이 더 붙었습니다
 *   잡힘     얼마나 들었는지와 어디에 쓰는지. dB 숫자는 보이지 않습니다 — 사용자에게 뜻이 없고,
 *           작아 보이면 다음 읽기에서 일부러 크게 말하게 됩니다
 *   다시     이유에 맞는 안내 (안 들림 · 모자람)
 */
function BaseVoicePanel({
  status,
  stage,
  onFinish,
  reason,
  heardSec,
  echoMeterRef,
}: {
  status: Exclude<BaseVoiceStatus, 'IDLE'>;
  stage: ReadStage;
  onFinish: () => void;
  reason: ShortReason | null;
  heardSec: number | null;
  echoMeterRef: RefObject<HTMLDivElement>;
}) {
  if (status === 'DONE') {
    return (
      <p role="status" className="mt-1.5 text-xs leading-[18px]">
        평소 목소리를 {heardSec}초 들었어요. 발표 중 이 기준과 비교해요. 평소와 다르게 읽었다면 다시
        읽어 주세요.
      </p>
    );
  }
  if (status === 'SHORT') {
    return (
      <div className="prepare-voice-panel mt-1.5 bg-cream p-2 text-xs leading-[18px]">
        <p role="alert" className="text-xs leading-relaxed text-ink/80">
          {SHORT_GUIDE[reason ?? 'BRIEF']}
        </p>
      </div>
    );
  }
  return (
    <div className="prepare-voice-panel mt-1.5 bg-cream p-2 text-xs leading-[18px]">
      <p className="prepare-voice-script font-semibold">{BASE_VOICE_SENTENCES.join(' ')}</p>
      <div className="prepare-voice-progress mt-1.5 grid grid-cols-[64px_minmax(0,1fr)_auto] items-center gap-2">
        <div className="prepare-voice-meter">
          <LevelBar variant="segments" meterRef={echoMeterRef} />
        </div>
        <p role="status">{READ_GUIDE[stage]}</p>
        <Button
          onClick={onFinish}
          size="sm"
          className="prepare-voice-finish min-h-[30px]! border! px-3! py-1! text-xs! shadow-none!"
        >
          다 읽었어요
        </Button>
      </div>
    </div>
  );
}

const READ_GUIDE: Record<ReadStage, string> = {
  WAITING: '말소리를 기다리고 있어요',
  HEARD: '목소리가 들려요 · 계속 읽어 주세요',
  ENOUGH: '충분히 들었어요 · 끝까지 읽고 버튼을 눌러 주세요',
};

function DeviceSelect({
  label,
  value,
  options,
  fallback,
  onChange,
  disabled = false,
}: {
  label: string;
  value: string;
  options: MediaDeviceInfo[];
  fallback: string;
  onChange: (deviceId: string) => void;
  disabled?: boolean;
}) {
  return (
    <label>
      <span className="sr-only">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled || options.length === 0}
        title={
          options.find((device) => device.deviceId === value)?.label ||
          options[0]?.label ||
          fallback
        }
        className="prepare-select min-h-[30px] w-full min-w-0 text-ellipsis border border-line-strong bg-panel px-1.5 py-[3px] text-xs text-ink disabled:border-dashed disabled:bg-cream disabled:text-ink prepare-low:min-h-7"
      >
        {options.length === 0 ? (
          <option value="">{fallback}</option>
        ) : (
          options.map((d) => (
            <option key={d.deviceId} value={d.deviceId}>
              {d.label || fallback}
            </option>
          ))
        )}
      </select>
    </label>
  );
}
