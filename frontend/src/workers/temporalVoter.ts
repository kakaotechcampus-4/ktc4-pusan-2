import type { FrameVerdict, ZoneDecision } from './gaze.contract';
import type { GazeZone, Ms } from '@/types/api';

/**
 * 프레임 판단들을 모아 **1초에 하나**를 냅니다.
 *
 * 왜 계약(gaze.contract.ts) 밖인가 —
 *   1초 다수결은 모델의 일이 아니라 우리 정책입니다. 엔진 버전 문자열에
 *   `vote-v1`이 **별도 부품으로** 적히는 것도 그래서입니다. AI팀도 이걸 별개로 봅니다.
 *   모델이 프레임 단위 판정을 주더라도 1초 다수결은 여전히 우리 일입니다.
 *
 *   분류기 안에 두면 모델을 갈아끼울 때 정책이 같이 사라집니다.
 *
 * 정책 상수 셋은 **이 파일에만** 있습니다. 다른 곳에 복사하지 마세요.
 */
export class TemporalVoter {
  /** 판정 주기. CLAUDE.md 3번 — 이 값이 바뀌면 gazeSegments 최소 구간 길이도 바뀝니다 */
  static readonly INTERVAL_MS = 1000;
  /** 다수가 이 비율 미만이면 다수결을 믿지 않고 UNCERTAIN */
  static readonly VOTE_THRESHOLD = 0.6;
  /** 1초에 표본이 이만큼도 안 되면 판정하지 않고 UNCERTAIN */
  static readonly MIN_SAMPLES = 4;

  private buffer: FrameVerdict[] = [];
  private lastDecisionAt = 0;

  /**
   * 프레임 판단 하나를 넣습니다. 분류기가 null 을 냈으면 부르지 않습니다 —
   * 그 프레임은 표본이 아니고, 표본이 모자라면 MIN_SAMPLES 규칙이 걸립니다.
   *
   * tMs 는 지금 쓰지 않습니다. 나중에 1초 창을 시간으로 자를 때 필요합니다
   * (지금은 decide 호출 사이를 창으로 봅니다).
   */
  push(verdict: FrameVerdict, _tMs: Ms): void {
    this.buffer.push(verdict);
  }

  /** 1초가 안 지났으면 null. 지났으면 그동안 쌓인 것으로 하나를 냅니다. */
  decide(nowMs: Ms): ZoneDecision | null {
    if (nowMs - this.lastDecisionAt < TemporalVoter.INTERVAL_MS) return null;

    // ★ 격자에 고정한다. `= nowMs` 로 맞추면 안 된다.
    //
    // decide() 는 프레임이 도착할 때만 불리므로, nowMs 로 맞추면 판정 시각이
    // 프레임 간격만큼 밀리고 그 밀림이 누적된다 (60fps 에서 1008ms, 12fps 에서 1040ms).
    //
    // 그러면 compressToSegments 가 무너진다 — 그 함수는 `cur.endMs === d.tMs`
    // 일 때만 구간을 병합하는데, 판정이 1008ms 간격이면 endMs(=tMs+1000)가
    // 다음 tMs 와 절대 같아지지 않는다. **판정마다 별개 구간이 되어
    // 600개를 몇 개로 줄이는 압축이 아예 일어나지 않는다.**
    // 10분 발표에서 판정 수도 600이 아니라 595쯤으로 모자라진다.
    //
    // 첫 판정만 실제 시각에 맞추고, 그다음부터는 정확히 INTERVAL_MS 씩 올린다.
    // 프레임이 한동안 끊겼다면 이후 호출들이 한 칸씩 따라잡으며 빈 초를
    // UNCERTAIN 으로 메운다 — 측정하지 않은 초를 그대로 기록하는 것이 맞다.
    this.lastDecisionAt =
      this.lastDecisionAt === 0 ? nowMs : this.lastDecisionAt + TemporalVoter.INTERVAL_MS;
    const tMs = this.lastDecisionAt;

    const verdicts = this.buffer;
    this.buffer = [];

    // 표본 부족 — 얼굴을 못 잡았거나 프레임이 안 들어온 1초입니다.
    // 이 경우의 confidence 는 0 입니다. null 이 아닙니다 — 측정은 했고 결론이 없는 것입니다.
    if (verdicts.length < TemporalVoter.MIN_SAMPLES) {
      return { tMs, zone: 'UNCERTAIN', confidence: 0, sampleCount: verdicts.length };
    }

    const votes = new Map<GazeZone, number>();
    for (const v of verdicts) {
      votes.set(v.zone, (votes.get(v.zone) ?? 0) + 1);
    }

    let winner: GazeZone = 'UNCERTAIN';
    let top = -1;
    for (const [zone, count] of votes) {
      if (count > top) {
        top = count;
        winner = zone;
      }
    }

    // 다수가 차지한 비율이 그 1초의 신뢰도입니다.
    // 프레임 하나하나의 confidence 는 여기 섞지 않습니다 — 다수결(vote-v1)의 뜻은
    // "몇 표를 얻었나"이고, 모델 확신도는 그와 별개의 신호입니다.
    const ratio = top / verdicts.length;

    if (ratio < TemporalVoter.VOTE_THRESHOLD) {
      return { tMs, zone: 'UNCERTAIN', confidence: ratio, sampleCount: verdicts.length };
    }
    return { tMs, zone: winner, confidence: ratio, sampleCount: verdicts.length };
  }

  /** 측정을 새로 시작할 때. 앞 측정이 다음에 섞이지 않게 합니다. */
  reset(): void {
    this.buffer = [];
    this.lastDecisionAt = 0;
  }
}
