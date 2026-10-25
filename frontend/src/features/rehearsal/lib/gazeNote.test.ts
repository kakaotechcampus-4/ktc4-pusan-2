import { describe, expect, it } from 'vitest';
import { gazeNote } from './gazeNote';

const ok = {
  declined: false,
  noCamera: false,
  cameraLost: false,
  missingCalibration: false,
  error: null,
  ready: true,
} as const;

describe('무대 발밑 줄의 시선 안내', () => {
  it('정상으로 기록 중이면 아무것도 띄우지 않는다 — 발표 중에 읽을 글자를 줄인다', () => {
    expect(gazeNote(ok)).toBeNull();
  });

  it('카메라 없이 시작하면 연결이 끊겼다는 안내 대신 시선 미기록을 알린다', () => {
    expect(gazeNote({ ...ok, noCamera: true, cameraLost: true, ready: false })).toEqual({
      text: '카메라를 열지 못해 시선은 기록하지 않아요',
      alert: false,
    });
    expect(gazeNote({ ...ok, noCamera: true, declined: true })?.text).toBe(
      '카메라 권한이 없어 시선은 기록하지 않아요',
    );
  });

  it('카메라가 끊기면 고칠 수 있으니 눈에 띄게 알린다', () => {
    expect(gazeNote({ ...ok, cameraLost: true })).toEqual({
      text: '카메라가 연결되지 않았어요 — 연결을 확인해 주세요',
      alert: true,
    });
    expect(gazeNote({ ...ok, error: 'CAMERA_LOST' })?.alert).toBe(true);
  });

  it('새로고침으로 시선 기준이 사라지면 이후 시선은 기록하지 않는다고 알린다 — 실전 모드에도 보인다', () => {
    expect(gazeNote({ ...ok, missingCalibration: true, error: 'CALIBRATION_LOST' })).toEqual({
      text: '시선 기준이 없어 이후 시선은 기록하지 않아요',
      alert: false,
    });
  });

  it('권한 거부 · 엔진 오류도 한 줄은 남긴다 — 오류 코드가 아니라 문장으로', () => {
    expect(gazeNote({ ...ok, declined: true })?.text).toBe(
      '카메라 권한이 없어 시선은 기록하지 않아요',
    );
    expect(gazeNote({ ...ok, error: 'ENGINE_UNAVAILABLE', ready: false })?.text).toBe(
      '시선 분석을 쓸 수 없어 시선은 기록하지 않아요',
    );
    const other = gazeNote({ ...ok, error: 'LOW_CONFIDENCE' });
    expect(other?.text).toBe('시선은 기록하지 않아요');
    expect(other?.text).not.toContain('LOW_CONFIDENCE');
  });

  it('엔진이 뜨기 전에는 준비 중이라고 알리고, 뜨면 지운다', () => {
    expect(gazeNote({ ...ok, ready: false })?.text).toBe('시선 분석을 준비하는 중이에요');
    expect(gazeNote({ ...ok, ready: true })).toBeNull();
  });

  it('여럿이 겹치면 권한 → 카메라 → 기준 → 엔진 순서로 하나만 말한다', () => {
    expect(
      gazeNote({
        declined: true,
        noCamera: false,
        cameraLost: true,
        missingCalibration: true,
        error: null,
        ready: false,
      })?.text,
    ).toBe('카메라 권한이 없어 시선은 기록하지 않아요');
    expect(
      gazeNote({ ...ok, cameraLost: true, missingCalibration: true, error: 'ENGINE_UNAVAILABLE' })
        ?.alert,
    ).toBe(true);
  });
});
