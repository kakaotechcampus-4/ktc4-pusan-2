import { delay, http, HttpResponse } from 'msw';
import { countPdfPages } from '@/shared/lib/pdf';
import type { PresentationDetail, UploadPresentationResponse } from '@/types/presentation';

/** 올린 파일. 리허설이 버전 id 로 다시 받을 때(GET /presentations/{id}) 같은 파일을 돌려줍니다 */
const uploaded = new Map<
  string,
  { pitchId: string; url: string; version: number; pageCount: number }
>();

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

    const pitchId = String(params.pitchId);
    const id = crypto.randomUUID();
    const url = URL.createObjectURL(file);
    // BE 처럼 쪽수를 세서 돌려줍니다 (#298). 열리지 않으면 BE 처럼 400 INVALID_PRESENTATION
    // 캐시에 남기지 않아 뷰어가 실서버처럼 따로 엽니다. 열기 실패(S3 CORS · 만료)는 목에서 재현되지 않습니다
    let pageCount;
    try {
      pageCount = await countPdfPages(url);
    } catch {
      URL.revokeObjectURL(url);
      return HttpResponse.json({ code: 'INVALID_PRESENTATION' }, { status: 400 });
    }
    const version = [...uploaded.values()].filter((u) => u.pitchId === pitchId).length + 1;
    uploaded.set(id, { pitchId, url, version, pageCount });

    const body: UploadPresentationResponse = {
      message: 'Presentation uploaded successfully',
      presentation: {
        pitch_id: pitchId,
        presentation_version_id: id,
        file_url: url,
        page_count: pageCount,
      },
    };
    return HttpResponse.json(body);
  }),

  // BE PresentationDetailDTO 모양 그대로. 모르는 버전이면 BE 처럼 404
  http.get('*/api/pitches/:pitchId/presentations/:versionId', ({ params }) => {
    const id = String(params.versionId);
    const found = uploaded.get(id);
    if (!found) {
      return HttpResponse.json({ code: 'PRESENTATION_VERSION_NOT_FOUND' }, { status: 404 });
    }
    const body: PresentationDetail = {
      pitch_id: found.pitchId,
      presentation_version_id: id,
      version: found.version,
      file_url: found.url,
      page_count: found.pageCount,
      description: null,
      created_at: new Date().toISOString().slice(0, 10),
    };
    return HttpResponse.json(body);
  }),
];
