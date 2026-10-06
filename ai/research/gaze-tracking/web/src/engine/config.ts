/**
 * Engine thresholds.  The values are generated from the Python configs
 * (`tools/export_config.py` -> `defaults.ts`); keys keep the YAML
 * spelling so a reader can grep one name across both implementations.
 */
import { DEFAULTS as GENERATED } from './defaults';

export interface PreprocessConfig {
  analysis_fps: number;
  num_faces: number;
  min_face_confidence: number;
  min_face_area_ratio: number;
  min_eye_openness: number;
  border_tolerance_px: number;
}

export interface PlacementConfig {
  min_samples_per_target: number;
  min_separation: number;
  min_delta_deg: number;
  anchor_min_axis_dominance: number;
}

export interface CalibrationConfig {
  min_samples_per_class: number;
  min_loo_accuracy: number;
  p_max_threshold: number;
  margin_threshold: number;
  check_pitch_ordering: boolean;
  target_good_frames: number;
  cue_settle_ms: number;
  cue_timeout_ms: number;
  min_sample_confidence: number;
  max_head_deviation_deg: number;
  outlier_k: number;
  cue_direction_gate: boolean;
  cue_min_up_deg: number;
  cue_min_down_deg: number;
  cue_screen_radius_deg: number;
  cue_max_side_deg: number;
  cue_confirm_frames: number;
  cue_confirm_deg: number;
  sigma_min_deg: number;
  sigma_scale: number;
  screen_aspect: number;
  script_width_fraction: number;
  script_height_fraction: number;
  camera_halfwidth_deg: number;
  other_field_yaw_deg: number;
  other_field_pitch_deg: number;
  other_margin_deg: number;
  screen_min_halfwidth_deg: number;
  screen_max_halfwidth_deg: number;
  prior_camera: number;
  prior_screen: number;
  prior_bottom: number;
  prior_other: number;
  head_away_yaw_deg: number;
  head_away_pitch_deg: number;
  min_anchor_separation: number;
  merge_inseparable_screen: boolean;
  reanchor_frames: number;
  reanchor_max_shift_deg: number;
  reanchor_timeout_ms: number;
}

export interface PreconditionConfig {
  strict: boolean;
  hold_ms: number;
  reject_after_ms: number;
  max_second_face_area_ratio: number;
  second_face_confirm_ms: number;
  max_center_offset_x: number;
  max_center_offset_y: number;
  min_face_area_ratio: number;
  min_iris_px: number;
  max_face_height_ratio: number;
  max_head_yaw_deg: number;
  max_head_pitch_deg: number;
  min_face_brightness: number;
  max_backlight_ratio: number;
  min_analysis_fps: number;
}

export interface ConditionConfig {
  window_ms: number;
  heartbeat_ms: number;
  emit_delta: number;
  fail_reliability: number;
  head_warn_deg: number;
  head_fail_deg: number;
  drift_fail_share: number;
  drift_fail_min_deg: number;
  drift_fail_max_deg: number;
  drift_warn_share: number;
  drift_confirm_ms: number;
  drift_default_span_deg: number;
  head_radius_cm: number;
  second_face_area_ratio: number;
  second_face_confirm_ms: number;
  jitter_window_ms: number;
  jitter_max_gap_ms: number;
  jitter_min_samples: number;
  jitter_fail_share: number;
  jitter_fail_min_deg: number;
  jitter_fail_max_deg: number;
  jitter_warn_share: number;
  valid_warn_ratio: number;
  valid_fail_ratio: number;
  small_face_warn_area: number;
  small_face_fail_area: number;
  large_face_warn_height: number;
  large_face_fail_height: number;
  replace_center_offset: number;
  replace_area_ratio: number;
  replace_confirm_ms: number;
  face_lost_ms: number;
}

/** Gaze evidence for the coach and review agents (`evidence.ts`, Python `vision.evidence.gaze`). */
export interface EvidenceConfig {
  slice_ms: number;
  min_frames_per_slice: number;
  slice_vote_threshold: number;
  short_window_ms: number;
  long_window_ms: number;
  script_min_ms: number;
  script_full_ms: number;
  screen_min_ms: number;
  screen_full_ms: number;
  away_min_ms: number;
  away_full_ms: number;
  low_eye_contact_ratio: number;
  low_eye_contact_min_measured_ms: number;
  unmeasurable_coverage: number;
  unmeasurable_reliability: number;
  segment_min_ms: number;
  outcome_before_ms: number;
  outcome_delay_ms: number;
  outcome_after_ms: number;
  outcome_min_change: number;
}

/** Head circle check between the set-up check and calibration (`sweep.ts`, Python `vision.runtime.sweep`). */
export interface SweepConfig {
  ticks: number;
  neutral_frames: number;
  reach_yaw_deg: number;
  reach_pitch_deg: number;
  max_speed_deg_s: number;
  max_gap_ms: number;
  max_fill_arc_deg: number;
  hint_after_ms: number;
  timeout_ms: number;
}

export interface EngineConfig {
  preprocess: PreprocessConfig;
  placement: PlacementConfig;
  calibration: CalibrationConfig;
  preconditions: PreconditionConfig;
  condition: ConditionConfig;
  evidence: EvidenceConfig;
  sweep: SweepConfig;
}

/** Version parts the frontend stamps on a Take (`aiAdapter.engineVersion`). */
export interface VersionParts {
  modelVersion: string;
  gazeBackbone: string;
  gazeClassifier: string;
}

interface Defaults extends EngineConfig {
  config_hash: string;
  version: VersionParts;
}

const DEFAULTS = GENERATED as unknown as Defaults;

/** Hash of the Python config the defaults were exported from. */
export const CONFIG_HASH: string = DEFAULTS.config_hash;
export const VERSION: VersionParts = Object.freeze({ ...DEFAULTS.version });

export type ConfigOverrides = { [S in keyof EngineConfig]?: Partial<EngineConfig[S]> };

/** A fresh, mutable config: the exported defaults with section-wise overrides. */
export function makeConfig(overrides: ConfigOverrides = {}): EngineConfig {
  return {
    preprocess: { ...DEFAULTS.preprocess, ...overrides.preprocess },
    placement: { ...DEFAULTS.placement, ...overrides.placement },
    calibration: { ...DEFAULTS.calibration, ...overrides.calibration },
    preconditions: { ...DEFAULTS.preconditions, ...overrides.preconditions },
    condition: { ...DEFAULTS.condition, ...overrides.condition },
    evidence: { ...DEFAULTS.evidence, ...overrides.evidence },
    sweep: { ...DEFAULTS.sweep, ...overrides.sweep },
  };
}
