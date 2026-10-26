// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ServiceReviewDialog } from './ServiceReviewDialog';
import { submitServiceReview } from '@/shared/api/serviceReview';

vi.mock('@/shared/api/serviceReview', () => ({ submitServiceReview: vi.fn() }));
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  vi.mocked(submitServiceReview).mockReset();
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});
const buttons = () => [...host.querySelectorAll('button')];

it('requires a rating, retains it after failure, prevents duplicate sends and continues on success', async () => {
  const next = vi.fn();
  await act(async () => root.render(<ServiceReviewDialog takeId="t1" onContinue={next} />));
  const send = buttons().find((b) => b.textContent === '별점 제출')!;
  expect(send.disabled).toBe(true);
  const rating = host.querySelector<HTMLInputElement>('input[value="3"]')!;
  await act(async () => rating.click());
  vi.mocked(submitServiceReview).mockRejectedValueOnce(new Error('offline'));
  await act(async () => send.click());
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
  expect(rating.checked).toBe(true);
  expect(next).not.toHaveBeenCalled();
  let resolve!: () => void;
  vi.mocked(submitServiceReview).mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = () => done({ take_id: 't1', rating: 3 });
      }),
  );
  await act(async () => {
    send.click();
    send.click();
  });
  expect(submitServiceReview).toHaveBeenCalledTimes(2);
  expect(send.disabled).toBe(true);
  await act(async () => resolve());
  expect(next).toHaveBeenCalledOnce();
});

it.each(['나중에', '닫기', 'Escape'])('continues without sending via %s', async (action) => {
  const next = vi.fn();
  await act(async () => root.render(<ServiceReviewDialog takeId="t1" onContinue={next} />));
  await act(async () => {
    if (action === 'Escape')
      host.querySelector('dialog')!.dispatchEvent(new Event('cancel', { cancelable: true }));
    else
      buttons()
        .find((b) => b.textContent === action)!
        .click();
  });
  expect(next).toHaveBeenCalledOnce();
  expect(submitServiceReview).not.toHaveBeenCalled();
});

it('allows leaving during submission and ignores a late success', async () => {
  let resolve!: () => void;
  vi.mocked(submitServiceReview).mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = () => done({ take_id: 't1', rating: 5 });
      }),
  );
  const next = vi.fn();
  await act(async () => root.render(<ServiceReviewDialog takeId="t1" onContinue={next} />));
  await act(async () => host.querySelector<HTMLInputElement>('input[value="5"]')!.click());
  await act(async () =>
    buttons()
      .find((b) => b.textContent === '별점 제출')!
      .click(),
  );
  await act(async () =>
    buttons()
      .find((b) => b.textContent === '나중에')!
      .click(),
  );
  await act(async () => root.render(null));
  await act(async () => resolve());
  expect(next).toHaveBeenCalledOnce();
});
