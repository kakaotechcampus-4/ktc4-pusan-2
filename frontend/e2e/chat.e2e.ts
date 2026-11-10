import { expect, test, type Locator, type Page } from '@playwright/test';

/**
 * 대화 에이전트 E2E — `docs/ai/features/chat-agent.md` 12-4 "E2E 시험 (F9)" 의 시험 번호를 그대로 붙입니다.
 * 목(`src/mocks/chat.ts`)은 보낸 말로 시나리오를 고르고, 조회할 때마다 한 단계씩 나아갑니다.
 */

/** 목의 첫 피치(`src/mocks/home.ts`) */
const PITCH_ID = '10000000-0000-4000-8000-000000000001';
const PITCH_TITLE = '캡스톤 최종 발표';
/** 점수가 있는 Take(2, 72점) · 점수가 없는 Take(3) — `src/mocks/home.ts` */
const TAKE2_ID = '20000000-0000-4000-8000-000000000002';
const TAKE3_ID = '20000000-0000-4000-8000-000000000003';

/** 런처를 눌러 대화창을 엽니다 */
async function openChat(page: Page): Promise<Locator> {
  await page.getByRole('button', { name: '새미나이 열기', exact: true }).click();
  const panel = page.getByRole('dialog', { name: '새미나이' });
  await expect(panel).toBeVisible();
  return panel;
}

async function ask(panel: Locator, text: string): Promise<void> {
  await panel.getByPlaceholder('새미나이에게 메시지 보내기...').fill(text);
  await panel.getByRole('button', { name: '입력', exact: true }).click();
}

const scopeChip = (panel: Locator) => panel.getByRole('button', { name: /^범위:/ });

/** 답 문장 — 같은 글이 스크린 리더 알림(status)에도 있어서 문장 칸(`data-claim`) 안에서 찾습니다 */
const claim = (panel: Locator, text: string) =>
  panel.locator('[data-claim]').filter({ hasText: text });

/** 실행이 끝나 저장된 답 — 진행 중인 답(`data-live-run`)이 아니라 저장된 메시지(`data-message`) 안에서 찾습니다 */
const savedAnswer = (panel: Locator, text: string) =>
  panel
    .locator('[data-message]')
    // `has` 는 그 메시지 안에서 찾으므로 대화창(panel)이 아니라 페이지에서 시작한 locator 를 넘깁니다
    .filter({ has: panel.page().locator('[data-claim]', { hasText: text }) });

/** 메시지 보내기 요청 — 같은 실행을 이어 받았는지(새로 보내지 않았는지) 세는 데도 씁니다 */
const isSend = (url: string, method: string) =>
  method === 'POST' && new URL(url).pathname === '/api/chat/messages';

test('1. 홈에서 버튼 → 전체 범위로 질문 → 답과 근거 칩', async ({ page }) => {
  await page.goto('/');
  // 설정한 화면 크기(1440×900)가 실제로 적용됐는지 — 기기 설정이 덮어쓰지 않았는지 봅니다
  // 브라우저에서 도는 식은 문자열로 넘깁니다 — 시험 파일의 타입 설정에는 DOM 타입이 없습니다
  expect(await page.evaluate('[innerWidth, innerHeight]')).toEqual([1440, 900]);
  const panel = await openChat(page);
  // 범위 칩 — 홈에서 열면 전체 범위입니다 (12-4 "처음 범위는 대화창을 연 화면으로")
  await expect(scopeChip(panel)).toHaveAccessibleName('범위: 전체');

  await ask(panel, '지난번보다 뭐가 나아졌어?');

  // 기록 문장에는 근거 칩이 붙습니다 — 문장이 다 나온 뒤에 보입니다
  const chips = panel.getByRole('list', { name: '근거' }).first();
  await expect(chips).toBeVisible();
  await expect(chips.getByRole('listitem').first()).toBeVisible();
});

test('넓은 화면에서는 400px을 확보하고 좁은 화면에서는 본문 위에 표시한다', async ({ page }) => {
  await page.goto('/');
  const content = page.locator('.chat-layout-content');
  const header = content.getByRole('banner');
  const main = content.getByRole('main');
  await expect(header).toHaveCount(1);
  await expect(main).toBeVisible();
  const headerWidth = (await header.boundingBox())!.width;
  const mainWidth = (await main.boundingBox())!.width;
  const panel = await openChat(page);
  await expect(panel).toHaveCSS('width', '400px');
  await expect(content).toHaveCSS('padding-right', '400px');
  expect((await header.boundingBox())!.width).toBe(headerWidth - 400);
  expect((await main.boundingBox())!.width).toBeLessThan(mainWidth);

  await page.setViewportSize({ width: 375, height: 780 });
  await expect(content).toHaveCSS('padding-right', '0px');
  const usableWidth = (await content.boundingBox())!.width;
  await expect(panel).toHaveCSS('width', `${usableWidth}px`);
  const composer = panel.getByRole('textbox', { name: '채팅 메시지' });
  await composer.fill('모바일 입력 확인');
  await expect(composer).toBeInViewport();
  await expect(panel.getByRole('button', { name: '입력', exact: true })).toBeInViewport();
  await page.keyboard.press('Escape');
  await expect(panel).toHaveCount(0);
  await expect(page.getByRole('button', { name: '새미나이 열기', exact: true })).toBeFocused();

  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(content).toHaveCSS('padding-right', '0px');
  expect((await header.boundingBox())!.width).toBe(headerWidth);
  expect((await main.boundingBox())!.width).toBe(mainWidth);
});

test('모바일 패널은 배경을 비활성화하고 키보드 초점을 패널 안에 유지한다', async ({ page }) => {
  for (const width of [375, 400, 401, 430, 452, 760]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto('/');
    const panel = await openChat(page);
    await expect(panel).toHaveAttribute('aria-modal', 'true');
    await expect(page.locator('.chat-layout-content')).toHaveAttribute('inert', '');
    await expect(page.locator('[data-chat-launcher] > button')).toBeHidden();

    const first = panel.getByRole('button', { name: '새 대화', exact: true });
    const last = panel.getByRole('button', { name: '입력', exact: true });
    await last.focus();
    await page.keyboard.press('Tab');
    await expect(first).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(last).toBeFocused();

    // 기억 설정 등에서 입력창이 숨으면 패널 자체가 초점을 받을 수 있습니다.
    await panel.focus();
    await page.keyboard.press('Shift+Tab');
    await expect(last).toBeFocused();
    await panel.focus();
    await page.keyboard.press('Tab');
    await expect(first).toBeFocused();

    await page.keyboard.press('Escape');
    await expect(panel).toHaveCount(0);
    await expect(page.locator('.chat-layout-content')).not.toHaveAttribute('inert');
    await expect(page.getByRole('button', { name: '새미나이 열기', exact: true })).toBeFocused();
  }
});

test('모바일 패널을 열어도 문서 스크롤 위치가 유지되고 가림막에서 배경을 스크롤할 수 없다', async ({ page }) => {
  await page.setViewportSize({ width: 600, height: 800 });
  await page.goto('/');
  await page.evaluate(`(() => {
    const content = document.querySelector('.chat-layout-content');
    if (!content) throw new Error('채팅 레이아웃 본문이 없습니다');
    const spacer = document.createElement('div');
    spacer.style.height = '200vh';
    content.append(spacer);
    window.scrollTo(0, 400);
  })()`);
  const before = await page.evaluate('window.scrollY');
  expect(before).toBeGreaterThan(0);
  const panel = await openChat(page);
  await expect(panel).toHaveAttribute('aria-modal', 'true');
  await expect(page.locator('body')).toHaveCSS('overflow-y', 'hidden');
  await page.mouse.move(40, 300);
  await page.mouse.wheel(0, 500);
  await page.waitForTimeout(100);
  expect(await page.evaluate('window.scrollY')).toBe(before);
  await page.keyboard.press('Escape');
  await expect(panel).toHaveCount(0);
  await expect(page.locator('body')).not.toHaveCSS('overflow-y', 'hidden');
  expect(await page.evaluate('window.scrollY')).toBe(before);
});

test('761px 이상에서는 채팅을 열어도 본문을 비활성화하지 않는다', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 800 });
  await page.goto('/');
  const panel = await openChat(page);
  await expect(panel).toHaveAttribute('aria-modal', 'false');
  await expect(page.locator('.chat-layout-content')).not.toHaveAttribute('inert');
  await expect(page.locator('[data-chat-launcher] > button')).toBeVisible();
});

test('대본 매핑과 슬라이드 확인은 열린 채팅 위에 뷰포트 기준으로 표시한다', async ({ page }) => {
  await page.goto(`/pitch/${PITCH_ID}/edit`);
  const panel = await openChat(page);
  const composer = panel.getByRole('textbox', { name: '채팅 메시지' });
  await expect(page.locator('.chat-layout-content')).toHaveCSS('padding-right', '400px');
  const footer = page.locator('.pitch-create-square > footer');
  await expect(footer).toHaveCSS('right', '400px');
  expect(
    await page.evaluate(
      'document.documentElement.scrollWidth <= document.documentElement.clientWidth',
    ),
  ).toBe(true);
  await composer.fill('확인할 내용');
  const triggers = [
    [/^슬라이드 확인 ·/, '슬라이드 확인'],
    ['매핑 확인/수정', '매핑 확인·수정'],
  ] as const;
  for (const [trigger, title] of triggers) {
    const button = page.getByRole('button', { name: trigger });
    await button.click();
    const modal = page
      .locator('dialog:modal')
      .filter({ has: page.getByRole('heading', { name: title, exact: true }) });
    await expect(modal).toHaveCSS('width', '1200px');
    await expect(modal).toHaveCSS('height', '850px');
    await expect(page.locator('[data-chat-panel]')).toBeVisible();
    await expect(page.locator('.chat-layout-content')).toHaveCSS('padding-right', '400px');
    await page.keyboard.press('Escape');
    await expect(modal).toHaveCount(0);
    await expect(button).toBeFocused();
    await expect(composer).toHaveValue('확인할 내용');
  }
});

test('2. 리포트에서 열면 범위가 그 피치 → 할 일 목록 · 도구 기록이 있는 답', async ({ page }) => {
  await page.goto(`/takes/${TAKE3_ID}`);
  const header = page
    .getByRole('banner')
    .filter({ has: page.getByRole('link', { name: 'Take 기록', exact: true }) });
  await expect(header).toBeVisible();
  const headerWidth = (await header.boundingBox())!.width;
  const panel = await openChat(page);
  await expect(page.locator('.chat-layout-content')).toHaveCSS('padding-right', '400px');
  expect((await header.boundingBox())!.width).toBe(headerWidth - 400);
  expect(
    await page.evaluate(
      'document.documentElement.scrollWidth <= document.documentElement.clientWidth',
    ),
  ).toBe(true);
  await expect(scopeChip(panel)).toHaveAccessibleName(`범위: 피치: ${PITCH_TITLE}`);

  await ask(panel, '지난번보다 뭐가 나아졌어?');

  // 할 일 — 답 위에, 실행이 끝나면 모두 끝남 (F3)
  const plan = panel.getByRole('list', { name: '할 일' });
  await expect(plan).toBeVisible();
  await expect(plan.locator('[data-plan-status="DONE"]')).toHaveCount(3);

  // 확인한 것 — 답 아래 접혀 있다가 펼치면 도구 기록이 보입니다 (F7)
  await panel.getByText('확인한 것', { exact: true }).click();
  await expect(panel.getByText(/3장 시선 둘 다 낮음/)).toBeVisible();
});

test('3. 에이전트가 다른 피치를 열면 범위 칩이 바뀜', async ({ page }) => {
  await page.goto('/');
  const panel = await openChat(page);
  await expect(scopeChip(panel)).toHaveAccessibleName('범위: 전체');

  await ask(panel, '캡스톤 발표 열어서 지난번이랑 비교해 줘');

  // 답에 바꾼 범위를 알리고, 칩은 실행이 끝나 BE 가 범위를 확정한 뒤 바뀝니다 (L3-08)
  await expect(claim(panel, `${PITCH_TITLE}로 범위를 바꿨어요.`)).toBeVisible();
  await expect(scopeChip(panel)).toHaveAccessibleName(`범위: 피치: ${PITCH_TITLE}`);
});

test('4. 목표 저장 카드 → [되돌리기]', async ({ page }) => {
  await page.goto(`/takes/${TAKE3_ID}`);
  const panel = await openChat(page);

  await ask(panel, '3장 시선을 목표로 잡아 줘');

  // 목표 저장은 승인 카드를 거칩니다 (F10)
  await panel.getByRole('button', { name: '승인', exact: true }).click();
  await expect(claim(panel, '목표를 저장했어요.')).toBeVisible();

  const goal = panel.getByRole('group', { name: '대화에서 정한 목표' });
  await expect(goal).toBeVisible();
  const undo = goal.getByRole('button', { name: /^되돌리기/ });
  await undo.click();

  // 되돌린 뒤에는 다시 누를 수 없고 이유를 함께 읽습니다
  await expect(undo).toHaveAccessibleName(/되돌렸어요/);
  await expect(undo).toHaveAttribute('aria-disabled', 'true');
});

test('5. "바로 발표할래" → [연습 준비로] 버튼 → 누르면 장치 점검 · 리허설 화면에서 버튼 숨김', async ({
  page,
}) => {
  await page.goto(`/takes/${TAKE3_ID}`);
  const panel = await openChat(page);

  await ask(panel, '바로 발표할래');

  // 버튼 카드만 오고 저절로 옮기지 않습니다 (L3-31)
  const start = panel.getByRole('button', { name: '연습 준비로' });
  await expect(start).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/takes/${TAKE3_ID}$`));

  await start.click();
  await expect(page).toHaveURL(new RegExp(`/pitch/${PITCH_ID}/device-check$`));
  await expect(page.getByRole('heading', { level: 1, name: '시작 전 세팅' })).toBeVisible();
  await expect(page.getByRole('button', { name: '새미나이 열기', exact: true })).toHaveCount(0);
  await expect(panel).toHaveCount(0);

  // 리허설 화면에는 대화 버튼이 없습니다 (L3-01) — 화면이 그려진 뒤에 셉니다(그리기 전에도 0 이라 통과해 버림).
  // 장치 점검을 거치지 않고 바로 열면 리허설 화면이 '연습 중단' 안내를 그립니다 — 같은 리허설 경로의 화면입니다
  await page.goto(`/takes/${TAKE3_ID}/rehearsal`);
  await expect(page.getByRole('heading', { level: 1, name: '연습 중단' })).toBeVisible();
  await expect(page.getByRole('button', { name: '새미나이 열기', exact: true })).toHaveCount(0);
});

test('6. 최신 아님 배지 → 다시 묻기', async ({ page }) => {
  await page.goto(`/takes/${TAKE3_ID}`);
  const panel = await openChat(page);

  // 목의 지난 대화 — 그 뒤에 끝난 Take 가 있어 최신이 아닙니다(NEW_TAKE)
  await panel.getByRole('button', { name: '대화 목록' }).click();
  const past = panel.getByRole('list', { name: '지난 대화' }).getByRole('button', {
    name: /지난번보다 청중을 더 봤어\?/,
  });
  await expect(past).toContainText('최신 아님');
  await past.click();

  const answers = panel.locator('[data-message]');
  const old = answers.filter({ has: page.locator('[data-stale-reason]') });
  await expect(old).toHaveCount(1);
  await expect(old.locator('[data-stale-reason]')).toContainText('최신 아님');
  const before = await answers.count();
  await panel.getByRole('button', { name: /^최신 기준으로 다시 묻기/ }).click();

  // 같은 질문을 새 메시지로 보내고, 새 답에는 최신 아님 표시가 없습니다 (F5)
  const asked = panel.getByText('지난번보다 청중을 더 봤어?', { exact: true });
  await expect(asked).toHaveCount(2);
  // 새 답이 끝나 저장될 때까지 기다린 뒤, 그 답 자체를 봅니다
  await expect(answers).toHaveCount(before + 1);
  const fresh = answers.last();
  await expect(fresh.locator('[data-claim]').first()).toBeVisible();
  await expect(fresh).not.toHaveAttribute('data-stale');
  await expect(fresh.locator('[data-stale-reason]')).toHaveCount(0);
  // 지난 답의 표시는 그대로입니다
  await expect(old.locator('[data-stale-reason]')).toContainText('최신 아님');
});

interface OfficialReport {
  take_number: number;
  review: { score: number | null } | null;
}
const scoreOf = (report: OfficialReport) => ({
  takeNumber: report.take_number,
  score: report.review?.score ?? null,
});
const isReport = (url: string, takeId: string) =>
  new URL(url).pathname === `/api/takes/${takeId}/report`;
/** 공식 리포트를 다시 읽습니다 — 목(MSW)은 페이지 안의 요청만 받으므로 페이지에서 부릅니다 */
const officialReport = (page: Page, takeId: string) =>
  page.evaluate(async (id) => {
    const res = await fetch(`/api/takes/${id}/report`);
    return (await res.json()) as OfficialReport;
  }, takeId);

test('7. 다시 평가 → 원래 결과 · 참고 결과 나란히', async ({ page }) => {
  // 점수가 있는 Take 2 에서 묻습니다 — 목은 요청의 `page.take_id` 로 다시 평가할 Take 를 고릅니다
  const opened = page.waitForResponse((r) => isReport(r.url(), TAKE2_ID));
  await page.goto(`/takes/${TAKE2_ID}`);
  // 다시 평가의 "바꾸기 전"(72)이 화면이 받은 공식 리포트와 같은 Take · 같은 점수인지 먼저 봅니다
  expect(scoreOf((await (await opened).json()) as OfficialReport)).toEqual({
    takeNumber: 2,
    score: 72,
  });
  const panel = await openChat(page);

  const sent = page.waitForRequest((r) => isSend(r.url(), r.method()));
  await ask(panel, '이번 Take 다시 평가해 줘');
  const body = (await sent).postDataJSON() as { page: { take_id: string | null } };
  expect(body.page.take_id).toBe(TAKE2_ID);

  // 다시 평가도 승인 카드를 거칩니다 (F10) — 카드는 보던 Take 를 다시 평가한다고 알립니다
  const confirm = panel
    .locator('[data-action]')
    .filter({ hasText: '대본을 보고 한 발표로 다시 평가할까요?' });
  await expect(confirm).toContainText('Take 2를 대본 사용 기준으로 다시 평가해');
  await confirm.getByRole('button', { name: '승인', exact: true }).click();

  // 공식 리뷰는 그대로 두고, 원래 결과("바꾸기 전")와 참고 결과를 한 표에 나란히 보입니다
  await expect(panel.getByText('참고 결과 — 공식 리뷰는 그대로예요')).toBeVisible();
  const table = panel.getByRole('table');
  await expect(table.getByRole('columnheader', { name: '바꾸기 전' })).toBeVisible();
  await expect(table.getByRole('columnheader', { name: '참고 결과' })).toBeVisible();
  // Take 2 의 원래 점수(72) → 참고 결과(78)
  const score = table
    .getByRole('row')
    .filter({ has: page.getByRole('rowheader', { name: /^점수/ }) });
  await expect(score.getByRole('cell')).toHaveText(['72', '78']);
  // 공식 결과와 다르다는 표시(`official_differs`)는 없습니다
  await expect(panel.getByText('지금 기록 기준이에요')).toHaveCount(0);
  // 참고 결과를 만든 뒤에도 공식 리포트의 점수는 그대로입니다
  expect(scoreOf(await officialReport(page, TAKE2_ID))).toEqual({ takeNumber: 2, score: 72 });
});

/** 목의 개발용 조작(`src/mocks/chat.ts` 머리말 "콘솔") — 다음 보내기 · 실행을 실패시키거나 꺼짐으로 둡니다 */
type MockChatCall = ['disable'] | ['expire'] | ['runFail', string];
const mockChat = (page: Page, ...call: MockChatCall) =>
  page.evaluate(([name, arg]) => {
    const m = (globalThis as unknown as { mockChat: Record<string, (a?: string) => void> })
      .mockChat;
    m[name]!(arg);
  }, call);

test.describe('8. 취소 · 화면 이탈 뒤 복원 · AGENT_DISABLED · 시간 초과 안내', () => {
  test('취소하면 실행을 멈추고 알린다', async ({ page }) => {
    await page.goto('/');
    const panel = await openChat(page);
    await ask(panel, '지난번보다 뭐가 나아졌어?');

    await panel.getByRole('button', { name: '취소', exact: true }).click();
    await expect(panel.getByText('요청을 취소했어요.').first()).toBeVisible();
  });

  test('답을 만드는 중에 화면을 떠났다 돌아오면 그 대화를 이어 받는다', async ({ page }) => {
    await page.goto('/');
    const panel = await openChat(page);
    await ask(panel, '지난번보다 뭐가 나아졌어?');
    await expect(panel.getByRole('button', { name: '취소', exact: true })).toBeVisible();

    // 대화를 쓰지 않는 화면(개인정보 처리방침)으로 갔다가 돌아옵니다 — 대화창 · 연결이 내려갔다가 새로 만들어집니다.
    // 앱 안에서 옮깁니다(pushState · 뒤로 가기). 전체 새로고침은 목 서버(MSW)의 메모리 상태까지 지워서 쓰지 않습니다
    const sends: string[] = [];
    page.on('request', (r) => {
      if (isSend(r.url(), r.method())) sends.push(r.url());
    });
    await page.evaluate(
      "history.pushState(null, '', '/privacy'); dispatchEvent(new PopStateEvent('popstate'))",
    );
    await expect(page.getByRole('heading', { level: 1, name: '개인정보 처리방침' })).toBeVisible();
    await expect(page.getByRole('button', { name: '새미나이 열기', exact: true })).toHaveCount(0);
    await expect(page.getByRole('dialog', { name: '새미나이' })).toHaveCount(0);

    await page.goBack();
    await expect(page).toHaveURL(/\/$/);
    const again = page.getByRole('dialog', { name: '새미나이' });
    if (!(await again.isVisible())) await openChat(page);

    // 다시 보내지 않고 같은 실행을 이어 받아, 끝난 답이 저장된 메시지로 보입니다
    await expect(again.getByText('지난번보다 뭐가 나아졌어?', { exact: true })).toHaveCount(1);
    await expect(
      again.locator('[data-message]').getByRole('list', { name: '근거' }).first(),
    ).toBeVisible();
    expect(sends).toEqual([]);
  });

  test('꺼지면(AGENT_DISABLED) 문구 없이 버튼을 숨긴다', async ({ page }) => {
    await page.goto('/');
    const panel = await openChat(page);
    await mockChat(page, 'disable');
    await ask(panel, '지난번보다 뭐가 나아졌어?');

    await expect(page.getByRole('button', { name: '새미나이 열기', exact: true })).toHaveCount(0);
  });

  test('시간 초과면 다시 보내라고 안내한다', async ({ page }) => {
    await page.goto('/');
    const panel = await openChat(page);
    await mockChat(page, 'runFail', 'AGENT_TIMEOUT');
    await ask(panel, '지난번보다 뭐가 나아졌어?');

    await expect(panel.getByText('답이 늦어지고 있으니 다시 보내 주세요.').first()).toBeVisible();
  });
});

test('9. 피치 수정 평가기준 단계에서 사용법 질문 → 화면 상태가 요청에 실림 → 입력은 그대로', async ({
  page,
}) => {
  // 문서는 '피치 만들기'지만, 새 피치는 슬라이드 · 대본을 올려야 평가기준에 닿습니다 — 같은 화면(`PitchCreatePage`)의
  // 수정 경로로 시험합니다. 화면 상태 이름만 `pitch_edit` 으로 다릅니다
  await page.goto(`/pitch/${PITCH_ID}/edit`);
  const criteria = page.getByRole('textbox', { name: '평가기준 작성' });
  const typed = '시장 규모와 출처를 말한다';
  await criteria.fill(typed);

  const panel = await openChat(page);
  const sent = page.waitForRequest((r) => isSend(r.url(), r.method()));
  await ask(panel, '평가기준은 어떻게 쓰면 돼?');

  // 보낸 요청에 지금 화면 상태(평가기준 단계 · 저장 전 입력)가 실립니다 (12-4 Screen)
  const body = (await sent).postDataJSON() as {
    page: { screen: { name: string; step: string; draft: { criteria_text: string } } | null };
  };
  expect(body.page.screen).toMatchObject({
    name: 'pitch_edit',
    step: 'criteria',
    draft: { criteria_text: typed },
  });

  // 답은 입력 위치 · 저장 방법을 안내하고, 입력란은 건드리지 않습니다
  await expect(claim(panel, '저장하면 다음 연습부터 적용돼요.')).toBeVisible();
  await expect(criteria).toHaveValue(typed);
});

test.describe('10. 승인 카드(전 → 후) → [승인] · [거절] · 만료 · 대기 중 새 메시지', () => {
  /** 목표 저장 요청 → 승인 카드가 뜰 때까지 */
  async function askToSave(page: Page): Promise<{ panel: Locator; card: Locator }> {
    await page.goto(`/takes/${TAKE3_ID}`);
    const panel = await openChat(page);
    await ask(panel, '3장 시선을 목표로 잡아 줘');
    const card = panel.locator('[data-action]').filter({ has: page.locator('[data-goal-change]') });
    await expect(card).toBeVisible();
    // 전 → 후 — 지금 목표와 새 목표를 나란히 보입니다
    await expect(card.getByText('지금 목표')).toBeVisible();
    await expect(card.getByText('새 목표')).toBeVisible();
    return { panel, card };
  }

  test('[승인] → 목표 저장됨', async ({ page }) => {
    const { panel, card } = await askToSave(page);
    await card.getByRole('button', { name: '승인', exact: true }).click();
    await expect(claim(panel, '목표를 저장했어요.')).toBeVisible();
    await expect(panel.getByRole('group', { name: '대화에서 정한 목표' })).toBeVisible();
  });

  test('[거절] → "목표를 저장하지 않았어요."', async ({ page }) => {
    const { panel, card } = await askToSave(page);
    await card.getByRole('button', { name: '거절', exact: true }).click();
    await expect(claim(panel, '목표를 저장하지 않았어요.')).toBeVisible();
    await expect(panel.getByRole('group', { name: '대화에서 정한 목표' })).toHaveCount(0);
  });

  test('만료되면 만료 문장', async ({ page }) => {
    const { panel } = await askToSave(page);
    await mockChat(page, 'expire');
    await expect(
      claim(panel, '확인 시간이 지나 실행하지 않았어요. 필요하면 다시 말해 주세요.'),
    ).toBeVisible();
  });

  test('승인 대기 중 새 메시지를 보내면 앞 확인 요청은 취소 문장', async ({ page }) => {
    const { panel } = await askToSave(page);
    await ask(panel, '지난번보다 뭐가 나아졌어?');
    await expect(claim(panel, '새 메시지를 받아 앞의 확인 요청은 취소했어요.')).toBeVisible();
  });
});

test('11. 답 문장이 글자 효과로 나옴 → 일반 조언에만 라벨 · 기록 문장에 근거 칩', async ({
  page,
}) => {
  await page.goto('/');
  const panel = await openChat(page);
  await ask(panel, '지난번보다 뭐가 나아졌어?');

  // 채워지는 중인 문장은 화면 읽기 프로그램에서 숨깁니다 — 글자 효과가 도는 동안만 생기는 표시입니다 (F12)
  const live = panel.locator('[data-live-run]');
  await expect(live.locator('[data-claim] > span[aria-hidden="true"]').first()).toBeAttached();

  // 다 나오면: 일반 조언 문장에만 라벨, 기록 문장에는 근거 칩 (F13)
  await expect(live.locator('[data-chat-skip]')).toHaveAttribute('aria-disabled', 'true');
  const general = claim(panel, '말하기 전에 숨을 고르고 청중 쪽을 먼저 보면 시작이 안정돼요.');
  await expect(general).toContainText('일반 조언 · 기록과 자료로 확인한 것이 아니에요');
  await expect(general.getByRole('list', { name: '근거' })).toHaveCount(0);
  await expect(panel.getByText('일반 조언 · 기록과 자료로 확인한 것이 아니에요')).toHaveCount(1);
  const record = panel.locator('[data-claim]').first();
  await expect(record.getByRole('list', { name: '근거' })).toBeVisible();
});

test.describe('11. 지난 답은 글자 효과 없이 처음부터 전체', () => {
  /** 글자 효과가 도는 동안만 생기는 표시 — 기다리지 않고 바로 셉니다(기다리면 효과가 끝난 뒤 0 이 되어 통과해 버림) */
  const typingNow = (panel: Locator) =>
    panel.locator('[data-claim] > span[aria-hidden="true"]').count();

  /** 목에 늘 있는 지난 대화(`지난번보다 청중을 더 봤어?`)를 엽니다 */
  async function openPast(panel: Locator): Promise<void> {
    await panel.getByRole('button', { name: '대화 목록' }).click();
    await panel
      .getByRole('list', { name: '지난 대화' })
      .getByRole('button', { name: /지난번보다 청중을 더 봤어\?/ })
      .click();
  }

  test('지난 대화를 고르면 저장된 답이 효과 없이 보인다', async ({ page }) => {
    await page.goto('/');
    const panel = await openChat(page);
    await openPast(panel);
    await expect(panel.locator('[data-message] [data-claim]').first()).toBeVisible();
    expect(await typingNow(panel)).toBe(0);
    await expect(panel.locator('[data-live-run]')).toHaveCount(0);
  });

  test('새로고침해도 보던 대화를 다시 열고 답은 효과 없이 보인다 (복원)', async ({ page }) => {
    // 목 서버는 새로고침하면 처음 상태로 돌아가서, 새로 보낸 대화가 아니라 목에 늘 있는 지난 대화로 봅니다
    await page.goto('/');
    const panel = await openChat(page);
    await openPast(panel);
    await expect(panel.getByText('지난번보다 청중을 더 봤어?', { exact: true })).toBeVisible();

    await page.reload();
    const again = page.getByRole('dialog', { name: '새미나이' });
    if (!(await again.isVisible())) await openChat(page);
    // 대화 ID 는 sessionStorage 에 남아 있어 목록을 고르지 않아도 그 대화가 열립니다
    await expect(again.getByText('지난번보다 청중을 더 봤어?', { exact: true })).toBeVisible();
    await expect(again.locator('[data-message] [data-claim]').first()).toBeVisible();
    expect(await typingNow(again)).toBe(0);
    await expect(again.locator('[data-live-run]')).toHaveCount(0);
  });
});

test.describe("12. '기억했어요' 카드 → [지우기] · 설정에서 끄기 · '기억 없이' 대화에서는 카드 없음", () => {
  const REMEMBER = '앞으로 짧게 설명해 줘, 기억해';
  /** 목의 기억 시나리오 마지막 문장 — 이 문장이 든 답이 저장되면 실행이 끝난 것입니다(카드는 끝날 때 붙음) */
  const LAST = '핵심을 먼저 말하고 이유는 짧게 덧붙여 볼게요.';

  /** 실행이 끝나 답이 저장될 때까지 기다린 뒤 기억 카드가 없는지 봅니다 */
  async function expectNoMemoryCard(panel: Locator): Promise<void> {
    const answer = savedAnswer(panel, LAST);
    await expect(answer).toHaveCount(1);
    await expect(panel.locator('[data-live-run]')).toHaveCount(0);
    await expect(answer.locator('[data-memory-item]')).toHaveCount(0);
    await expect(panel.locator('[data-memory-item]')).toHaveCount(0);
  }

  test("'기억했어요' 카드 → [지우기]", async ({ page }) => {
    await page.goto('/');
    const panel = await openChat(page);
    await ask(panel, REMEMBER);

    // 같은 시나리오에서 카드가 실제로 붙는지 — 아래 '카드 없음' 두 시험이 헛돌지 않는지 견줍니다
    const card = savedAnswer(panel, LAST).locator('[data-memory-item]');
    await expect(card).toContainText('기억했어요');
    await card.getByRole('button', { name: /^지우기/ }).click();
    await expect(card).toContainText('지운 기억이에요.');
  });

  test('설정에서 끄면 기억 카드가 오지 않는다', async ({ page }) => {
    await page.goto('/');
    const panel = await openChat(page);

    await panel.getByRole('button', { name: '기억 설정' }).click();
    const toggle = panel.getByRole('switch', { name: '코칭 프로필 기억' });
    await expect(toggle).toHaveAttribute('aria-checked', 'true');
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-checked', 'false');
    await panel.getByRole('button', { name: '돌아가기' }).click();

    await ask(panel, REMEMBER);
    await expectNoMemoryCard(panel);
  });

  test("'기억 없이' 대화에서는 기억 카드가 오지 않는다", async ({ page }) => {
    await page.goto('/');
    const panel = await openChat(page);

    // 스위치 input 은 sr-only 라 꾸민 라벨이 덮고 있습니다 — 사람처럼 보이는 글자 라벨을 눌러 켜고, 켜졌는지 봅니다
    await panel.getByText('기억 없이 대화하기', { exact: true }).click();
    await expect(panel.getByRole('switch', { name: '기억 없이 대화하기' })).toBeChecked();
    await ask(panel, REMEMBER);
    await expectNoMemoryCard(panel);
  });
});
