import { createPitch } from '@/shared/api/pitch';
import { uploadPresentation } from '@/shared/api/presentation';
import { ApiFailure } from '@/shared/api/tokenStore';
import { openPdf } from '@/shared/lib/pdf';
import { isCurrentDraft, useCreateStore } from './createStore';
import { fromPitchSaved, fromUploadedPresentation, toPitchRequest } from './lib/beAdapter';
import { infoOf, infoReady, titleFromFile, type SlideUploadFailure } from './lib/draft';

/**
 * 업로드 실패를 화면 문구로 가릅니다 (BE `pitch/exception.py`).
 * 열 수 없는 PDF(깨짐 · 암호 · PDF 아님)와 너무 복잡한 PDF 는 다시 올려도 같아서 따로 알리고,
 * 서버가 바쁠 때는 네트워크 탓으로 보이지 않게 합니다.
 */
function uploadFailureReason(error: unknown): SlideUploadFailure {
  if (!(error instanceof ApiFailure)) return 'upload';
  switch (error.code) {
    case 'INVALID_PRESENTATION':
      return 'invalid';
    case 'PRESENTATION_TOO_COMPLEX':
      return 'complex';
    case 'PRESENTATION_BUSY':
      return 'busy';
    default:
      return 'upload';
  }
}

/**
 * 슬라이드 올리기 — 업로드 → 장수 받기 → 버전에 채우기.
 *
 * ── 장수는 BE 가 센 숫자입니다 (#298) ─────────────────────────────────
 * BE 가 PDF 를 열어 센 쪽수로 Take 생성 때 대본 장 수와 맞는지 검사합니다. 화면도 같은 숫자를 써야
 * "맞는다"고 보였는데 시작에서 거절되는 일이 없습니다. 그래서 여기서 PDF 를 열지 않습니다 —
 * 미리보기는 뷰어가 엽니다. 못 열어도(실서버 S3 CORS 등) 버전은 서버와 같은 번호로 남습니다.
 * BE 가 쪽수를 주지 않을 때(#298 전)만 지금처럼 열어서 셉니다.
 *
 * ── 왜 컴포넌트 밖인가 ─────────────────────────────────────────────
 * 올리는 도중에 사이드바에서 대본으로 넘어가도 업로드는 끝까지 가야 합니다.
 * 컴포넌트의 콜백에 걸어 두면 화면을 떠나는 순간 결과를 받을 곳이 사라집니다.
 * 그래서 진행 상태를 스토어에 두고, 이 함수가 스토어에 직접 씁니다.
 *
 * ★ **고른 파일을 바로 그리지 않습니다.** 서버가 돌려준 URL 로만 그립니다 —
 *   화면에 보이는 것이 곧 저장된 것이고, 다시 들어올 때와 같은 길 하나만 탑니다.
 *
 * ── 피치가 없으면 여기서 만듭니다 (시안 `pitch-create`) ──────────────────────
 * 첫 단계가 슬라이드라 처음 올릴 때는 피치가 없습니다. 발표자료는 피치 아래에 올라가므로
 * 파일 이름을 제목으로, 시간은 기본값으로 피치부터 만들고(`POST /pitches/add`) 올립니다.
 * 새 발표의 날짜는 비워 둡니다. 제목과 시간은 발표정보에서 확인합니다.
 *
 * 실패는 스토어의 `slideUpload` 로 화면에 올리고, 이 함수는 던지지 않습니다.
 */
export async function uploadSlides(file: File): Promise<void> {
  const store = useCreateStore.getState();
  const { draftId } = store;
  let { pitchId } = store;
  // 업로드가 끝나기 전에 다른 빈 버전을 만들어도 시작한 자리에 채웁니다.
  const viewed = store.node === 'slides' ? store.version : store.viewedVersions.slides;
  const targetVersion = viewed ?? store.draft.slides.at(-1)?.version ?? null;
  // 두 번 눌러도 한 번만 올립니다
  if (store.slideUpload.status === 'uploading' || store.infoSaving) return;
  const set = store.setSlideUpload;

  set({ status: 'uploading', file });

  if (!pitchId) {
    const current = store.infoEdits ?? infoOf(store.draft);
    const info = { ...current, title: current.title.trim() || titleFromFile(file.name) };
    // 새 작성의 시간 기본값은 늘 알고 있습니다. 모르는 허용오차(수정 화면)는 여기 오지 않습니다
    if (!infoReady(info)) return set({ status: 'failed', file, reason: 'upload' });
    try {
      pitchId = fromPitchSaved(await createPitch(toPitchRequest(info)));
    } catch (error) {
      if (!isCurrentDraft(draftId)) return;
      console.error('[슬라이드] 피치를 만들지 못했습니다', { file: file.name, error });
      return set({ status: 'failed', file, reason: 'upload' });
    }
    // 그사이 새 피치를 시작했으면 앞 피치는 서버에 남고, 새 작성에는 넣지 않습니다
    if (!isCurrentDraft(draftId)) return;
    useCreateStore.getState().startFromFile(pitchId, info);
  }

  let uploaded;
  try {
    uploaded = fromUploadedPresentation(await uploadPresentation(pitchId, file));
  } catch (error) {
    // 그사이 새 피치를 시작했으면 앞 피치의 실패를 새 작성에 띄우지 않습니다
    if (!isCurrentDraft(draftId)) return;
    // 화면에는 한 줄만 보이므로 원인은 콘솔에 남깁니다 (404 피치 없음 · 네트워크 · 413 크기 …)
    console.error('[슬라이드] 업로드 실패', { pitchId, file: file.name, error });
    set({ status: 'failed', file, reason: uploadFailureReason(error) });
    return;
  }

  let pageCount = uploaded.pageCount;
  if (pageCount === null) {
    try {
      pageCount = (await openPdf(uploaded.fileUrl)).numPages;
    } catch (error) {
      if (!isCurrentDraft(draftId)) return;
      // 실서버에서는 대개 S3 CORS 입니다 — 브라우저가 URL 의 바이트를 읽지 못합니다
      console.error('[슬라이드] PDF 열기 실패', { url: uploaded.fileUrl, error });
      set({ status: 'failed', file, reason: 'open' });
      return;
    }
  }

  const result = {
    fileUrl: uploaded.fileUrl,
    presentationVersionId: uploaded.presentationVersionId,
    pageCount,
    fileName: file.name,
  };
  // 그사이 새 피치를 시작했으면 버립니다. 파일은 앞 피치(pitchId) 아래에 올라가 있으니 거기 남습니다
  if (!isCurrentDraft(draftId)) return;
  useCreateStore.getState().attachSlides(result, targetVersion);
  set({ status: 'idle' });
}

/**
 * 화면에서 부르는 입구. `uploadSlides` 는 실패를 스토어로 올리므로 여기까지 오는 건
 * 예상 밖의 오류뿐입니다 — 버리지 않고 남깁니다 (CLAUDE.md 9번).
 */
export function startSlideUpload(file: File): void {
  uploadSlides(file).catch((err: unknown) => {
    console.error('[슬라이드] 업로드 중 예상 밖의 오류', err);
  });
}
