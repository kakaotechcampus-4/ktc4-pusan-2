import type { RefObject } from 'react';
import { createPortal } from 'react-dom';
import type { CalibrationPhase } from './useGazeCalibration';

/**
 * 카메라 자기 화면.
 *
 * 두 가지를 같이 합니다 —
 *   1. 얼굴이 화면 안에 들어왔는지 스스로 보게 한다
 *   2. 카메라 배치 확인과 2점 캘리브레이션의 시선 표적을 띄운다
 *
 * 영상은 **거울상**으로 보여 줍니다. 자기 모습을 좌우 반대로 보면 위치를 못 맞춥니다.
 * 그래서 `CalibrationSummary.coordinateSpace`가 'mirrored'로 남습니다 —
 * 판정할 때 이 뒤집기를 한 번 더 뒤집지 않으면 좌우가 통째로 바뀝니다.
 */
export function CameraPreview({
  videoRef,
  live,
  phase,
  countdownRef,
  variant = 'full',
  onEnable,
}: {
  videoRef: RefObject<HTMLVideoElement>;
  live: boolean;
  phase: CalibrationPhase;
  /** 남은 초. 훅이 DOM 에 직접 씁니다 — 4초 동안 렌더를 돌리지 않습니다 */
  countdownRef?: RefObject<HTMLSpanElement>;
  variant?: 'full' | 'mini';
  /** 아직 스트림이 없을 때 켜는 버튼. 브라우저 권한은 사용자 조작 안에서만 열립니다 */
  onEnable?: () => void;
}) {
  const mini = variant === 'mini';

  return (
    <div
      className={`relative h-full w-full overflow-hidden border border-stage-panel bg-stage-deep ${
        mini ? 'rounded-lg' : 'rounded-xl'
      }`}
    >
      {/* -scale-x-100 — 거울상. 위 주석 참고 */}
      <video
        ref={videoRef}
        muted
        playsInline
        className={`h-full w-full -scale-x-100 object-cover ${live ? '' : 'invisible'}`}
      />

      {/* 얼굴 안내선 */}
      <div className="pointer-events-none absolute inset-0 grid place-items-center">
        <div
          className={`rounded-full border-2 border-dashed border-stone ${
            mini ? 'h-10 w-10' : 'aspect-3/4 h-[68%]'
          }`}
        />
      </div>

      <CalibrationTarget phase={phase} mini={mini} />

      {/* 남은 초. 표적 옆이 아니라 가운데 둡니다 — 표적을 보는 동안 곁눈으로 읽힙니다.
          PLACE_SCREEN 에서는 화면 전체 표적이 같은 ref 로 초를 받습니다 */}
      {!mini && (phase === 'PLACE_CAMERA' || phase === 'CAMERA' || phase === 'BOTTOM') && (
        <span
          ref={countdownRef}
          className="tabular pointer-events-none absolute top-1/2 left-1/2 -translate-x-1/2
                     -translate-y-1/2 text-5xl font-bold text-ink-stage/80"
        />
      )}

      {!mini && (
        <>
          <p className="absolute inset-x-0 bottom-12 text-center text-sm text-stone">
            {CAPTION[phase]}
          </p>

          {/* 색 점 + 글자. 색만으로 상태를 말하지 않습니다 */}
          <span
            className="absolute bottom-3 left-3 flex items-center gap-1.5 rounded-full
                       bg-stage/80 px-2.5 py-1 text-xs"
          >
            <span aria-hidden className={live ? 'text-coral' : 'text-stone'}>
              ●
            </span>
            {/* 얼굴 판정은 FaceLandmarker가 붙는 날 여기로 들어옵니다 (I-03).
                그 전까지 "인식됨"이라고 쓰면 없는 판정을 있다고 말하는 게 됩니다 */}
            {live ? '영상 들어옴' : '영상 없음'}
          </span>

          <span className="absolute right-3 bottom-3 font-mono text-[11px] tracking-wide text-stone">
            CAMERA PREVIEW · 16:9
          </span>
        </>
      )}

      {!mini && phase === 'PLACE_SCREEN' && <ScreenCenterTarget countdownRef={countdownRef} />}

      {!live && onEnable && (
        <div className="absolute inset-0 grid place-items-center bg-stage/70 px-4 text-center">
          <button
            type="button"
            onClick={onEnable}
            className="rounded-full bg-coral px-4 py-2 text-xs font-semibold text-white
                       hover:bg-coral-deep"
          >
            {mini ? '카메라 켜기' : '카메라·마이크 켜기'}
          </button>
        </div>
      )}
    </div>
  );
}

const CAPTION: Record<CalibrationPhase, string> = {
  IDLE: '얼굴을 안내선 안에 맞춰 주세요',
  PLACE_CAMERA: '카메라 렌즈를 바라보세요 · 카메라 위치 확인 중',
  PLACE_SCREEN: '화면 한가운데 점을 바라보세요',
  PLACE_EVALUATING: '카메라 위치를 확인하는 중…',
  PLACE_WARN: '카메라 위치를 확인해 주세요',
  CAMERA: '카메라 렌즈를 바라보세요',
  BOTTOM: '대본이 놓일 아래쪽을 바라보세요',
  EVALUATING: '기준을 확인하는 중…',
  DONE: '시선 기준을 잡았어요',
  FAILED: '기준을 잡지 못했어요',
};

/**
 * 시선 표적. 2점이라 자리도 둘뿐입니다 — 카메라(위) · 화면 아래.
 * 이 두 방향은 고개도 같이 움직여서 head pose가 시선 벡터를 보강합니다 (CLAUDE.md 2번).
 */
function CalibrationTarget({ phase, mini }: { phase: CalibrationPhase; mini: boolean }) {
  // 배치 확인의 첫 2초도 렌즈를 봅니다 — 캘리브레이션 CAMERA 와 같은 자리입니다
  const lookingAtLens = phase === 'CAMERA' || phase === 'PLACE_CAMERA';
  if (!lookingAtLens && phase !== 'BOTTOM') {
    return (
      <span
        aria-hidden
        className="pointer-events-none absolute top-1/2 left-1/2 size-1.5 -translate-x-1/2
                   -translate-y-1/2 rounded-full bg-coral"
      />
    );
  }

  const place = lookingAtLens ? 'top-4' : 'bottom-4';
  return (
    <span
      aria-hidden
      className={`pointer-events-none absolute left-1/2 -translate-x-1/2 animate-pulse rounded-full
                  bg-coral ${place} ${mini ? 'size-2.5' : 'size-4'}`}
    />
  );
}

/**
 * 배치 확인 둘째 2초의 표적 — **모니터 한가운데.**
 *
 * 미리보기 안이 아니라 화면 전체를 덮습니다. 배치 확인은 "렌즈에서 본 화면 가운데가
 * 어느 쪽인가"를 재는 것이라, 표적이 미리보기(화면 위쪽 왼편)에 있으면 각도가 틀어집니다.
 *
 * body 로 포털을 띄웁니다 — 조상에 transform 이 있으면 `fixed` 가 그 조상 기준이 되어
 * 화면 가운데를 벗어납니다.
 */
function ScreenCenterTarget({ countdownRef }: { countdownRef?: RefObject<HTMLSpanElement> }) {
  return createPortal(
    <div className="fixed inset-0 z-50 bg-stage-deep/95" role="status" aria-live="polite">
      <span
        aria-hidden
        className="pointer-events-none absolute top-1/2 left-1/2 size-5 -translate-x-1/2
                   -translate-y-1/2 animate-pulse rounded-full bg-coral"
      />
      <p className="absolute inset-x-0 top-1/2 mt-8 text-center text-sm text-ink-stage/80">
        화면 한가운데 점을 바라보세요 · <span ref={countdownRef} className="tabular" />
      </p>
    </div>,
    document.body,
  );
}
