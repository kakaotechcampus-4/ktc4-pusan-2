import { describe, expect, it } from 'vitest';
import { buildGazePayload, ENGINE_VERSION_UNAVAILABLE } from './gazePayload';
import type { ZoneDecision } from '@/workers/gaze.contract';
import type { GazeZone } from '@/types/api';

const d = (tMs: number, zone: GazeZone): ZoneDecision => ({
  tMs,
  zone,
  confidence: 0.9,
  sampleCount: 12,
});

const base = {
  durationMs: 10_000,
  engineVersion: 'dummy@heuristic-v0+vote-v1',
  decisionIntervalMs: 1000,
  excludedReason: null,
  calibration: null,
};

describe('GazePayload 조립', () => {
  it('판정을 구간으로 묶고 시간 관계식을 만족한다', () => {
    const decisions = [
      d(0, 'CAMERA'),
      d(1000, 'CAMERA'),
      d(2000, 'CAMERA'),
      d(3000, 'BOTTOM'),
      d(4000, 'UNCERTAIN'),
    ];
    const { payload, validationErrors, cameraMs, bottomMs } = buildGazePayload({
      ...base,
      decisions,
    });

    expect(validationErrors).toHaveLength(0);
    expect(payload.excluded).toBe(false);
    expect(payload.excludedReason).toBeNull();

    // 연속된 CAMERA 3개가 한 구간으로
    expect(payload.segments).toHaveLength(3);
    expect(payload.segments[0]).toMatchObject({ startMs: 0, endMs: 3000, zone: 'CAMERA' });

    // 명세의 관계식 — 서버의 422 검증과 같은 규칙
    expect(payload.measuredMs + payload.uncertainMs).toBe(payload.trackedMs);
    expect(cameraMs! + bottomMs!).toBe(payload.measuredMs);
    expect(payload).toMatchObject({ trackedMs: 5000, measuredMs: 4000, uncertainMs: 1000 });
  });

  it('제외 사유가 있으면 구간을 만들지 않고 0을 보낸다', () => {
    // ★ 여기서 0 은 "0% 봤다"가 아니라 "측정한 구간이 없다"는 뜻이고,
    //   excluded: true 가 그 구분을 합니다. 리포트의 null 은 서버가 만듭니다.
    const { payload, cameraMs, bottomMs } = buildGazePayload({
      ...base,
      decisions: [d(0, 'CAMERA'), d(1000, 'CAMERA')],
      engineVersion: null,
      excludedReason: 'ENGINE_UNAVAILABLE',
    });

    expect(payload.excluded).toBe(true);
    expect(payload.excludedReason).toBe('ENGINE_UNAVAILABLE');
    expect(payload.segments).toHaveLength(0);
    expect(payload).toMatchObject({ trackedMs: 0, measuredMs: 0, uncertainMs: 0 });

    // 엔진이 못 떴으면 버전을 모릅니다. 없는 값을 지어내지 않습니다.
    expect(payload.engineVersion).toBe(ENGINE_VERSION_UNAVAILABLE);

    // 화면에 숫자를 그리면 안 되는 경우 — null 로 알립니다
    expect(cameraMs).toBeNull();
    expect(bottomMs).toBeNull();
  });

  it('자체 검증에 걸리면 VALIDATION_FAILED 로 제외한다', () => {
    // 시간대가 겹친 기록 — 보내면 서버가 422 로 되돌린다
    const { payload, validationErrors } = buildGazePayload({
      ...base,
      decisions: [d(0, 'CAMERA'), d(500, 'BOTTOM')],
    });

    expect(validationErrors.length).toBeGreaterThan(0);
    expect(payload.excluded).toBe(true);
    expect(payload.excludedReason).toBe('VALIDATION_FAILED');
    expect(payload.segments).toHaveLength(0);
  });

  /**
   * 판정 시각은 무대 시계라 끝내기를 누른 순간에 걸친 마지막 판정이 발표 길이를 넘습니다.
   * 그 하나 때문에 Take 전체의 시선이 VALIDATION_FAILED 로 빠지면 안 됩니다.
   */
  it('발표 길이를 넘는 마지막 판정은 버리고 나머지를 보낸다', () => {
    const { payload, validationErrors } = buildGazePayload({
      ...base,
      durationMs: 3_500,
      decisions: [d(1000, 'CAMERA'), d(2000, 'CAMERA'), d(3000, 'CAMERA')],
    });

    expect(validationErrors).toHaveLength(0);
    expect(payload.excluded).toBe(false);
    expect(payload.segments).toEqual([
      expect.objectContaining({ startMs: 1000, endMs: 3000, zone: 'CAMERA' }),
    ]);
  });

  it('판정이 하나도 없어도 제외가 아니다 — 측정은 했고 결론이 없는 것', () => {
    const { payload } = buildGazePayload({ ...base, decisions: [] });
    expect(payload.excluded).toBe(false);
    expect(payload.segments).toHaveLength(0);
    expect(payload.trackedMs).toBe(0);
  });

  it('engineProfile 은 아직 항상 NORMAL — LIGHT/OFF 임계값은 I-03 대기', () => {
    const { payload } = buildGazePayload({ ...base, decisions: [d(0, 'CAMERA')] });
    expect(payload.engineProfile).toBe('NORMAL');
  });
});
