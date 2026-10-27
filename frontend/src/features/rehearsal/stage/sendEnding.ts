import { endSession, markSubmitted } from '../lib/db';
import { clearWriteFailures, noteWriteFailure, readWriteFailures } from '../lib/writeFailures';
import type { Ms } from '@/types/api';
import type { TakeEndRequest } from '@/types/take';
import { clearClock, clearMetrics, clearSlides } from './resume';

/** 끝내기를 누른 순간 고정한 값 (`SessionRow.ending`) */
export interface Ending {
  durationMs: Ms;
  endedAtIso: string;
}

/**
 * Take 의 종료 시각을 BE 에 적습니다. 리허설 끝내기(종료 중 새로고침 포함)와 재시도 화면(P15)이 같이 씁니다.
 *
 * 성공하면 세션 행에 보냈다고 적고 이 탭에 남긴 이어 가기 값을 지웁니다. 실패는 그대로 던집니다 —
 * 기록은 IndexedDB 에 그대로 있어서 같은 `clientSessionId` 로 다시 부르면 됩니다.
 *
 * 시선 · 슬라이드 · 코치 기록은 여기서 보내지 않습니다. 계약에서 기록은 발표 중 WS `metrics` 로
 * 가고(`docs/ai/architecture.md` 1-4, `useMetricsSender`), 코치 기록은 서버 코치가 만듭니다.
 * 브라우저(IndexedDB)의 기록은 이 브라우저의 사본입니다.
 */
export async function sendEnding(
  takeId: string,
  id: string,
  { endedAtIso }: Ending,
  endTake: (body: TakeEndRequest) => Promise<unknown>,
): Promise<void> {
  await endSession(id);

  // ── 대조 ────────────────────────────────────────────────────────
  // 발표는 한 번뿐이라 여기가 마지막 확인입니다. 장부에 쌓인 실패를 봅니다.
  // ★ 지금은 알리는 곳이 콘솔뿐입니다.
  const failures = readWriteFailures(id);
  if (Object.keys(failures).length > 0) {
    console.error('[rehearsal] 기록이 불완전합니다', { 실패: failures });
  }

  await endTake({
    // 시작 시각은 첫 WS 연결 때 서버가 기록한 값을 유지합니다.
    ended_at: endedAtIso,
  });
  // 보냈다는 표시. 이 주소로 다시 들어오면(새로고침 · 뒤로 가기) 무대를 열지 않습니다.
  // 못 적으면 다시 들어왔을 때 종료를 한 번 더 이어가는데, 같은 시각을 다시 적을 뿐이라 괜찮습니다
  await markSubmitted(id).catch((err: unknown) => noteWriteFailure(id, 'ending', err));
  clearWriteFailures(id);
  clearClock(takeId);
  clearMetrics(takeId);
  clearSlides(takeId);
}
