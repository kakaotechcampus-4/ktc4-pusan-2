import { apiRequest } from './client';
import type { UploadPresentationResponse } from '@/types/presentation';

/**
 * 발표자료 업로드. 서버는 S3 에 올리고 presigned URL 을 돌려줍니다 — 변환은 하지 않습니다.
 * 페이지 수와 렌더링은 FE 가 그 URL 을 pdf.js 로 열어서 정합니다.
 *
 * Content-Type 을 직접 넣지 않습니다. FormData 를 넘기면 브라우저가
 * multipart 경계(boundary)까지 붙여 줍니다 — 손으로 넣으면 경계가 빠져 서버가 못 읽습니다.
 */
export function uploadPresentation(pitchId: string, file: File) {
  const body = new FormData();
  // BE 파라미터 이름(`presentation_file: UploadFile`)과 같아야 합니다
  body.append('presentation_file', file);
  return apiRequest<UploadPresentationResponse>(`/api/pitches/add/${pitchId}/presentation`, {
    method: 'POST',
    body,
  });
}
