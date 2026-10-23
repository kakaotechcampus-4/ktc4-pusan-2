/** 리허설 기록 IndexedDB 이름. `features/rehearsal/lib/db.ts` 가 이 값으로 엽니다 */
export const PITCH_DB_NAME = 'pitchcoach';

/**
 * 이 브라우저의 리허설 기록을 통째로 지웁니다 — 마이페이지의 "기록 지우기"와 탈퇴 뒤.
 *
 * 열려 있는 연결은 `db.ts` 의 `blocking` 이 닫아 줍니다(이 탭 · 다른 탭 모두). 그래도 막히면
 * (닫지 못하는 옛 탭) `BLOCKED` 로 실패합니다. 요청은 남아 있어서 그 탭이 닫히면 지워집니다.
 */
export function deleteLocalRecords(): Promise<void> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.deleteDatabase(PITCH_DB_NAME);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
    request.onblocked = () => reject(new Error('BLOCKED'));
  });
}
