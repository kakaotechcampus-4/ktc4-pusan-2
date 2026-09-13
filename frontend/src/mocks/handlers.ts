import { http, HttpResponse } from 'msw';
import { authHandlers } from './auth';
import type { AnalysisStatus, HomeResponse, TakeReport } from '@/types/api';

/**
 * 인터페이스 명세 8-4의 JSON을 그대로 옮긴 목.
 *
 * 이 파일이 W4의 핵심 산출물입니다. BE·AI를 기다리지 않고 19개 화면을
 * 끝까지 만들 수 있게 해주고, W8 연동 때는 이 파일만 걷어냅니다.
 *
 * 규칙: 명세가 바뀌면 여기부터 고칩니다. 값을 임의로 지어내지 마세요 —
 * 시연 데이터가 화면마다 어긋나는 사고(시안의 09:42 vs 09:58)가 여기서 시작됩니다.
 */

const ENGINE = 'face-landmarker@0.10.3+mobileone-s0@1.0+vote-v1';

// 명세 8-4의 표와 열을 맞춰 둔다 — 나란히 놓고 값을 대조하는 게 이 파일의 용도다.
// prettier-ignore
const home: HomeResponse = {
  nearestPitch: { title: '캡스톤 최종 발표', daysUntil: 3 },
  nextMission: {
    pitchId: 'p1',
    nextTakeNumber: 4,
    missions: [{ id: 'm1', priority: 1, description: 'Slide 6을 Keyword Mode로 설명하기' }],
  },
  weeklyTakeCount: 3,
  weeklyDelta: 1,
  pitches: [
    {
      id: 'p1', title: '캡스톤 최종 발표', timeLimitSec: 600, daysUntil: 3,
      presentationVersion: 2, scriptVersion: 2, bestTakeId: null,
      takes: [
        { id: 't1', takeNumber: 1, status: 'COMPLETED', isBest: false },
        { id: 't2', takeNumber: 2, status: 'COMPLETED', isBest: false },
        { id: 't3', takeNumber: 3, status: 'COMPLETED', isBest: false },
      ],
    },
  ],
  inProgressTake: null,
};

// 명세 8-4의 표와 열을 맞춰 둔다 — 나란히 놓고 값을 대조하는 게 이 파일의 용도다.
// prettier-ignore
const report: TakeReport = {
  takeId: 't3', takeNumber: 3, mode: 'COACHING', scriptMode: 'HIGHLIGHT',
  headline: '이번 Take, 대본에서 한 발 떨어졌어요',
  summary: {
    durationMs: 598_000, timeLimitMs: 600_000, overTimeMs: -2_000,
    averageWpm:            { value: 156,  delta: -12,   direction: 'IMPROVED' },
    fillerCount:           { value: 6,    delta: -3,    direction: 'IMPROVED' },
    scriptSimilarity:      { value: 0.62, delta: -0.14, direction: 'IMPROVED' },
    scriptDependencyCount: { value: 1,    delta: -2,    direction: 'IMPROVED' },
    overallConfidence: 0.88,
  },
  gaze: {
    excluded: false, excludedReason: null,
    engineVersion: ENGINE, engineProfile: 'NORMAL', decisionIntervalMs: 1000,
    trackedMs: 560_000, measuredMs: 526_000, uncertainMs: 34_000,
    cameraMs: 352_000, bottomMs: 174_000,
    uncertainRatio: 0.061, coverageRatio: 0.88, reliability: 'HIGH',
  },
  findings: [{
    id: 'f1', type: 'SCRIPT_DEPENDENCY', polarity: 'ISSUE',
    slideNumber: 6, startMs: 252_000, endMs: 294_000, severity: 2,
    title: '대본 의존도가 높았습니다',
    message: '이 구간의 발화가 준비한 대본과 거의 같았고, 화면을 오래 바라봤습니다.',
    evidence: [
      { label: '대본 일치율', value: '91%' },
      { label: '화면 응시 비율', value: '71%' },
      { label: '말하기 속도', value: '178 WPM' },
    ],
  }],
  missions: [{ id: 'm1', priority: 1, slideNumber: 6, description: 'Slide 6을 Keyword Mode로 설명하기', result: null }],
  hasRecording: false,
};

/** 16 측정 제외 — 0이 아니라 null. 이 케이스를 목에 꼭 남겨두세요. */
// 명세 8-4의 표와 열을 맞춰 둔다 — 나란히 놓고 값을 대조하는 게 이 파일의 용도다.
// prettier-ignore
const reportExcluded: TakeReport = {
  ...report,
  takeId: 't2', takeNumber: 2,
  gaze: {
    excluded: true, excludedReason: 'ENGINE_UNAVAILABLE',
    engineVersion: ENGINE, engineProfile: 'OFF', decisionIntervalMs: 1000,
    trackedMs: null, measuredMs: null, uncertainMs: null,
    cameraMs: null, bottomMs: null,
    uncertainRatio: null, coverageRatio: null, reliability: null,
  },
};

// 명세 8-4의 표와 열을 맞춰 둔다 — 나란히 놓고 값을 대조하는 게 이 파일의 용도다.
// prettier-ignore
const analyzing: AnalysisStatus = {
  status: 'ANALYZING',
  steps: [
    { name: 'UPLOAD',  status: 'COMPLETED' },
    { name: 'STT',     status: 'RUNNING' },
    { name: 'CONTENT', status: 'PENDING' },
    { name: 'REPORT',  status: 'PENDING' },
  ],
  estimatedRemainSec: 45,
};

export const handlers = [
  ...authHandlers,
  http.get('/api/home', () => HttpResponse.json(home)),

  http.get('/api/takes/:takeId/report', ({ params }) =>
    HttpResponse.json(params.takeId === 't2' ? reportExcluded : report),
  ),

  http.get('/api/takes/:takeId/status', () => HttpResponse.json(analyzing)),

  http.post('/api/takes/:takeId/complete', async ({ params }) =>
    HttpResponse.json({ takeId: params.takeId, status: 'ANALYZING' }, { status: 202 }),
  ),

  http.post('/api/takes/:takeId/calibration', () => new HttpResponse(null, { status: 204 })),

  http.get('/api/takes/in-progress', () => HttpResponse.json(null)),
];
