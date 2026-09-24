/** GET /api/pitches/ — backend PR #47. Times are seconds; absent scores are null. */
export interface HomeTake {
  take_version: number;
  take_elapsed: number | null;
  take_time: number;
  script_mode: string;
  score: number | null;
  delta: number | null;
  /** Not supplied by #47 yet. Never derive a resource ID from its version. */
  take_id?: string | null;
}

export interface HomePitch {
  pitch_title: string;
  pitch_time: number;
  thumbnail_url: string | null;
  takes: HomeTake[];
  /** Required backend extension for opening this pitch's editor. */
  pitch_id?: string | null;
}

export interface PitchListResponse {
  pitches: HomePitch[];
}
