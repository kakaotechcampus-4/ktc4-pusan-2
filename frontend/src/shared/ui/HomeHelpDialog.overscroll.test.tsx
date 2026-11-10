import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { expect, it } from 'vitest';
import { HomeHelpDialog } from './HomeHelpDialog';

it('사용방법 창은 본문 스크롤은 허용하고 끝의 탄성 스크롤은 막는다 (#1140)', () => {
  const markup = renderToStaticMarkup(
    <MemoryRouter>
      <HomeHelpDialog onDismiss={() => {}} />
    </MemoryRouter>,
  );
  const dialogClasses = markup.match(/<dialog[^>]*class="([^"]+)"/)?.[1]?.split(/\s+/);

  expect(dialogClasses).toContain('overflow-y-auto');
  expect(dialogClasses).toContain('overscroll-none');
  expect(dialogClasses).not.toContain('overscroll-contain');
  expect(markup).toContain('sticky top-0');
});
