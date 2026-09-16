import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { DEVICE_ERROR_MESSAGE, useCameraStream } from '@/features/rehearsal/media/useCameraStream';
import { usePrepare } from '@/shared/api/prepare';
import { CameraPreview } from './CameraPreview';
import { CheckCard } from './CheckCard';
import { LevelBar } from '@/features/rehearsal/media/LevelBar';
import { ScreenFrame, StageButton } from './ScreenFrame';
import { usePrepareStore } from './prepareStore';
import { useGazeCalibration } from './useGazeCalibration';
import { useMicLevel } from '@/features/rehearsal/media/useMicLevel';
import { useVideoStream } from '@/features/rehearsal/media/useVideoStream';

/**
 * 05 카메라 점검 — 리허설 준비 바로 앞.
 *
 * 여기서는 **Take를 만들지 않습니다.** Take는 준비 화면의 시작 CTA에서만 생깁니다
 * (CLAUDE.md 8번). 여기서 만들면 점검하다 그만둔 만큼 빈 Take가 쌓이고,
 * takeNumber가 실제 연습 횟수와 어긋납니다.
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
  const { meterRef, dbRef, rowRef, silentRef, micOk, audioState } = useMicLevel(stream);
  const declineGaze = usePrepareStore((s) => s.declineGaze);
  const cal = useGazeCalibration({ videoRef, live });

  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);

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
    if (activation?.hasBeenActive) void request();
  }, [request]);

  // 장치 이름은 권한을 받은 뒤에야 채워집니다. 그 전에는 label이 빈 문자열입니다
  useEffect(() => {
    if (!stream) return;
    void navigator.mediaDevices.enumerateDevices().then(setDevices);
  }, [stream]);

  const cameras = devices.filter((d) => d.kind === 'videoinput');
  const mics = devices.filter((d) => d.kind === 'audioinput');

  const ready = live && micOk && cal.points === 2;
  const goPrepare = () => navigate(`/pitch/${pitchId}/prepare`);

  const hint = deviceError
    ? DEVICE_ERROR_MESSAGE[deviceError]
    : !live
      ? '카메라를 켜야 점검을 시작할 수 있습니다'
      : !micOk
        ? '마이크에 대고 한 마디 해보세요'
        : cal.phase === 'FAILED'
          ? '기준을 잡지 못했어요. 얼굴이 화면 안에 있는지 보고 다시 해주세요'
          : cal.points < 2
            ? '시선 기준을 먼저 잡아야 연습을 시작할 수 있습니다'
            : '점검이 끝났어요';

  return (
    <ScreenFrame
      screenNo="05"
      screenName="카메라 점검"
      entry="진입 · 피치 생성의 다음 / 리포트의 다시 연습하기"
      title={data?.title ?? '불러오는 중…'}
      subtitle={data ? `자료 v${data.presentationVersion} · 대본 v${data.scriptVersion}` : ''}
      badge={`TAKE ${data?.nextTakeNumber ?? '—'} · 장치 점검`}
      onBack={() => navigate(-1)}
      hint={hint}
      actions={
        <>
          <StageButton
            disabled={!micOk}
            onClick={() => {
              declineGaze();
              goPrepare();
            }}
          >
            소리만으로 계속하기
          </StageButton>
          <StageButton variant="primary" disabled={!ready} onClick={goPrepare}>
            시작하기
          </StageButton>
        </>
      }
    >
      <div className="grid gap-5 lg:grid-cols-[1fr_320px]">
        <div className="flex flex-col gap-4">
          <div className="aspect-video w-full">
            <CameraPreview
              videoRef={videoRef}
              live={live}
              phase={cal.phase}
              countdownRef={cal.countdownRef}
              onEnable={() => void request()}
            />
          </div>

          <div className="rounded-xl border border-stage-panel bg-stage-panel/40 px-4 py-3">
            <LevelBar variant="segments" meterRef={meterRef} dbRef={dbRef} />
          </div>
        </div>

        <div className="flex flex-col gap-4">
          <section className="rounded-xl border border-stage-panel bg-stage-panel/40 p-4">
            <h2 className="text-sm font-bold">장치 선택</h2>

            <DeviceSelect
              label="카메라"
              value={videoId}
              options={cameras}
              fallback="기본 카메라"
              onChange={(id) => void request({ videoDeviceId: id, audioDeviceId: audioId })}
            />
            <DeviceSelect
              label="마이크"
              value={audioId}
              options={mics}
              fallback="기본 마이크"
              onChange={(id) => void request({ videoDeviceId: videoId, audioDeviceId: id })}
            />
          </section>

          <CheckCard
            title="점검 항목"
            rows={[
              {
                id: 'camera',
                label: live ? '카메라 · 영상 들어옴' : '카메라 · 영상 없음',
                done: live,
              },
              {
                id: 'mic',
                // 문구 전체를 rAF가 다시 씁니다 — 초당 수십 번 바뀌는 값이라 상태로 올리지 않습니다
                label: <span ref={rowRef}>마이크 입력 확인 중</span>,
                done: micOk,
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
                  text:
                    cal.phase === 'DONE'
                      ? '다시 잡기'
                      : cal.phase === 'EVALUATING'
                        ? '확인 중…'
                        : cal.running
                          ? '잡는 중…'
                          : !live
                            ? '카메라 먼저'
                            : '누르면 시작',
                  onClick: () => {
                    if (live && !cal.running) cal.start();
                  },
                },
              },
            ]}
          />

          {/* 권한·무입력 안내. 문구는 useCameraStream이 들고 있는 것을 그대로 씁니다 —
              화면마다 새로 지으면 사용자가 뭘 해야 할지 매번 달라집니다 */}
          <section className="rounded-xl border border-stage-panel bg-stage-panel/40 p-4 text-xs leading-relaxed">
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
        className="mt-1 w-full rounded-lg border border-stage-panel bg-stage px-3 py-2 text-sm
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
