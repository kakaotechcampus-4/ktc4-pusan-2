/**
 * 분석 정보 · 설문 답을 보관하는 기한입니다. 이 기한이 지나면 파기합니다.
 * 개인정보 처리 동의 팝업 · 개인정보 처리방침(4절) · 테스트 참여 안내 · 피치 삭제 확인 창이 같은 값을 씁니다.
 * 바꾸면 동의 팝업 안내문이 바뀌므로 `PRIVACY_CONSENT_VERSION`(privacyConsent.ts)을 올립니다.
 * (BE 미구현) 이 날 일괄 파기하는 작업은 아직 BE 에 없습니다
 */
export const DATA_RETENTION_UNTIL = '2026년 12월 31일';
