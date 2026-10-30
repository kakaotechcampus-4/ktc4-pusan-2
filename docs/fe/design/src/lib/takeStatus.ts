// 제품 `frontend/src/shared/lib/takeStatus.ts`의 문구와 tone. 설계서가 관리한다.
import type { Tone } from '@/ui/Badge';

/** Take 상태 배지 여섯 가지. 상태 이름은 BE의 Take status(실패 중 중단은 end_reason) */
export const TAKE_STATUSES: { status: string; label: string; tone: Tone }[] = [
  { status: 'READY', label: '시작 전', tone: 'muted' },
  { status: 'RUNNING', label: '연습 중', tone: 'good' },
  { status: 'ANALYZING', label: '분석 중', tone: 'good' },
  { status: 'COMPLETED', label: '완료', tone: 'plain' },
  { status: 'FAILED · ABANDONED/CLOSED', label: '중단', tone: 'muted' },
  { status: 'FAILED', label: '실패', tone: 'muted' },
];
