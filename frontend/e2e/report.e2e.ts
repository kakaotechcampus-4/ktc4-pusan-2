import { expect, test, type Page } from '@playwright/test';
import { FIRST_PITCH } from './fixtures.ts';

/**
 * 리포트(`/takes/:takeId`) E2E — 목(`src/mocks/report.ts` · `contentCoverage.ts`)의 Take id 마다 상태가 정해져 있습니다.
 *   t3                정상 — 리뷰까지 끝남, 내용 전달 MEASURED(#1152 예시)
 *   t2                시선 측정 제외
 *   t-review-failed   리뷰 실패 — 측정값만
 *   t-fallback        AI 문장 실패 — 기본 문장
 *   t-content-*       내용 전달 카드의 상태별 목 (#1157)
 */

const PITCH_ID = FIRST_PITCH.id;

const contentCard = (page: Page) =>
  page.locator('section').filter({ has: page.getByRole('heading', { name: '내용 전달' }) });

async function openSlideList(page: Page) {
  const card = contentCard(page);
  await card.getByRole('button', { name: /^장별 보기/ }).click();
  return card;
}

test('리포트 기본: 코칭 노트 · 지난 Take 비교 · 측정값 · 다음 미션', async ({ page }) => {
  await page.goto('/takes/t3');
  await expect(
    page.getByText('속도는 안정됐지만 3번 장에서 대본을 이어 읽는 시간이 길었습니다.'),
  ).toBeVisible();
  await expect(page.getByRole('heading', { name: '지난 Take와 비교' })).toBeVisible();
  await expect(page.getByRole('region', { name: '측정값' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Take 4에서 해 볼 것' })).toBeVisible();
});

test('목소리 크기는 "기준 대비 변화"로, 부호가 있는 dB 로 보인다 (#1184)', async ({ page }) => {
  await page.goto('/takes/t3');
  const comparison = page
    .locator('section')
    .filter({ has: page.getByRole('heading', { name: '지난 Take와 비교' }) });
  await expect(comparison.getByText('기준 대비 변화').first()).toBeVisible();
  await expect(comparison.getByText('−1dB').first()).toBeVisible();
  // 예전 이름 · 부호 없는 음수가 남지 않습니다
  await expect(comparison.getByText('평소 대비 목소리')).toHaveCount(0);
});

test('"Take N 연습하기"는 장치 점검으로 이동만 한다', async ({ page }) => {
  await page.goto('/takes/t3');
  await page.getByRole('link', { name: 'Take 4 연습하기' }).click();
  await expect(page).toHaveURL(new RegExp(`/pitch/${PITCH_ID}/device-check$`));
});

test.describe('리뷰가 없거나 일부만 있는 리포트', () => {
  test('시선을 재지 않은 Take 는 0% 가 아니라 — 와 안내', async ({ page }) => {
    await page.goto('/takes/t2');
    await expect(page.getByText('이번 Take는 시선을 재지 않았어요.')).toBeVisible();
    const measures = page.getByRole('region', { name: '측정값' });
    await expect(measures.getByText('—').first()).toBeVisible();
  });

  test('리뷰가 실패해도 측정값은 보인다', async ({ page }) => {
    await page.goto('/takes/t-review-failed');
    await expect(page.getByText('이번 Take는 총평을 만들지 못했어요.')).toBeVisible();
    await expect(page.getByRole('region', { name: '측정값' })).toBeVisible();
  });

  test('AI 문장이 실패하면 기본 문장임을 알린다', async ({ page }) => {
    await page.goto('/takes/t-fallback');
    await expect(
      page.getByText('코치 문장을 만들지 못해 기본 문장으로 보여 드려요.'),
    ).toBeVisible();
  });
});

test.describe('내용 전달 카드 (#1157)', () => {
  test('대표 숫자 · 잠정 배지 · 일부 장 제외 · 지난 Take 대비', async ({ page }) => {
    await page.goto('/takes/t3');
    const card = contentCard(page);
    await expect(card.getByText('핵심 내용 전달률')).toBeVisible();
    await expect(card.getByText('50%').first()).toBeVisible();
    await expect(card.getByText('잠정').first()).toBeVisible();
    await expect(card.getByText('일부 장 제외')).toBeVisible();
    await expect(card.getByText('지난 Take보다')).toBeVisible();
  });

  test('장별 목록은 접혀 있고, 열어서 장을 누르면 그 장의 상세를 불러온다', async ({ page }) => {
    await page.goto('/takes/t3');
    const card = contentCard(page);
    const toggle = card.getByRole('button', { name: /^장별 보기/ });
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');

    const slide3 = card.getByRole('button', { name: /^3장/ });
    await slide3.click();
    await expect(slide3).toHaveAttribute('aria-expanded', 'true');
    await expect(card.getByRole('heading', { name: '대본 문장' })).toBeVisible();
    // 대본 밖 구간은 <mark> 와 범주 이름 글자 — 이모지 뒤에서도 위치가 맞습니다
    await expect(card.locator('mark')).toHaveText([
      '안녕하세요[진행 말]',
      '사실 저도 놀랐어요[대본 밖 말]',
    ]);
    await expect(card.getByText('대본 밖 말은 세부 구분 없이 합쳐 보여요').first()).toBeVisible();
  });

  test('간이 장은 이유와 간이 배지를 보인다', async ({ page }) => {
    await page.goto('/takes/t3');
    const card = await openSlideList(page);
    await card.getByRole('button', { name: /^4장/ }).click();
    await expect(card.getByText('채점이 시간 안에 끝나지 않았어요')).toBeVisible();
    await expect(card.getByText('간이').first()).toBeVisible();
    // 간이 표시는 배지 하나로만
    await expect(card.getByText('(간이)')).toHaveCount(0);
  });

  test('결과가 바뀌었으면(409) 리포트를 다시 읽고 새 결과로 그 장을 다시 받는다', async ({
    page,
  }) => {
    await page.goto('/takes/t-content-revised');
    const card = await openSlideList(page);
    await card.getByRole('button', { name: /^3장/ }).click();
    await expect(card.getByRole('heading', { name: '대본 문장' })).toBeVisible();
  });

  test('채점된 장이 없으면 간이 단계만 보이고 전체 개수는 집계하지 못했다고 알린다', async ({
    page,
  }) => {
    await page.goto('/takes/t-content-estimated');
    const card = contentCard(page);
    await expect(card.getByText('대부분 말함 1장')).toBeVisible();
    await expect(card.getByText(/채점된 장이 없어 .* 집계하지 못했어요/)).toBeVisible();
    await expect(card.getByText('확인할 수치 0개')).toHaveCount(0);
  });

  test('전달률 0 · 수치 모두 확인 대기 · 잠정 장의 이유', async ({ page }) => {
    await page.goto('/takes/t-content-zero');
    const card = contentCard(page);
    await expect(card.getByText('핵심 내용을 빠뜨리거나 다르게 말했어요')).toBeVisible();
    await expect(card.getByText('확인 전 추정 67%')).toBeVisible();
    await card.getByRole('button', { name: /^장별 보기/ }).click();
    await expect(
      card.getByText('음성 인식이 끊긴 구간이 있어요. 녹음된 부분만 봤어요'),
    ).toBeVisible();
  });

  for (const [takeId, line] of [
    ['t-content-none', '대본 전달도 상세 결과를 아직 제공하지 않아요'],
    ['t-content-analyzing', '대본 전달도를 분석하고 있어요'],
    ['t-content-legacy', '이 Take는 자세한 결과를 지원하기 전에 분석됐어요'],
    ['t-content-failed', '분석이 시간 안에 끝나지 않았어요'],
  ] as const)
    test(`측정값이 없는 상태(${takeId})는 숫자 없이 이유만`, async ({ page }) => {
      await page.goto(`/takes/${takeId}`);
      const card = contentCard(page);
      await expect(card.getByText(line)).toBeVisible();
      await expect(card.getByText('핵심 내용 전달률')).toHaveCount(0);
    });
});
