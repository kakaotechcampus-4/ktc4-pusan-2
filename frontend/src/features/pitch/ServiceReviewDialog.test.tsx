// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ServiceReviewDialog } from './ServiceReviewDialog';
import { ApiFailure } from '@/shared/api/tokenStore';
import { getServiceReviewState, submitServiceReview } from '@/shared/api/serviceReview';

vi.mock('@/shared/api/serviceReview', () => ({
  submitServiceReview: vi.fn(),
  getServiceReviewState: vi.fn().mockResolvedValue({ submitted: true }),
}));
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
  await act(async () => root.render(<ServiceReviewDialog onContinue={next} />));
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
        resolve = () => done({ submitted: true, created_at: '2026-10-27T00:00:00Z', rating: 3 });
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
  await act(async () => root.render(<ServiceReviewDialog onContinue={next} />));
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
        resolve = () => done({ submitted: true, created_at: '2026-10-27T00:00:00Z', rating: 5 });
      }),
  );
  const next = vi.fn();
  await act(async () => root.render(<ServiceReviewDialog onContinue={next} />));
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

async function setOpinion(value: string) {
  await act(async () => {
    const input = host.querySelector('textarea')!;
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(
      input,
      value,
    );
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
it('submits the optional opinion and retains identical content on retry', async () => {
  await act(async () => root.render(<ServiceReviewDialog onContinue={vi.fn()} />));
  await act(async () => host.querySelector<HTMLInputElement>('input[value="4"]')!.click());
  await setOpinion('가'.repeat(2001));
  expect(buttons().find((b) => b.textContent === '별점 제출')!.disabled).toBe(true);
  await setOpinion('  좋은 서비스  ');
  await chooseCategory('POSITIVE');
  vi.mocked(submitServiceReview).mockRejectedValue(new Error('lost'));
  await act(async () =>
    buttons()
      .find((b) => b.textContent === '별점 제출')!
      .click(),
  );
  expect(host.querySelector('textarea')!.value).toBe('  좋은 서비스  ');
  expect(host.querySelector('select')!.disabled).toBe(true);
  expect(host.querySelector('select')!.value).toBe('POSITIVE');
  await act(async () =>
    buttons()
      .find((b) => b.textContent === '다시 제출')!
      .click(),
  );
  expect(vi.mocked(submitServiceReview).mock.calls.map((c) => c[0])).toEqual([
    { rating: 4, suggestion: '좋은 서비스', category: 'POSITIVE' },
    { rating: 4, suggestion: '좋은 서비스', category: 'POSITIVE' },
  ]);
});
it('refreshes server state and closes when another tab has already submitted', async () => {
  const close = vi.fn();
  vi.mocked(submitServiceReview).mockRejectedValue(
    new ApiFailure(409, 'SERVICE_FEEDBACK_ALREADY_SUBMITTED'),
  );
  await act(async () => root.render(<ServiceReviewDialog onContinue={close} />));
  await act(async () => host.querySelector<HTMLInputElement>('input[value="4"]')!.click());
  await act(async () =>
    buttons()
      .find((b) => b.textContent === '별점 제출')!
      .click(),
  );
  expect(getServiceReviewState).toHaveBeenCalled();
  expect(close).toHaveBeenCalledOnce();
});
it('announces missing report eligibility without claiming success', async () => {
  const close = vi.fn();
  vi.mocked(submitServiceReview).mockRejectedValue(
    new ApiFailure(409, 'SERVICE_FEEDBACK_NOT_ELIGIBLE'),
  );
  await act(async () => root.render(<ServiceReviewDialog onContinue={close} />));
  await act(async () => host.querySelector<HTMLInputElement>('input[value="4"]')!.click());
  await act(async () =>
    buttons()
      .find((b) => b.textContent === '별점 제출')!
      .click(),
  );
  expect(host.querySelector('[role="alert"]')!.textContent).toContain('완료 리포트가 없어요');
  expect(close).not.toHaveBeenCalled();
});

async function chooseCategory(value: string) {
  await act(async () => {
    const select = host.querySelector('select')!;
    select.value = value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

it.each(['POSITIVE', 'IMPROVEMENT', 'PROBLEM', 'QUESTION', 'OTHER'])(
  'sends selected popup category %s',
  async (category) => {
    await act(async () => root.render(<ServiceReviewDialog onContinue={vi.fn()} />));
    expect(host.querySelector('select')!.value).toBe('OTHER');
    expect(host.querySelectorAll('option')).toHaveLength(5);
    await act(async () => host.querySelector<HTMLInputElement>('input[value="4"]')!.click());
    await setOpinion('의견');
    await chooseCategory(category);
    await act(async () =>
      buttons()
        .find((b) => b.textContent === '별점 제출')!
        .click(),
    );
    expect(vi.mocked(submitServiceReview).mock.calls[0]![0]).toEqual({
      rating: 4,
      suggestion: '의견',
      category,
    });
  },
);
it('allows changing category after 422 and clears it when the account form remounts', async () => {
  await act(async () => root.render(<ServiceReviewDialog key="a" onContinue={vi.fn()} />));
  await act(async () => host.querySelector<HTMLInputElement>('input[value="4"]')!.click());
  await chooseCategory('QUESTION');
  vi.mocked(submitServiceReview).mockRejectedValueOnce(new ApiFailure(422, 'VALIDATION_ERROR'));
  await act(async () =>
    buttons()
      .find((b) => b.textContent === '별점 제출')!
      .click(),
  );
  expect(host.querySelector('select')!.disabled).toBe(false);
  await chooseCategory('PROBLEM');
  await act(async () =>
    buttons()
      .find((b) => b.textContent === '다시 제출')!
      .click(),
  );
  expect(vi.mocked(submitServiceReview).mock.calls[1]![0].category).toBe('PROBLEM');
  await act(async () => root.render(<ServiceReviewDialog key="b" onContinue={vi.fn()} />));
  expect(host.querySelector('select')!.value).toBe('OTHER');
});
