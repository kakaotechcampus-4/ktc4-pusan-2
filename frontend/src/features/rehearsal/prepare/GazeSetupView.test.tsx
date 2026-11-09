// @vitest-environment jsdom
import { act, createRef } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { GazeSetupView } from './GazeSetupView';

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

it('marks only the full-screen gaze overlay without replacing its camera frame', async () => {
  const frameRef = createRef<HTMLDivElement>();
  const boxRef = createRef<HTMLDivElement>();
  const videoRef = createRef<HTMLVideoElement>();
  const render = async (overlay: boolean) => {
    await act(async () => {
      root.render(
        <GazeSetupView
          frameRef={frameRef}
          boxRef={boxRef}
          videoRef={videoRef}
          live={false}
          status="IDLE"
          unavailableMessage={null}
          overlay={overlay}
          onCancel={() => undefined}
        />,
      );
    });
  };

  await render(false);
  const frame = frameRef.current;
  expect(frame).not.toBeNull();
  expect(frame?.hasAttribute('data-gaze-overlay')).toBe(false);
  expect(frame?.classList.contains('absolute')).toBe(true);

  await render(true);
  expect(frameRef.current).toBe(frame);
  expect(frame?.getAttribute('data-gaze-overlay')).toBe('');
  expect(frame?.classList.contains('fixed')).toBe(true);

  await render(false);
  expect(frameRef.current).toBe(frame);
  expect(frame?.hasAttribute('data-gaze-overlay')).toBe(false);
  expect(frame?.classList.contains('absolute')).toBe(true);
});
