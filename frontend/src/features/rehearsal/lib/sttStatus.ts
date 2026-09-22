/**
 * 서버 STT(2단 코치)가 살아 있는지. BE `realtime/dto.py` 의 `SttState` 와 같은 값입니다.
 *
 * ★ **`degraded` 여도 발표는 계속됩니다.** BE 주석이 명시합니다 —
 *   "재접속이 이어서 실패해도 연결을 끊지 않는다. FE 1단 코치는 계속 돌아야 하므로
 *   degraded 만 알리고 재시도한다."
 *
 *   1단 코치(브라우저에서 도는 규칙)는 서버와 무관하게 돌고, 2단 코치(속도·군더더기·
 *   대본 일치)만 빠집니다. 그래서 이 상태는 **경고지 오류가 아닙니다.**
 */
export type SttState = 'connecting' | 'ok' | 'reconnecting' | 'degraded' | 'closed';

/**
 * 무대 아래 줄에 띄울 문구. `null` 이면 아무것도 띄우지 않습니다.
 *
 * `ok` 와 `closed` 가 null 인 이유 — 정상일 때 굳이 한 줄을 차지하지 않습니다.
 * 발표 중에 눈에 들어와야 하는 것은 남은 시간과 대본이지 연결 상태가 아닙니다.
 */
export const STT_STATE_MESSAGE: Record<SttState, string | null> = {
  connecting: '실시간 코칭 준비 중',
  ok: null,
  reconnecting: '실시간 코칭 다시 연결 중',
  degraded: '실시간 코칭이 잠시 멈췄어요 — 발표는 계속 진행됩니다',
  closed: null,
};

/** 서버가 보내 준 누적 통계. 연결 단위가 아니라 **Take 누적**입니다 */
export interface SttStatus {
  state: SttState;
  sttSessionNo: number;
  frames: number;
  droppedFrames: number;
  silenceMs: number;
  /** STT 에 닿지 못한 오디오. 채우지 못한 갭 + 버린 프레임 */
  lostMs: number;
}

/**
 * 실시간 전사가 얼마나 빠졌나 — 리포트에 "2단 코치가 일부 구간을 못 봤다" 를
 * 표시할지 정하는 값입니다. 원본 오디오는 IndexedDB 에 그대로 있으므로
 * **발표 후 배치 처리로는 복구됩니다.**
 */
export function hasLostAudio(status: SttStatus | null): boolean {
  return status !== null && status.lostMs > 0;
}
