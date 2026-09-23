import { create } from 'zustand';
import type { Ms } from '@/types/api';

export type StagePhase = 'RUNNING' | 'ENDING' | 'ENDED';

/**
 * 단계의 순서. **한 방향으로만 갑니다.**
 *
 * 발표는 한 번뿐입니다. ENDING 에서 RUNNING 으로 돌아가면 —
 *   · useRecording 이 새 MediaRecorder 를 띄우고 seq 가 0 부터 시작합니다.
 *     audioChunks 의 키가 [clientSessionId, seq] 라 put 이 **원본 조각을 덮어씁니다**
 *   · clock.start() 가 t0 를 다시 잡아 durationMs 가 재시도 시점부터 재측정됩니다
 *   · useSlideDeck 의 init 이펙트가 다시 돌아 0ms 행을 현재 슬라이드로 덮어씁니다
 *
 * 타입은 "셋 중 하나" 만 말하고 순서는 말하지 않습니다. 그래서 실제로 한 번
 * 되돌리는 코드가 들어갔고 타입도 린트도 아무 말이 없었습니다. 여기서 막습니다.
 *
 * 되돌리는 길은 `reset()` 하나뿐입니다 — 그건 **새 Take** 를 시작한다는 뜻입니다.
 */
const ORDER: Record<StagePhase, number> = { RUNNING: 0, ENDING: 1, ENDED: 2 };

export interface CoachMessage {
  type: string;
  text: string;
  atMs: Ms;
}

/**
 * 화면에 보이는 리허설 상태만 둡니다 (상태 배치표의 가운데 칸).
 *
 * 여기 없는 것들 —
 *   시선 원시 좌표 · 음량 RMS · 경과 ms : ref와 DOM. 초당 수십 번 바뀝니다
 *   자료 · 대본 · Take 맥락             : TanStack Query. 서버에서 온 값입니다
 *
 * 슬라이드 번호가 여기 있는 이유는 화면 세 곳(머리줄·다음 슬라이드·대본)이
 * 같은 값을 봐야 하기 때문입니다. 코치 메시지는 8초에 한 번 바뀝니다 —
 * 그 주기의 렌더는 이 프로젝트가 허용하는 범위입니다.
 */
interface RehearsalState {
  phase: StagePhase;
  slideNumber: number;
  coach: CoachMessage | null;

  setPhase: (phase: StagePhase) => void;
  setSlide: (slideNumber: number) => void;
  showCoach: (coach: CoachMessage) => void;
  clearCoach: () => void;
  reset: () => void;
}

const INITIAL = { phase: 'RUNNING' as StagePhase, slideNumber: 1, coach: null };

export const useRehearsalStore = create<RehearsalState>((set) => ({
  ...INITIAL,
  // 뒤로 가거나 제자리인 요청은 **조용히 무시합니다.** throw 하지 않는 이유는
  // 발표 도중 예외로 화면을 깨뜨리는 쪽이 더 나쁘기 때문입니다.
  setPhase: (phase) => set((s) => (ORDER[phase] > ORDER[s.phase] ? { phase } : s)),
  setSlide: (slideNumber) => set({ slideNumber }),
  showCoach: (coach) => set({ coach }),
  // 같은 메시지를 두 번 지우지 않도록 비교합니다 — 8초 타이머가 겹칠 수 있습니다
  clearCoach: () => set((s) => (s.coach === null ? s : { coach: null })),
  reset: () => set(INITIAL),
}));
