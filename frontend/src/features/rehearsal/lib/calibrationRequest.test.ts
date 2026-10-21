import { describe, expect, it } from 'vitest';
import type { CalibrationSummary } from '@/types/api';
import { toCalibrationRequest } from './calibrationRequest';

const summary: CalibrationSummary = {
  points: 3,
  quality: 'GOOD',
  separability: 2.4,
  looAccuracy: 0.93,
  warnings: [],
  engineVersion: 'gaze_v1.1.0+head_pose+reference_anchor_v1',
  coordinateSpace: 'raw',
  layoutSignature: 'sig',
};

describe('보정 요약 → 발표 전 점검 결과 (AI 시선 문서 12-1)', () => {
  it('통과한 보정은 loo_accuracy 를 시선 신뢰도로 보내고 result 에 요약을 담는다', () => {
    expect(toCalibrationRequest(summary, { micDetected: true })).toEqual({
      face_detected: true,
      mic_detected: true,
      gaze_confidence: 0.93,
      result: {
        engine_version: 'gaze_v1.1.0+head_pose+reference_anchor_v1',
        status: 'OK',
        separability: 2.4,
        loo_accuracy: 0.93,
        warnings: [],
      },
    });
  });

  it('경고가 붙은 통과(FAIR)도 OK 이고 경고를 그대로 옮긴다', () => {
    const warnings = ['SCREEN_MERGED: looking at the lens and at the screen centre …'];
    const body = toCalibrationRequest(
      { ...summary, quality: 'FAIR', warnings },
      { micDetected: true },
    );
    expect(body.result.status).toBe('OK');
    expect(body.result.warnings).toEqual(warnings);
    expect(body.gaze_confidence).toBe(0.93);
  });

  it('검사에 떨어진 보정(POOR)은 RETRY_REQUIRED 이고 시선 신뢰도는 0 이다', () => {
    const body = toCalibrationRequest({ ...summary, quality: 'POOR' }, { micDetected: false });
    expect(body.result.status).toBe('RETRY_REQUIRED');
    expect(body.gaze_confidence).toBe(0);
    expect(body.mic_detected).toBe(false);
  });

  it('수치를 모르면(null) 0 으로 채우지 않고 null 로 보낸다', () => {
    const body = toCalibrationRequest(
      { ...summary, separability: null, looAccuracy: null },
      { micDetected: true },
    );
    expect(body.result).toMatchObject({ separability: null, loo_accuracy: null });
    expect(body.gaze_confidence).toBe(0);
  });
});
