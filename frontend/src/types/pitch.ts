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

export interface PitchDetail extends PitchRequest {
  pitch_id: string;
  /** First Take 튜토리얼 샘플 피치인지 (BE #1135). 수정 화면은 샘플을 열지 않습니다 */
  is_demo: boolean;
}

/**
 * First Take 튜토리얼 샘플 피치 — `POST /api/pitches/tutorials/first-take` (BE #1135).
 * 사용자마다 하나라 처음이면 만들고, 있으면 그 피치를 줍니다. 여러 탭에서 불러도 하나만 생깁니다.
 * 대본 나누기 · 장별 평가 기준이 끝난 상태로 오므로 바로 장치 점검으로 갈 수 있습니다.
 */
export interface FirstTakeResponse {
  /**
   * 튜토리얼 리포트를 이미 받은 계정에 샘플 피치가 없으면(지웠으면) BE 가 새로 만들지 않아 null 입니다.
   * `report_received` 가 false 면 늘 있습니다
   */
  pitch_id: string | null;
  /**
   * 튜토리얼 리포트를 이미 받았는지. 리포트는 계정당 한 번이라 true 면 이 피치로 새 Take 를 만들 수 없습니다
   * (409 `TUTORIAL_REPORT_USED`). 샘플 피치를 지우고 다시 받아도 같습니다
   */
  report_received: boolean;
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
  /** 발표자료에는 BE 가 센 쪽수가 붙습니다 (#298). 옛 발표자료는 null, #298 전의 BE 는 주지 않습니다 */
  presentation_versions: (VersionRef & { page_count?: number | null })[];
  script_versions: VersionRef[];
  evaluation_versions: VersionRef[];
}
