import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { DEVICE_ERROR_MESSAGE, useCameraStream, type DeviceError } from '../media/useCameraStream';
import { postCalibration, useCreateTake, usePrepare } from '@/shared/api/prepare';
import { setTakeId, startSession } from '../lib/db';
import { toMessage } from '@/shared/api/errorMessage';
import { CameraPreview } from './CameraPreview';
import { CheckCard } from './CheckCard';
import { LevelBar } from '../media/LevelBar';
import { ScreenFrame, StageButton } from './ScreenFrame';
import { MissionCard } from './MissionCard';
import { ScriptModeChoice } from './ScriptModeChoice';
import { modeForScriptMode, normalizeScriptMode, usePrepareStore } from './prepareStore';
import { useGazeCalibration, type CalibrationPhase } from './useGazeCalibration';
import { useMicLevel } from '../media/useMicLevel';
import { useVideoStream } from '../media/useVideoStream';

/**
 * 점검 안내 한 줄.
 *
 * **쓴 순서가 곧 우선순위입니다.** 여럿이 동시에 어긋나 있어도 사용자가 지금 할 수 있는
 * 일은 하나뿐이라, 가장 앞을 막고 있는 것만 말합니다.
 *
 * 마이크가 시선 기준보다 앞인 이유 — 마이크가 없으면 '소리만으로 계속하기'까지 잠깁니다.
 * 그 상태에서 시선 기준을 잡아 봐야 열리는 버튼이 없습니다. 반대로 시선을 못 잡아도
 * 마이크만 되면 소리만으로 갈 수 있습니다. 그래서 마이크가 먼저입니다.
 */
function deviceCheckHint({
  deviceError,
  live,
  micOk,
  calPhase,
  calPoints,
}: {
  deviceError: DeviceError | null;
  live: boolean;
  micOk: boolean;
  calPhase: CalibrationPhase;
  calPoints: number;
}): string {
  if (deviceError) return DEVICE_ERROR_MESSAGE[deviceError];
  if (!live) return '카메라를 켜야 점검을 시작할 수 있습니다';
  if (!micOk) return '마이크에 대고 한 마디 해보세요';
  if (calPhase === 'FAILED')
    return '기준을 잡지 못했어요. 얼굴이 화면 안에 있는지 보고 다시 해주세요';
  if (calPoints < 2) return '시선 기준을 먼저 잡아야 연습을 시작할 수 있습니다';
  return '점검이 끝났어요';
}

/**
 * 시선 기준점 항목의 버튼 문구.
 *
 * `cal.running` 을 보지 않고 phase 만 봅니다 — running 이 phase 에서 파생된 값이라
 * (CAMERA·BOTTOM·EVALUATING) 둘을 같이 보면 같은 사실을 두 번 묻는 셈입니다.
 *
 * 상태 하나에 대한 분기라 switch 로 둡니다. phase 가 늘면 **여기서 컴파일이 깨져서**
 * 빠뜨린 갈래가 바로 드러납니다 — 삼항으로 이어 붙이면 조용히 마지막 갈래로 떨어집니다.
 *
 * IDLE·FAILED 만 `live` 를 함께 봅니다. 카메라가 없으면 눌러도 시작되지 않는데
 * 버튼이 '누르면 시작'이라고 말하면 안 됩니다.
 */
function calibrationActionText(phase: CalibrationPhase, live: boolean): string {
  switch (phase) {
    case 'DONE':
      return '다시 잡기';
    case 'EVALUATING':
      return '확인 중…';
    case 'CAMERA':
    case 'BOTTOM':
      return '잡는 중…';
    case 'IDLE':
    case 'FAILED':
      return live ? '누르면 시작' : '카메라 먼저';
  }
}

/**
 * 09 시작 전 세팅 — 리허설 바로 앞. **Take가 생기는 유일한 화면입니다.**
 *
 * 전에는 장치 점검(05)과 리허설 준비(06)가 따로였는데, 시안 09 가 둘을 한 화면으로
 * 그리면서 합쳤습니다. 미션·대본 표시·평가기준이 준비 화면에만 있던 것들입니다.
 *
 * ★ Take 는 아래 `start()` 에서만 생깁니다 (CLAUDE.md 8번). 화면에 들어오는 것만으로는
 *   만들지 않습니다 — 점검하다 그만둔 만큼 빈 Take 가 쌓이고 takeNumber 가 실제
 *   연습 횟수와 어긋납니다. 이 함수를 다른 화면으로 복사하지 마세요.
 *
 * 점검 셋 중 둘은 장치(카메라·마이크)고 하나는 시선 기준점입니다.
 * 시선을 못 잡겠으면 '소리만으로 계속하기'로 빠집니다 — 그 Take의 시선은
 * USER_DECLINED로 제외되고, 말하기 지표만으로 리포트가 나옵니다.
 */
export function DeviceCheckPage() {
  const { pitchId = '' } = useParams();
  const navigate = useNavigate();
  const { data } = usePrepare(pitchId);

  const { stream, error: deviceError, request } = useCameraStream();
  const { videoRef, live } = useVideoStream(stream, 'device-check');
  const { meterRef, dbRef, rowRef, silentRef, micOk, audioState, meterError } = useMicLevel(stream);
  const declineGaze = usePrepareStore((s) => s.declineGaze);
  // 대본 표시는 준비 화면과 **같은 스토어**를 씁니다 — 여기서 고른 것이 그대로 이어집니다
  const scriptMode = usePrepareStore((s) => s.scriptMode);
  const setScriptMode = usePrepareStore((s) => s.setScriptMode);
  const calibration = usePrepareStore((s) => s.calibration);
  const scriptModeTouched = usePrepareStore((s) => s.scriptModeTouched);
  const setChosenDevices = usePrepareStore((s) => s.setDevices);
  const cal = useGazeCalibration({ videoRef, live });
  const createTake = useCreateTake();

  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

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

  // 서버 기본값은 **사용자가 고르기 전에만** 넣습니다. 조건 없이 덮으면 응답이 늦게
  // 올 때 사용자가 고른 것이 되돌아갑니다. FULL 이 올 수 있어 3단계로 접습니다.
  useEffect(() => {
    if (data && !scriptModeTouched) {
      setScriptMode(normalizeScriptMode(data.defaultScriptMode), false);
    }
  }, [data, scriptModeTouched, setScriptMode]);

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

  const ready = live && micOk && cal.points === 2 && data !== undefined && !starting;

  /**
   * ★ Take 는 여기서만 생깁니다. 이 함수를 다른 화면으로 복사하지 마세요.
   *
   * 순서가 중요합니다 - 세션(clientSessionId)이 먼저입니다. 그 값이 POST /takes 의
   * 멱등 키이자 IndexedDB 에 쌓일 모든 기록의 키입니다 (업로드 재시도도 같은 값).
   */
  const start = async () => {
    if (!data || starting) return;
    setStarting(true);
    setStartError(null);

    // 드롭다운 값이 아니라 **실제로 열린 트랙**의 장치를 넘깁니다. 기본 장치로 통과했어도
    // 그 장치가 남아서, 그사이 OS 기본값이 바뀌어도 리허설이 같은 장치를 엽니다.
    // '' 는 undefined 로 — exact 에 빈 문자열을 걸면 OverconstrainedError 가 납니다
    setChosenDevices({
      videoDeviceId: videoId || undefined,
      audioDeviceId: audioId || undefined,
    });

    // 시안 09 - 대본 표시 하나로 연습 모드까지 정해집니다
    const mode = modeForScriptMode(scriptMode);

    try {
      const clientSessionId = await startSession();

      const take = await createTake.mutateAsync({
        pitchId: data.pitchId,
        clientSessionId,
        mode,
        scriptMode,
        presentationVersion: data.presentationVersion,
        scriptVersion: data.scriptVersion,
        criteriaVersion: data.criteria.version,
      });
      await setTakeId(clientSessionId, take.takeId);

      // 품질 요약만 갑니다. 기준 벡터는 브라우저에 남습니다 (CLAUDE.md 1번)
      if (calibration) await postCalibration(take.takeId, calibration);

      navigate(mode === 'EXAM' ? `/takes/${take.takeId}/exam` : `/takes/${take.takeId}/rehearsal`, {
        state: { clientSessionId, scriptMode },
      });
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
      ? '연습을 여는 중…'
      : deviceCheckHint({
          deviceError,
          live,
          micOk,
          calPhase: cal.phase,
          calPoints: cal.points,
        }));

  const takeNumber = data?.nextTakeNumber ?? 0;
  const startLabel = modeForScriptMode(scriptMode) === 'EXAM' ? '실전 모드로 시작' : '시작하기';

  return (
    <ScreenFrame
      screenNo="09"
      screenName="시작 전 세팅 (Take 준비)"
      entry="진입 · 피치 생성 완료 / 리포트의 다시 연습하기"
      title={data?.title ?? '불러오는 중…'}
      subtitle={data ? `자료 v${data.presentationVersion} · 대본 v${data.scriptVersion}` : ''}
      badge={`TAKE ${data?.nextTakeNumber || '—'} · 시작 전 세팅`}
      onBack={() => navigate(-1)}
      hint={hint}
      actions={
        <>
          {/*
            시선을 못 잡아도 연습은 갑니다. 그 Take 의 시선은 USER_DECLINED 로 제외되고
            말하기 지표만으로 리포트가 나옵니다 - 막는 것보다 반쪽이라도 남기는 것이 낫습니다.
          */}
          <StageButton
            disabled={!micOk || starting || data === undefined}
            onClick={() => {
              declineGaze();
              start().catch(() => undefined);
            }}
          >
            소리만으로 계속하기
          </StageButton>
          <StageButton
            variant="primary"
            disabled={!ready}
            onClick={() => start().catch(() => undefined)}
          >
            {takeNumber > 0 ? `Take ${takeNumber} ${startLabel}` : startLabel}
          </StageButton>
        </>
      }
    >
      {/*
        목업 09 의 구성 — 왼쪽은 "지금 보이는 것"(카메라와 그 아래 점검), 오른쪽은
        "이번 Take 를 어떻게 할지"(미션 · 대본 표시)입니다. 장치와 결정을 갈라 두면
        발표 직전에 눈이 한쪽만 훑어도 됩니다.
      */}
      <div className="grid gap-5 lg:grid-cols-[1fr_340px]">
        <div className="flex flex-col gap-4">
          <div className="aspect-video w-full">
            <CameraPreview
              videoRef={videoRef}
              live={live}
              phase={cal.phase}
              countdownRef={cal.countdownRef}
              onEnable={() => request().catch(() => undefined)}
            />
          </div>

          {/* 장치 선택과 점검 항목을 나란히 — 고른 장치의 결과가 바로 옆에서 보입니다 */}
          <div className="grid gap-4 sm:grid-cols-2">
            <section className="rounded-xl border border-line-strong bg-panel p-4">
              <h2 className="text-sm font-bold">장치 선택</h2>

              <DeviceSelect
                label="카메라"
                value={videoId}
                options={cameras}
                fallback="기본 카메라"
                onChange={(id) =>
                  request({ videoDeviceId: id, audioDeviceId: audioId }).catch(() => undefined)
                }
              />
              <DeviceSelect
                label="마이크"
                value={audioId}
                options={mics}
                fallback="기본 마이크"
                onChange={(id) =>
                  request({ videoDeviceId: videoId, audioDeviceId: id }).catch(() => undefined)
                }
              />
            </section>

            <CheckCard
              title="점검 항목"
              rows={[
                {
                  id: 'camera',
                  label: live ? '카메라 · 얼굴 인식됨' : '카메라 · 영상 없음',
                  done: live,
                },
                {
                  id: 'mic',
                  // 문구 전체를 rAF가 다시 씁니다 — 초당 수십 번 바뀌는 값이라 상태로 올리지 않습니다
                  label: <span ref={rowRef}>마이크 입력 확인 중</span>,
                  done: micOk,
                  // 막대를 이 줄 안에 둡니다 (목업 09) — 숫자와 움직임이 같이 보여야
                  // "소리가 들어오고 있다"가 한 번에 읽힙니다
                  trailing: <LevelBar variant="segments" meterRef={meterRef} dbRef={dbRef} />,
                },
                {
                  // 카메라를 2초, 대본 자리를 2초. 모은 프레임은 분류기가 받아
                  // 기준과 품질을 정합니다 (A안) — 여기서는 순서와 안내만 합니다.
                  id: 'gaze',
                  label:
                    cal.phase === 'FAILED'
                      ? '시선 기준점 · 다시 필요'
                      : `시선 기준점 ${cal.points} / 2`,
                  done: cal.phase === 'DONE',
                  action: {
                    text: calibrationActionText(cal.phase, live),
                    onClick: () => {
                      if (live && !cal.running) cal.start();
                    },
                  },
                },
              ]}
            />
          </div>
        </div>

        <div className="flex flex-col gap-4">
          <MissionCard description={data?.lastMission?.description ?? null} />

          <ScriptModeChoice value={scriptMode} onChange={(m) => setScriptMode(m)} />

          {/* 권한·무입력 안내. 문구는 useCameraStream이 들고 있는 것을 그대로 씁니다 —
              화면마다 새로 지으면 사용자가 뭘 해야 할지 매번 달라집니다 */}
          <section className="rounded-xl border border-dashed border-line-strong p-4 text-xs leading-relaxed">
            {deviceError ? (
              <p className="font-bold text-coral">{DEVICE_ERROR_MESSAGE[deviceError]}</p>
            ) : (
              <>
                <p className="text-stone">권한·무입력 안내가 이 자리에 표시됩니다</p>
                <p className="mt-1 text-stone">
                  예: 주소창 왼쪽 자물쇠 → 카메라 → 허용으로 바꿔 주세요
                </p>
              </>
            )}

            {cal.phase === 'FAILED' && (
              <div className="mt-2 rounded-lg border border-coral bg-coral/10 p-3">
                <p className="font-bold text-coral">기준을 잡지 못했어요</p>
                <p className="mt-1 text-stone">
                  두 지점을 바라보는 4초 동안 얼굴이 화면 안에 있어야 합니다. 카메라를 볼 때와 대본
                  자리를 볼 때를 분명히 나눠 주세요.
                </p>
                <button
                  type="button"
                  onClick={cal.start}
                  className="mt-2 rounded-full bg-coral px-3 py-1 font-semibold text-white
                             hover:bg-coral-deep"
                >
                  다시 잡기
                </button>
              </div>
            )}

            <p className="mt-1 text-coral">
              <span ref={silentRef} />
            </p>
            {audioState && audioState !== 'running' && (
              <p className="mt-1 text-coral">
                AudioContext가 {audioState} 상태입니다 — 오디오가 흐르지 않습니다
              </p>
            )}
            {/* 계량기가 아예 못 떴을 때. '입력 없음'과 구분해서 보여줘야
                사용자가 말을 더 크게 할지, 장치를 바꿀지 정할 수 있습니다 */}
            {meterError && <p className="mt-1 text-coral">{meterError}</p>}
          </section>
        </div>
      </div>
    </ScreenFrame>
  );
}

function DeviceSelect({
  label,
  value,
  options,
  fallback,
  onChange,
}: {
  label: string;
  value: string;
  options: MediaDeviceInfo[];
  fallback: string;
  onChange: (deviceId: string) => void;
}) {
  return (
    <label className="mt-3 block">
      <span className="text-xs text-stone">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={options.length === 0}
        className="mt-1 w-full rounded-lg border border-line-strong bg-panel px-3 py-2 text-sm
                   disabled:opacity-50"
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
