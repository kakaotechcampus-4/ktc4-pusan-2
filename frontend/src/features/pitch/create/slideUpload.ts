import { uploadPresentation } from '@/shared/api/presentation';
import { openPdf } from '@/shared/lib/pdf';
import { isCurrentDraft, useCreateStore } from './createStore';
import { fromUploadedPresentation } from './lib/beAdapter';

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
 *
 * 실패는 스토어의 `slideUpload` 로 화면에 올리고, 이 함수는 던지지 않습니다.
 *
 * @param replace "파일 교체"로 바꿀 버전. 없으면 빈 자리에 새로 올립니다
 */
export async function uploadSlides(file: File, replace: number | null = null): Promise<void> {
  const store = useCreateStore.getState();
  const { pitchId, draftId } = store;
  // 업로드가 끝나기 전에 다른 빈 버전을 만들어도 시작한 자리에 채웁니다.
  const targetVersion = store.version ?? store.draft.slides.at(-1)?.version ?? null;
  // 두 번 눌러도 한 번만 올립니다. 피치가 없으면 올릴 곳이 없습니다 — 화면이 먼저 막습니다
  if (store.slideUpload.status === 'uploading' || !pitchId) return;
  const set = store.setSlideUpload;

  set({ status: 'uploading', file, replace });

  let uploaded;
  try {
    uploaded = fromUploadedPresentation(await uploadPresentation(pitchId, file));
  } catch (error) {
    // 그사이 새 피치를 시작했으면 앞 피치의 실패를 새 작성에 띄우지 않습니다
    if (!isCurrentDraft(draftId)) return;
    // 화면에는 한 줄만 보이므로 원인은 콘솔에 남깁니다 (404 피치 없음 · 네트워크 · 413 크기 …)
    console.error('[슬라이드] 업로드 실패', { pitchId, file: file.name, error });
    set({ status: 'failed', file, replace, reason: 'upload' });
    return;
  }

  let pageCount;
  try {
    pageCount = (await openPdf(uploaded.fileUrl)).numPages;
  } catch (error) {
    if (!isCurrentDraft(draftId)) return;
    // 실서버에서는 대개 S3 CORS 입니다 — 브라우저가 URL 의 바이트를 읽지 못합니다
    console.error('[슬라이드] PDF 열기 실패', { url: uploaded.fileUrl, error });
    set({ status: 'failed', file, replace, reason: 'open' });
    return;
  }

  const result = {
    pageCount,
    ...uploaded,
    fileName: file.name,
  };
  // 그사이 새 피치를 시작했으면 버립니다. 파일은 앞 피치(pitchId) 아래에 올라가 있으니 거기 남습니다
  if (!isCurrentDraft(draftId)) return;
  const latest = useCreateStore.getState();
  if (replace === null) latest.attachSlides(result, targetVersion);
  else latest.replaceSlides(replace, result);
  set({ status: 'idle' });
}

/**
 * 화면에서 부르는 입구. `uploadSlides` 는 실패를 스토어로 올리므로 여기까지 오는 건
 * 예상 밖의 오류뿐입니다 — 버리지 않고 남깁니다 (CLAUDE.md 9번).
 */
export function startSlideUpload(file: File, replace: number | null = null): void {
  uploadSlides(file, replace).catch((err: unknown) => {
    console.error('[슬라이드] 업로드 중 예상 밖의 오류', err);
  });
}
