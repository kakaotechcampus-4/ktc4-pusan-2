import { expect, test } from '@playwright/test';

/**
 * 피치 만들기 · 수정(`/pitch/new` · `/pitch/:id/edit`) E2E — 목(`src/mocks/pitchEdit.ts` 등).
 * 첫 피치('캡스톤 최종 발표')는 슬라이드 · 대본 · 발표정보 · 평가기준이 모두 준비된 상태입니다.
 * PDF 올리기는 실제 PDF 렌더링까지 얽혀 여기서는 보지 않습니다.
 */

const PITCH_ID = '10000000-0000-4000-8000-000000000001';
const EDIT = `/pitch/${PITCH_ID}/edit`;

test('준비가 끝난 피치는 최종 확인을 거쳐 장치 점검으로 간다', async ({ page }) => {
  await page.goto(EDIT);
  await expect(page.getByRole('navigation', { name: '준비 순서' })).toBeVisible();

  await page.getByRole('button', { name: '최종 확인 →' }).click();
  const dialog = page.getByRole('dialog', { name: '발표 준비 최종 확인' });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('캡스톤 최종 발표');

  // 돌아가서 수정하면 창만 닫힙니다
  await dialog.getByRole('button', { name: '돌아가서 수정' }).click();
  await expect(dialog).toBeHidden();
  await expect(page).toHaveURL(new RegExp(`${EDIT}$`));

  await page.getByRole('button', { name: '최종 확인 →' }).click();
  await page
    .getByRole('dialog', { name: '발표 준비 최종 확인' })
    .getByRole('button', { name: '확인하고 시작 전 점검 →' })
    .click();
  await expect(page).toHaveURL(new RegExp(`/pitch/${PITCH_ID}/device-check$`));
});

test('발표정보를 고치면 저장 버튼이 생기고, 저장하면 저장됨이 된다', async ({ page }) => {
  await page.goto(EDIT);
  const info = page.getByRole('region', { name: '발표정보' });
  const goal = info.getByRole('spinbutton', { name: '목표 발표시간' });
  await expect(goal).toBeVisible();
  const before = Number(await goal.inputValue());
  await goal.fill(String(before + 1));

  const save = info.getByRole('button', { name: '발표정보 저장' });
  await expect(save).toBeEnabled();
  await save.click();
  await expect(info.getByRole('button', { name: '저장됨' })).toBeVisible();
});

test('새 피치는 슬라이드부터 준비하라고 안내한다', async ({ page }) => {
  await page.goto('/pitch/new');
  await expect(page.getByRole('navigation', { name: '준비 순서' })).toBeVisible();
  await expect(page.getByText('PDF를 올리고 내용과 순서를 확인해 주세요.')).toBeVisible();
  await expect(page.getByRole('button', { name: '슬라이드 확인하기 ↓' })).toBeVisible();
  // 필수 준비 셋 중 아무것도 끝나지 않았습니다
  await expect(page.getByRole('progressbar', { name: '필수 준비' })).toHaveAttribute('value', '0');
});
