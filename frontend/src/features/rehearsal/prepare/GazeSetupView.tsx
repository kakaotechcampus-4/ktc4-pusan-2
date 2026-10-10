import type { RefObject } from 'react';
import type { GazeSetupStatus } from './useGazeSetup';

/**
 * 카메라 자기 화면 — **상자 안은 AI 카메라 화면 모듈이 그립니다** (`useGazeSetup`).
 *
 * 이 컴포넌트가 하는 일은 셋뿐입니다.
 *   1. 모듈이 채울 상자를 둔다. 평소엔 16:9 카드, 보정 중엔 화면 전체
 *   2. 배치 키를 읽을 FE 의 `<video>` 를 화면 밖에 둔다
 *   3. 상자 바깥의 것 — 카메라 켜기 버튼, 엔진이 없을 때 안내, 보정 그만하기
 *
 * 얼굴 원 · 3D 십자선 · 보정 과녁 · 안내 카드 · 결과 · 실시간 상태는 모두 모듈 몫입니다.
 * 거울상도 모듈이 보여 줍니다(엔진에는 원본 프레임이 갑니다).
 *
 * 상자는 **한 번만 만들고 틀의 클래스만 바꿉니다.** 카드와 전체 화면을 다른 요소로 그리면
 * 모듈이 다시 만들어지고 엔진을 처음부터 띄웁니다. 상자 크기가 바뀌면 모듈이 컨테이너 쿼리로
 * 배치를 다시 잡습니다.
 */
export function GazeSetupView({
  frameRef,
  boxRef,
  videoRef,
  live,
  status,
  unavailableMessage,
  overlay,
  onEnable,
  onCancel,
}: {
  /** 전체 화면으로 올리는 틀. 상자와 [그만하기]를 함께 감쌉니다 */
  frameRef: RefObject<HTMLDivElement>;
  /** 모듈이 채우는 상자 */
  boxRef: RefObject<HTMLDivElement>;
  videoRef: RefObject<HTMLVideoElement>;
  live: boolean;
  status: GazeSetupStatus;
  /** UNAVAILABLE 일 때의 이유와 할 일 (`useGazeSetup`) */
  unavailableMessage: string | null;
  overlay: boolean;
  /** 아직 스트림이 없을 때 켜는 버튼. 브라우저 권한은 사용자 조작 안에서만 열립니다 */
  onEnable: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="relative h-full w-full">
      <div
        ref={frameRef}
        className={
          overlay
            ? 'fixed inset-0 z-50 bg-stage-deep'
            : 'absolute inset-0 overflow-hidden rounded-xl border border-stage-panel bg-stage-deep'
        }
      >
        {/*
          배치 키(layoutSignature)와 카메라 교체 감지용. 모듈은 자기 <video> 를 따로 씁니다.
          display:none 이면 일부 브라우저가 재생을 멈춰 `playing` 이 안 와서, 크기만 없앱니다
        */}
        <video
          ref={videoRef}
          muted
          playsInline
          aria-hidden
          className="pointer-events-none absolute size-px opacity-0"
        />

        <div ref={boxRef} className="absolute inset-0" />

        {overlay && (
          <button
            type="button"
            onClick={onCancel}
            className="absolute right-4 bottom-4 rounded-full border border-stone/60 bg-stage/80
                     px-3 py-1 text-xs text-ink-stage/80 hover:bg-stage"
          >
            그만하기 · Esc
          </button>
        )}

        {live && status === 'LOADING' && (
          <p
            className="pointer-events-none absolute inset-x-0 bottom-12 text-center text-sm
                     text-stone"
          >
            시선 분석을 준비하는 중…
          </p>
        )}

        {live && status === 'UNAVAILABLE' && (
          <div className="absolute inset-x-4 bottom-4 rounded-lg bg-stage/90 p-3 text-xs">
            <p className="font-bold text-coral">시선 분석을 켤 수 없어요</p>
            <p className="mt-1 text-stone">{unavailableMessage}</p>
          </div>
        )}

        {!live && (
          <div className="absolute inset-0 grid place-items-center bg-stage/70 px-4 text-center">
            <button
              type="button"
              onClick={onEnable}
              className="rounded-full bg-coral px-4 py-2 text-xs font-semibold text-white
                       hover:bg-coral-deep"
            >
              카메라·마이크 켜기
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
