// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest';
import {
  claimReviewPreview,
  markReviewPreviewReady,
  expireReviewPreview,
} from './serviceReviewPreview';
vi.mock('@/shared/api/serviceReview', () => ({ serviceReviewPreview: true }));
beforeEach(() => sessionStorage.clear());

it('does not prompt for an old report, and consumes a completed Take only once', () => {
  expect(claimReviewPreview('old')).toBe(false);
  markReviewPreviewReady('new');
  expect(claimReviewPreview('new')).toBe(true);
  expect(claimReviewPreview('new')).toBe(false);
  markReviewPreviewReady('new');
  expect(claimReviewPreview('new')).toBe(false);
  markReviewPreviewReady('next');
  expect(claimReviewPreview('next')).toBe(true);
});

it('does not re-arm a Take after an unused opportunity expired', () => {
  markReviewPreviewReady('old');
  expireReviewPreview('old');
  markReviewPreviewReady('old');
  expect(claimReviewPreview('old')).toBe(false);
  markReviewPreviewReady('next');
  expect(claimReviewPreview('next')).toBe(true);
});
