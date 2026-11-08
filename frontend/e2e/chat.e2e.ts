import { expect, test } from '@playwright/test';

/**
 * 대화 에이전트 E2E — `docs/ai/features/chat-agent.md` 12-4 "E2E 시험 (F9)" 의 시험 번호를 그대로 붙입니다.
 * 목(`src/mocks/chat.ts`)은 보낸 말로 시나리오를 고르고, 조회할 때마다 한 단계씩 나아갑니다.
 */

test('1. 홈에서 버튼 → 전체 범위로 질문 → 답과 근거 칩', async ({ page }) => {
  await page.goto('/');

  await page.getByRole('button', { name: '채팅 에이전트', exact: true }).click();
  const panel = page.getByRole('dialog');
  await expect(panel).toBeVisible();
  // 범위 칩 — 홈에서 열면 전체 범위입니다 (12-4 "처음 범위는 대화창을 연 화면으로")
  await expect(panel.getByRole('button', { name: /^범위:/ })).toHaveAccessibleName('범위: 전체');

  await panel.getByPlaceholder('메시지를 입력하세요').fill('지난번보다 뭐가 나아졌어?');
  await panel.getByRole('button', { name: '입력', exact: true }).click();

  // 기록 문장에는 근거 칩이 붙습니다 — 문장이 다 나온 뒤에 보입니다
  await expect(panel.getByRole('list', { name: '근거' }).first()).toBeVisible();
  await expect(
    panel.getByRole('list', { name: '근거' }).first().getByRole('listitem').first(),
  ).toBeVisible();
});
