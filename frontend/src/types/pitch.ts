/**
 * 피치 생성 · 수정 — `POST /api/pitches/add` · `PATCH /api/pitches/update/{pitch_id}` (BE #70).
 * BE 그대로 snake_case 입니다 (`PitchSaveRequestDTO`).
 */
export interface PitchRequest {
  /** 1~50자. BE 가 DTO 에서 검사하고, 어기면 422 VALIDATION_ERROR 입니다 */
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

/** 버전 하나 — id 와 갈래 안의 번호 (BE `VersionDTO`) */
export interface VersionRef {
  id: string;
  version: number;
}

/**
 * GET /api/pitches/{pitch_id}/resources — 피치의 버전 목록 (BE `VersionSummaryDTO`).
 * 갈래마다 id · 번호만 옵니다. 내용은 버전마다 따로 읽습니다.
 */
export interface PitchResources {
  pitch_id: string;
  presentation_versions: VersionRef[];
  script_versions: VersionRef[];
  evaluation_versions: VersionRef[];
}
