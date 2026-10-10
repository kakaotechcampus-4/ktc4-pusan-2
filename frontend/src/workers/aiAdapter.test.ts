import { describe, expect, it } from 'vitest';
import {
  engineVersion,
  toCalibrationResult,
  toPlacementResult,
  type AiCalibrationQuality,
} from './aiAdapter';
import { SCREEN_MERGED_WARNING } from '@/vendor/gaze/engine/reference';

const passed: AiCalibrationQuality = {
  status: 'OK',
  reason: null,
  hint: null,
  separability: 2.4,
  loo_accuracy: 0.97,
};

describe('캘리브레이션 품질 → CalibrationResult', () => {
  const model = { w: [1, 2] };

  it('합격에 경고가 없으면 GOOD 이고 수치를 옮겨 담는다', () => {
    expect(toCalibrationResult(passed, model)).toEqual({
      ok: true,
      ref: { quality: 'GOOD', metrics: { separability: 2.4, looAccuracy: 0.97 }, model },
      advice: null,
    });
  });

  it('합격이어도 INVERTED_PITCH 경고가 있으면 FAIR 다 — 문구 중간에 있어도 잡는다', () => {
    const r = toCalibrationResult({ ...passed, hint: 'Note. INVERTED_PITCH: reversed?' }, model);
    expect(r).toMatchObject({ ok: true, ref: { quality: 'FAIR' }, advice: null });
  });

  it('합격이어도 화면이 렌즈에 합쳐졌으면(SCREEN_MERGED) FAIR 다 — 화면 본 시간이 청중으로 세진다', () => {
    // 엔진이 실제로 붙이는 문구 그대로 넣습니다. 엔진이 문구를 바꾸면 여기서 깨집니다
    expect(toCalibrationResult({ ...passed, hint: SCREEN_MERGED_WARNING }, model)).toMatchObject({
      ok: true,
      ref: { quality: 'FAIR' },
      advice: null,
    });
    // 다른 안내 뒤에 붙어 와도 잡습니다 (엔진이 hint 뒤에 이어 붙입니다)
    const appended = toCalibrationResult(
      { ...passed, hint: `Some note. ${SCREEN_MERGED_WARNING}` },
      model,
    );
    expect(appended).toMatchObject({ ref: { quality: 'FAIR' } });
  });

  it('경고가 없으면 GOOD 이다', () => {
    expect(toCalibrationResult({ ...passed, hint: null }, model)).toMatchObject({
      ref: { quality: 'GOOD' },
    });
  });

  it('불합격이어도 모델이 있으면 POOR 로 진행하고, 사유는 권고로 온다 — AI 정책', () => {
    const r = toCalibrationResult(
      { ...passed, status: 'RETRY_REQUIRED', reason: 'LOW_LOO_ACCURACY', hint: '...' },
      model,
    );
    expect(r).toMatchObject({
      ok: true,
      ref: { quality: 'POOR', model },
      advice: 'LOW_LOO_ACCURACY',
    });
  });

  it('모델이 없으면 막는다 — 사유는 AI 사유 그대로', () => {
    const r = toCalibrationResult(
      { ...passed, status: 'RETRY_REQUIRED', reason: 'NOT_ENOUGH_SAMPLES' },
      null,
    );
    expect(r).toEqual({ ok: false, reason: 'NOT_ENOUGH_SAMPLES' });
  });

  it('v1.1 의 ANCHOR_AMBIGUOUS 는 그대로 옮긴다 — ENGINE_ERROR 로 뭉개지 않는다', () => {
    const r = toCalibrationResult(
      { ...passed, status: 'RETRY_REQUIRED', reason: 'ANCHOR_AMBIGUOUS' },
      model,
    );
    expect(r).toMatchObject({ ok: true, ref: { quality: 'POOR' }, advice: 'ANCHOR_AMBIGUOUS' });
  });

  it('모르는 사유는 ENGINE_ERROR 로 둔다 — 재시도 안내는 나가야 한다', () => {
    const r = toCalibrationResult(
      { ...passed, status: 'RETRY_REQUIRED', reason: 'SOMETHING_NEW' },
      model,
    );
    expect(r).toMatchObject({ ok: true, advice: 'ENGINE_ERROR' });
  });
});

describe('버전 문자열', () => {
  it('모델 버전 · 백본 · 분류기 순서로 잇는다', () => {
    expect(
      engineVersion({
        modelVersion: 'gaze_v1.1.0',
        gazeBackbone: 'head_pose',
        gazeClassifier: 'reference_anchor_v1',
      }),
    ).toBe('gaze_v1.1.0+head_pose+reference_anchor_v1');
  });
});

describe('카메라 배치 → PlacementResult', () => {
  it('TOP 이면 지원된다', () => {
    expect(toPlacementResult({ placement: 'TOP', supported: true, reason: 'OK' })).toEqual({
      placement: 'TOP',
      supported: true,
      reason: 'OK',
    });
  });

  it('화면 아래·옆은 지원되지 않는다', () => {
    expect(
      toPlacementResult({ placement: 'BOTTOM', supported: false, reason: 'OK' }),
    ).toMatchObject({ placement: 'BOTTOM', supported: false });
  });

  it('TOP 이 아닌데 supported 가 true 로 와도 false 로 둔다', () => {
    expect(
      toPlacementResult({ placement: 'SIDE_LEFT', supported: true, reason: 'OK' }),
    ).toMatchObject({ supported: false });
  });

  it('판별이 어려우면 사유를 그대로 옮긴다', () => {
    expect(
      toPlacementResult({
        placement: 'INCONCLUSIVE',
        supported: false,
        reason: 'TARGETS_NOT_SEPARATED',
      }),
    ).toEqual({ placement: 'INCONCLUSIVE', supported: false, reason: 'TARGETS_NOT_SEPARATED' });
  });

  it('모르는 값은 INCONCLUSIVE · ENGINE_ERROR 로 둔다', () => {
    expect(toPlacementResult({ placement: 'CEILING', supported: true, reason: 'NEW' })).toEqual({
      placement: 'INCONCLUSIVE',
      supported: false,
      reason: 'ENGINE_ERROR',
    });
  });
});
