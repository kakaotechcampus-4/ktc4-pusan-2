/**
 * The camera view: what the camera picture shows, for the frontend to place in
 * its own page (`GazeCameraView`).  It brings its worker (`src/worker`) and the
 * engine (`src/engine`) with it; the host gives it a box and a camera stream.
 */
export { GazeCameraView } from './view';
export type {
  CameraPhase,
  CameraViewEvents,
  CameraViewOptions,
  CameraZone,
  SetupResult,
} from './view';
export type { FrameSummary } from '../worker/protocol';
