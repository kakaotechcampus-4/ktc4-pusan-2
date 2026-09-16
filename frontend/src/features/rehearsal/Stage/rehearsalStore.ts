import { create } from 'zustand';
import type { Ms } from '@/types/api';

export type StagePhase = 'RUNNING' | 'ENDING' | 'ENDED';

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
  setPhase: (phase) => set({ phase }),
  setSlide: (slideNumber) => set({ slideNumber }),
  showCoach: (coach) => set({ coach }),
  // 같은 메시지를 두 번 지우지 않도록 비교합니다 — 8초 타이머가 겹칠 수 있습니다
  clearCoach: () => set((s) => (s.coach === null ? s : { coach: null })),
  reset: () => set(INITIAL),
}));
