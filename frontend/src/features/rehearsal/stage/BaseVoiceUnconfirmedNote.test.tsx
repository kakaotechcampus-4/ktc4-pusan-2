// @vitest-environment jsdom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { BASE_VOICE_UNCONFIRMED_TEXT, isBaseVoiceUnconfirmed } from '../lib/sendCalibration';
import { BaseVoiceUnconfirmedNote } from './BaseVoiceUnconfirmedNote';

/** 무대가 하는 그대로 — 지금 주소의 라우터 state 를 넘깁니다 */
function StageNotes() {
  return (
    <div className="stage-status">
      <BaseVoiceUnconfirmedNote state={useLocation().state} />
    </div>
  );
}

let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

/** 장치 점검이 넘기는 주소 · state 로 무대 자리를 엽니다. 실시간 코칭과 실전은 같은 무대(`RehearsalPage`)입니다 */
async function open(path: string, state: unknown) {
  await act(async () =>
    root.render(
      <MemoryRouter initialEntries={[{ pathname: path, state }]}>
        <Routes>
          <Route path="/takes/:takeId/rehearsal" element={<StageNotes />} />
          <Route path="/takes/:takeId/exam" element={<StageNotes />} />
        </Routes>
      </MemoryRouter>,
    ),
  );
}
const status = () => host.querySelector('.stage-status [role="status"]');

// #1184 리뷰 — 저장 실패 안내가 실제로 그려지는지 · 숨는지
it.each(['/takes/t1/rehearsal', '/takes/t1/exam'])(
  '%s: 목소리 기준을 확인하지 못했으면 안내를 role="status" 로 띄운다',
  async (path) => {
    await open(path, { clientSessionId: 'c1', baseVoiceUnconfirmed: true });
    expect(status()?.textContent).toBe(BASE_VOICE_UNCONFIRMED_TEXT);
  },
);

it.each(['/takes/t1/rehearsal', '/takes/t1/exam'])(
  '%s: 저장됐거나 탈출구로 시작했으면 안내를 그리지 않는다',
  async (path) => {
    await open(path, { clientSessionId: 'c1', baseVoiceUnconfirmed: false });
    expect(status()).toBeNull();
    expect(host.textContent).not.toContain(BASE_VOICE_UNCONFIRMED_TEXT);
  },
);

it('state 가 없거나(새로고침 전 다른 길) 모양이 다르면 그리지 않는다', () => {
  expect(isBaseVoiceUnconfirmed(null)).toBe(false);
  expect(isBaseVoiceUnconfirmed(undefined)).toBe(false);
  expect(isBaseVoiceUnconfirmed({ clientSessionId: 'c1' })).toBe(false);
  expect(isBaseVoiceUnconfirmed({ baseVoiceUnconfirmed: 'true' })).toBe(false);
  expect(isBaseVoiceUnconfirmed({ baseVoiceUnconfirmed: true })).toBe(true);
});
