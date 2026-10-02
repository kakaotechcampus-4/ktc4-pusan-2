import { delay, http, HttpResponse } from 'msw';
import type { UploadPresentationResponse } from '@/types/presentation';

/**
 * 발표자료 업로드 목.
 *
 * 실서버는 S3 presigned URL 을 돌려줍니다. 목에는 S3 가 없으므로 **받은 파일 자체**를
 * blob URL 로 돌려줍니다 — 그래서 목 상태에서도 사용자가 고른 PDF 가 그대로 그려지고,
 * FE 는 실서버와 똑같이 "URL 을 받아서 그린다" 한 길만 탑니다.
 *
 * 핸들러는 페이지 쪽에서 돌기 때문에(`setupWorker`) `URL.createObjectURL` 을 쓸 수 있습니다.
 */
export const presentationHandlers = [
  http.post('*/api/pitches/add/:pitchId/presentation', async ({ request, params }) => {
    const form = await request.formData();
    const file = form.get('presentation_file');
    if (!(file instanceof File)) {
      return HttpResponse.json({ code: 'INVALID_FILE' }, { status: 422 });
    }

    // "올리는 중…" 화면을 눈으로 확인할 수 있게 조금 기다립니다
    await delay(1200);

    const body: UploadPresentationResponse = {
      message: 'Presentation uploaded successfully',
      presentation: {
        pitch_id: String(params.pitchId),
        presentation_version_id: crypto.randomUUID(),
        file_url: URL.createObjectURL(file),
      },
    };
    return HttpResponse.json(body);
  }),
];
