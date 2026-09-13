import type { Ms } from '@/types/api';

/**
 * 발표 경과 시간을 재는 시계.
 *
 * setInterval로 카운트를 올리면 안 됩니다.
 * 브라우저가 백그라운드 탭의 타이머를 1초 이상으로 늦추기 때문에,
 * 발표자가 잠깐 다른 창을 봤다 오면 시간이 실제보다 적게 흐른 것으로 나옵니다.
 *
 * performance.now()는 탭이 가려져도 정확합니다.
 * 화면 갱신용 tick만 interval로 돌리고, 값은 매번 now()에서 다시 계산합니다.
 */
export class PresentationClock {
  private t0: number | null = null;
  private stoppedAt: number | null = null;

  start(): void {
    this.t0 = performance.now();
    this.stoppedAt = null;
  }

  stop(): void {
    if (this.t0 !== null && this.stoppedAt === null) {
      this.stoppedAt = performance.now();
    }
  }

  /** 발표 시작 이후 경과 ms. 시작 전이면 0 */
  elapsedMs(): Ms {
    if (this.t0 === null) return 0;
    const end = this.stoppedAt ?? performance.now();
    return Math.max(0, Math.round(end - this.t0));
  }

  get isRunning(): boolean {
    return this.t0 !== null && this.stoppedAt === null;
  }
}

/** 남은 시간. 음수면 초과입니다 */
export function remainingMs(elapsed: Ms, limitSec: number): Ms {
  return limitSec * 1000 - elapsed;
}

/** 09:42 / +00:24 형태로 */
export function formatDuration(ms: Ms, showSign = false): string {
  const neg = ms < 0;
  const total = Math.floor(Math.abs(ms) / 1000);
  const mm = String(Math.floor(total / 60)).padStart(2, '0');
  const ss = String(total % 60).padStart(2, '0');
  const sign = showSign ? (neg ? '+' : '-') : '';
  return `${sign}${mm}:${ss}`;
}
