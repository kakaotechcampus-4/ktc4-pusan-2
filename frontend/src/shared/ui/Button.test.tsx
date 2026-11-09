// @vitest-environment jsdom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { Button } from './Button';

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

// aria-disabled 는 마우스만 막습니다. Enter · Space 가 내는 click 은 jsdom 의 click() 과 같습니다
it('aria-disabled 면 키보드가 낸 click 에도 onClick 을 부르지 않고 제출하지 않는다 (#874 리뷰)', async () => {
  const onClick = vi.fn();
  const onSubmit = vi.fn((event: SubmitEvent) => event.preventDefault());
  await act(async () =>
    root.render(
      <form>
        <Button aria-disabled onClick={onClick}>
          저장하는 중…
        </Button>
        <Button type="submit" aria-disabled="true">
          보내는 중…
        </Button>
      </form>,
    ),
  );
  host.querySelector('form')!.addEventListener('submit', onSubmit);
  const [pending, submit] = host.querySelectorAll('button');
  await act(async () => pending.click());
  await act(async () => submit.click());
  expect(onClick).not.toHaveBeenCalled();
  expect(onSubmit).not.toHaveBeenCalled();
});

it('aria-disabled 가 거짓이면 onClick 을 그대로 부른다', async () => {
  const onClick = vi.fn();
  await act(async () =>
    root.render(
      <Button aria-disabled={false} onClick={onClick}>
        저장
      </Button>,
    ),
  );
  await act(async () => host.querySelector('button')!.click());
  expect(onClick).toHaveBeenCalledTimes(1);
});
