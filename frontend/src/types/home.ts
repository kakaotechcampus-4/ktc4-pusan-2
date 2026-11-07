import type { TakeEndReason, TakeStatus } from './api';

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
  /** 점수 유무가 아니라 이 값으로 분석 상태를 판단합니다 (BE #490) */
  status: TakeStatus;
  /** 사용자가 끝냈거나 아직 끝나지 않았으면 null. 키는 항상 옵니다 */
  end_reason: TakeEndReason | null;
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
  /**
   * 지금 지울 수 있는가 (BE #1055). 연습 중이거나 분석이 실제로 도는 Take 가 있으면 false 이고, 그때 삭제는
   * 409 `PITCH_BUSY` 입니다. 멈춘 분석은 막지 않습니다 — 막으면 그 피치를 영영 지울 수 없어서입니다
   */
  can_delete: boolean;
}

export interface PitchListResponse {
  pitches: HomePitch[];
}
