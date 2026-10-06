/**
 * POST /api/pitches/add/{pitch_id}/presentation — 발표자료(PDF) 업로드.
 * BE 응답 그대로 snake_case 입니다 (`UploadPresentationResultDTO`).
 */
export interface UploadedPresentation {
  pitch_id: string;
  presentation_version_id: string;
  /**
   * S3 presigned GET URL. **1시간 뒤 만료됩니다** — 저장해 두고 다시 쓰지 않습니다.
   * 다시 들어올 때는 조회 API 로 새 URL 을 받습니다.
   */
  file_url: string;
}

/**
 * GET /api/pitches/{pitch_id}/presentations/{presentation_version_id} — 발표자료 한 버전.
 * BE 그대로 snake_case 입니다 (`PresentationDetailDTO`). 부를 때마다 새 presigned URL 을 줍니다.
 */
export interface PresentationDetail {
  pitch_id: string;
  presentation_version_id: string;
  version: number;
  /** S3 presigned GET URL. 1시간 뒤 만료됩니다 */
  file_url: string;
  description: string | null;
  created_at: string;
}

export interface UploadPresentationResponse {
  message: string;
  presentation: UploadedPresentation;
}
