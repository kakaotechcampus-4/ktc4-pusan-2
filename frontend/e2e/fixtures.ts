/**
 * E2E 가 함께 쓰는 목 데이터의 id · 이름 (`src/mocks/home.ts` 의 피치 목록).
 * 목의 값이 바뀌면 여기 한 곳만 고칩니다. 이 파일은 `*.e2e.ts` 가 아니라 시험으로 돌지 않습니다.
 */

/** 첫 피치 — 슬라이드 · 대본 · 발표정보 · 평가기준이 모두 준비됨. 분석이 도는 중이라 BE 가 삭제를 막음 */
export const FIRST_PITCH = {
  id: '10000000-0000-4000-8000-000000000001',
  title: '캡스톤 최종 발표',
} as const;

/** 지울 수 있는 피치 */
export const DELETABLE_PITCH_TITLE = '창업 경진대회 본선';
