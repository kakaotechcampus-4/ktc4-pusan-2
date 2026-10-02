import { uploadPresentation } from '@/shared/api/presentation';
import { openPdf } from '@/shared/lib/pdf';
import { useCreateStore } from './createStore';

/**
 * 슬라이드 올리기 — 업로드 → 받은 URL 로 열기 → 장수 세기 → 버전에 채우기.
 *
 * ── 왜 컴포넌트 밖인가 ─────────────────────────────────────────────
 * 올리는 도중에 사이드바에서 대본으로 넘어가도 업로드는 끝까지 가야 합니다.
 * 컴포넌트의 콜백에 걸어 두면 화면을 떠나는 순간 결과를 받을 곳이 사라집니다.
 * 그래서 진행 상태를 스토어에 두고, 이 함수가 스토어에 직접 씁니다.
 *
 * ★ **고른 파일을 바로 그리지 않습니다.** 서버가 돌려준 URL 로만 그립니다 —
 *   화면에 보이는 것이 곧 저장된 것이고, 다시 들어올 때와 같은 길 하나만 탑니다.
 */
export async function uploadSlides(file: File): Promise<void> {
  const store = useCreateStore.getState();
  // 두 번 눌러도 한 번만 올립니다
  if (store.slideUpload.status === 'uploading') return;

  // ★ 피치 생성 API 가 아직 붙지 않아 pitchId 는 늘 null 입니다. 목은 어떤 id 에도 답하지만,
  //   실서버에서는 없는 피치라 업로드가 거절됩니다 — 생성이 붙으면 이 줄이 진짜 id 를 씁니다.
  const pitchId = store.pitchId ?? store.draftId;
  const set = useCreateStore.getState().setSlideUpload;

  set({ status: 'uploading', file });

  let uploaded;
  try {
    ({ presentation: uploaded } = await uploadPresentation(pitchId, file));
  } catch (error) {
    // 화면에는 한 줄만 보이므로 원인은 콘솔에 남깁니다 (404 피치 없음 · 네트워크 · 413 크기 …)
    console.error('[슬라이드] 업로드 실패', { pitchId, file: file.name, error });
    set({ status: 'failed', file, reason: 'upload' });
    return;
  }

  let pageCount;
  try {
    pageCount = (await openPdf(uploaded.file_url)).numPages;
  } catch (error) {
    // 실서버에서는 대개 S3 CORS 입니다 — 브라우저가 URL 의 바이트를 읽지 못합니다
    console.error('[슬라이드] PDF 열기 실패', { url: uploaded.file_url, error });
    set({ status: 'failed', file, reason: 'open' });
    return;
  }

  useCreateStore.getState().attachSlides({
    pageCount,
    fileUrl: uploaded.file_url,
    presentationVersionId: uploaded.presentation_version_id,
  });
  set({ status: 'idle' });
}
