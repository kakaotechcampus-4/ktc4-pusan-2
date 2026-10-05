/**
 * 피치 생성 · 수정 — `POST /api/pitches/add` · `PUT /api/pitches/update/{pitch_id}`.
 * BE 그대로 snake_case 입니다 (`PitchDTO`).
 */
export interface PitchRequest {
  title: string;
  time_limit_sec: number;
  /** 목표보다 길게 발표해도 되는 시간(초) */
  upper_deviation: number;
  /** 목표보다 짧게 발표해도 되는 시간(초) */
  lower_deviation: number;
  /** yyyy-mm-dd. 안 정했으면 null */
  presentation_date: string | null;
}

/** 두 API 모두 `{"message", "pitch_id"}` 를 돌려줍니다 */
export interface PitchSavedResponse {
  message: string;
  pitch_id: string;
}
