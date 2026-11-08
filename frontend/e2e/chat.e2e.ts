import { expect, test, type Locator, type Page } from '@playwright/test';

/**
 * 대화 에이전트 E2E — `docs/ai/features/chat-agent.md` 12-4 "E2E 시험 (F9)" 의 시험 번호를 그대로 붙입니다.
 * 목(`src/mocks/chat.ts`)은 보낸 말로 시나리오를 고르고, 조회할 때마다 한 단계씩 나아갑니다.
 */

/** 목의 첫 피치(`src/mocks/home.ts`) */
const PITCH_ID = '10000000-0000-4000-8000-000000000001';
const PITCH_TITLE = '캡스톤 최종 발표';
const TAKE3_ID = '20000000-0000-4000-8000-000000000003';

/** 런처를 눌러 대화창을 엽니다 */
async function openChat(page: Page): Promise<Locator> {
  await page.getByRole('button', { name: '채팅 에이전트', exact: true }).click();
  const panel = page.getByRole('dialog', { name: '채팅 에이전트' });
  await expect(panel).toBeVisible();
  return panel;
}

async function ask(panel: Locator, text: string): Promise<void> {
  await panel.getByPlaceholder('메시지를 입력하세요').fill(text);
  await panel.getByRole('button', { name: '입력', exact: true }).click();
}

const scopeChip = (panel: Locator) => panel.getByRole('button', { name: /^범위:/ });

/** 답 문장 — 같은 글이 스크린 리더 알림(status)에도 있어서 문장 칸(`data-claim`) 안에서 찾습니다 */
const claim = (panel: Locator, text: string) =>
  panel.locator('[data-claim]').filter({ hasText: text });

test('1. 홈에서 버튼 → 전체 범위로 질문 → 답과 근거 칩', async ({ page }) => {
  await page.goto('/');
  const panel = await openChat(page);
  // 범위 칩 — 홈에서 열면 전체 범위입니다 (12-4 "처음 범위는 대화창을 연 화면으로")
  await expect(scopeChip(panel)).toHaveAccessibleName('범위: 전체');

  await ask(panel, '지난번보다 뭐가 나아졌어?');

  // 기록 문장에는 근거 칩이 붙습니다 — 문장이 다 나온 뒤에 보입니다
  const chips = panel.getByRole('list', { name: '근거' }).first();
  await expect(chips).toBeVisible();
  await expect(chips.getByRole('listitem').first()).toBeVisible();
});

test('2. 리포트에서 열면 범위가 그 피치 → 할 일 목록 · 도구 기록이 있는 답', async ({ page }) => {
  await page.goto(`/takes/${TAKE3_ID}`);
  const panel = await openChat(page);
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

  // 리허설 화면에는 대화 버튼이 없습니다 (L3-01)
  await page.goto(`/takes/${TAKE3_ID}/rehearsal`);
  await expect(page).toHaveURL(/\/rehearsal$/);
  await expect(page.getByRole('button', { name: '채팅 에이전트', exact: true })).toHaveCount(0);
});
