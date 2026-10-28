import { afterEach, describe, expect, it, vi } from 'vitest';
import { measureTrackOf, openMeasureStream } from './level';

/** 녹음용 스트림 흉내 — 오디오 트랙 하나와 그 장치 id 만 있으면 됩니다 */
function recordingStream(): MediaStream {
  const track = { readyState: 'live', getSettings: () => ({ deviceId: 'mic-1' }) };
  return { getAudioTracks: () => [track] } as unknown as MediaStream;
}

/** getUserMedia 가 돌려줄 측정 트랙. 브라우저가 실제로 적용한 설정만 흉내 냅니다 */
function measureStream(settings: MediaTrackSettings): MediaStream {
  const track = { getSettings: () => settings };
  return { getAudioTracks: () => [track] } as unknown as MediaStream;
}

function stubGetUserMedia(impl: () => Promise<MediaStream>) {
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: vi.fn(impl) } });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('측정 트랙 → 발표 전 점검 결과의 measure_track (#609)', () => {
  it('측정 트랙을 못 받으면 FALLBACK 이다 — 녹음용 스트림으로 계속 잰다', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    stubGetUserMedia(() => Promise.reject(new DOMException('busy', 'NotReadableError')));

    const measure = await openMeasureStream(recordingStream());
    expect(measure).toBeNull();
    expect(measureTrackOf(measure)).toBe('FALLBACK');
  });

  it('측정 트랙을 받고 자동 음량 조절이 꺼졌으면 UNPROCESSED 이다', async () => {
    stubGetUserMedia(() => Promise.resolve(measureStream({ autoGainControl: false })));

    expect(measureTrackOf(await openMeasureStream(recordingStream()))).toBe('UNPROCESSED');
  });

  it('측정 트랙은 받았는데 브라우저가 자동 음량 조절을 끄지 않았으면 PROCESSED 이다', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    stubGetUserMedia(() => Promise.resolve(measureStream({ autoGainControl: true })));

    expect(measureTrackOf(await openMeasureStream(recordingStream()))).toBe('PROCESSED');
  });
});
