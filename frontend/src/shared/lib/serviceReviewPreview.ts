import { serviceReviewPreview } from '@/shared/api/serviceReview';

const key = (takeId: string) => `pitchcoach:review-preview:${takeId}`;

/** A session-only mock policy; fail closed if browser storage is unavailable. */
export function markReviewPreviewReady(takeId: string) {
  if (!serviceReviewPreview) return;
  try {
    if (sessionStorage.getItem(key(takeId)) === null) sessionStorage.setItem(key(takeId), 'ready');
  } catch {
    /* The rehearsal must still finish when storage is unavailable. */
  }
}

export function claimReviewPreview(takeId: string): boolean {
  if (!serviceReviewPreview) return false;
  try {
    if (sessionStorage.getItem(key(takeId)) !== 'ready') return false;
    sessionStorage.setItem(key(takeId), 'shown');
    return true;
  } catch {
    return false;
  }
}

/** Leaving the first report view ends its unused preview opportunity. */
export function expireReviewPreview(takeId: string) {
  if (!serviceReviewPreview) return;
  try {
    if (sessionStorage.getItem(key(takeId)) === 'ready')
      sessionStorage.setItem(key(takeId), 'expired');
  } catch {
    // Storage failure must not interrupt navigation.
  }
}
