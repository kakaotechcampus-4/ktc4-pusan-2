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
  /**
   * BE 가 PDF 를 열어 센 쪽수 (BE #298). 이 숫자로 Take 생성 때 대본 장 수와 맞는지 검사하므로
   * 화면도 이 숫자를 씁니다. #298 전의 BE 는 주지 않습니다.
   */
  page_count?: number;
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
  /** BE 가 센 쪽수 (#298). 쪽수를 아직 채우지 않은 옛 발표자료는 null, #298 전의 BE 는 주지 않습니다 */
  page_count?: number | null;
  description: string | null;
  created_at: string;
}

export interface UploadPresentationResponse {
  message: string;
  presentation: UploadedPresentation;
}
