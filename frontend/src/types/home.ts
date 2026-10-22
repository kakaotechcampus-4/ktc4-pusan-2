/** GET /api/pitches/ — backend PR #47. Times are seconds; absent scores are null. */
export interface HomeTake {
  take_version: number;
  take_elapsed: number | null;
  take_time: number;
  script_mode: string;
  score: number | null;
  delta: number | null;
  /** UUID returned by #47; distinct from take_version. */
  take_id: string;
}

export interface HomePitch {
  pitch_title: string;
  pitch_time: number;
  thumbnail_url: string | null;
  /** yyyy-mm-dd. 생성 때 비워 둘 수 있습니다 (BE `PitchListItemDTO.presentation_date`) */
  presentation_date?: string | null;
  takes: HomeTake[];
  /** UUID returned by #47 for opening the pitch editor. */
  pitch_id: string;
}

export interface PitchListResponse {
  pitches: HomePitch[];
}
