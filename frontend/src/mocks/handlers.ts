import { http, HttpResponse } from 'msw';
import { authHandlers } from './auth';
import { sttHandlers } from './stt';
import type {
  AnalysisStatus,
  CreateTakeRequest,
  CreateTakeResponse,
  HomeResponse,
  PitchDetail,
  PrepareResponse,
  TakeContext,
  TakeReport,
} from '@/types/api';

/**
 * 인터페이스 명세 8-4의 JSON을 그대로 옮긴 목.
 *
 * 이 파일이 W4의 핵심 산출물입니다. BE·AI를 기다리지 않고 19개 화면을
 * 끝까지 만들 수 있게 해주고, W8 연동 때는 이 파일만 걷어냅니다.
 *
 * 규칙: 명세가 바뀌면 여기부터 고칩니다. 값을 임의로 지어내지 마세요 —
 * 시연 데이터가 화면마다 어긋나는 사고(시안의 09:42 vs 09:58)가 여기서 시작됩니다.
 */

// ★ 경로 앞에 와일드카드를 붙입니다.
//
//   .env 의 VITE_API_BASE 가 채워져 있으면 요청이 http://localhost:8000/api/... 로
//   나갑니다. 상대 경로 핸들러는 **같은 origin 요청만** 잡으므로 그때 목이 통째로
//   새고, 화면에는 네트워크 오류만 뜹니다. mocks/auth.ts 가 같은 이유로 그렇게 씁니다.
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

/**
 * 준비 화면(P4)이 쓰는 값. 홈의 p1과 같은 Pitch입니다 —
 * 화면마다 제목·버전·Take 번호가 어긋나면 시연에서 바로 티가 납니다.
 */
// 명세 8-4의 표와 열을 맞춰 둔다 — 나란히 놓고 값을 대조하는 게 이 파일의 용도다.
// prettier-ignore
const prepare: PrepareResponse = {
  pitchId: 'p1', title: '캡스톤 최종 발표',
  presentationVersion: 2, scriptVersion: 2, timeLimitSec: 600,
  nextTakeNumber: 4,
  lastMission: { id: 'm1', description: 'Slide 6을 Keyword Mode로 설명하기' },
  criteria: {
    version: 1, readOnly: true,
    items: [
      { id: 'c1', order: 1, text: '시장 규모 숫자를 반드시 말하고 출처도 덧붙인다' },
      { id: 'c2', order: 2, text: '대본을 보지 않고 도입부를 말한다' },
      { id: 'c3', order: 3, text: '군더더기 표현 5회 이하' },
      { id: 'c4', order: 4, text: '청중 절반 이상 보기' },
      { id: 'c5', order: 5, text: '마지막 슬라이드에서 목표 금액을 분명하게 말한다' },
    ],
  },
  defaultScriptMode: 'HIGHLIGHT',
};

/**
 * 발급한 Take. clientSessionId가 멱등키입니다 —
 * 같은 값으로 다시 부르면 **새 Take를 만들지 않습니다.**
 * 준비 화면에서 시작 버튼을 두 번 눌러도 빈 Take가 쌓이면 안 됩니다.
 */
const issuedTakes = new Map<string, CreateTakeResponse>();

/**
 * 발표 자료와 대본. 리허설 화면이 **시작 전에 한 번에 다 받아 둡니다** —
 * 발표 중에 네트워크를 타면 그 순간 화면이 빕니다 (CLAUDE.md 4번).
 *
 * imageUrl 은 빈 문자열입니다. 목에는 실제 이미지가 없고, 화면은 빈 값일 때
 * 자리표시(SLIDE n · 16:9)를 그립니다 — 없는 URL 을 지어내면 깨진 이미지가 뜹니다.
 */
const SLIDE_COUNT = 12;

/** 슬라이드당 한 문단. 대본은 여기서 이어 붙이고, 앵커는 길이로 계산합니다 */
const SCRIPT_PARAGRAPHS = [
  '안녕하세요. 저희가 만든 서비스는 발표 연습을 도와주는 피치코치입니다.',
  '발표 연습은 혼자 합니다. 그런데 혼자서는 자기 말하기가 어떤지 알 수 없습니다.',
  '말이 빨라졌는지, 청중을 보고 있는지, 대본에 얼마나 기대고 있는지는 본인이 가장 모릅니다.',
  '그래서 저희는 두 가지 축을 잡았습니다. 하나는 말하기 속도와 채움말, 다른 하나는 시선입니다.',
  '먼저 말하기 속도를 보겠습니다. 3번 슬라이드가 기준선입니다.',
  '시장 규모는 작년 기준 1조 2천억 원이고, 출처는 한국콘텐츠진흥원 보고서입니다.',
  '경쟁 서비스는 녹화 후 되돌려 보기에 머물러 있습니다. 저희는 다음 Take에서 고칠 것 하나를 줍니다.',
  '시선은 브라우저 안에서만 계산합니다. 카메라 영상은 서버로 보내지 않습니다.',
  '판정은 1초 단위이고, 청중과 화면 두 갈래로만 나눕니다. 억지로 쪼개면 정확도가 떨어집니다.',
  '리포트는 점수를 매기지 않습니다. 지난 Take보다 무엇이 나아졌는지만 말합니다.',
  '지금까지 팀 내부에서 38번의 Take를 쌓았고, 평균 채움말이 9회에서 4회로 줄었습니다.',
  '목표 금액은 2억 원이고, 6개월 안에 학교 단위 도입 세 곳을 만들겠습니다.',
];

const SLIDE_KEYWORDS = [
  ['피치코치', '발표 연습'],
  ['혼자', '알 수 없다'],
  ['속도', '시선', '대본'],
  ['두 가지 축'],
  ['기준선', '3번'],
  ['시장 규모', '1조 2천억', '출처'],
  ['경쟁', '다음 Take'],
  ['온디바이스', '서버 전송 없음'],
  ['1초 판정', '2분할'],
  ['점수 없음', '변화'],
  ['38 Take', '채움말 9 → 4'],
  ['목표 2억', '6개월'],
];

const scriptContent = SCRIPT_PARAGRAPHS.join('\n\n');

/** 각 슬라이드가 시작되는 글자 위치. 대본 자동 스크롤이 이 값을 씁니다 */
const slideAnchors = SCRIPT_PARAGRAPHS.map((_, i) => ({
  slideNumber: i + 1,
  charOffset: SCRIPT_PARAGRAPHS.slice(0, i).reduce((n, p) => n + p.length + 2, 0),
}));

// 명세 8-4의 표와 열을 맞춰 둔다 — 나란히 놓고 값을 대조하는 게 이 파일의 용도다.
// prettier-ignore
const pitchDetail: PitchDetail = {
  id: 'p1', title: '캡스톤 최종 발표', timeLimitSec: 600,
  presentationDate: '2026-09-18', bestTakeId: null,
  presentation: {
    id: 'pres1', version: 2, pageCount: SLIDE_COUNT, convertStatus: 'COMPLETED',
    slides: Array.from({ length: SLIDE_COUNT }, (_, i) => ({
      slideNumber: i + 1,
      imageUrl: '',
      // 발표 중에 만료되면 화면이 깨집니다. 목은 넉넉히 둡니다
      imageUrlExpiresAt: new Date(Date.now() + 6 * 60 * 60 * 1000).toISOString(),
      keywords: (SLIDE_KEYWORDS[i] ?? []).map((text, k) => ({ text, required: k === 0 })),
    })),
  },
  script: {
    id: 'scr1', version: 2, content: scriptContent,
    charCount: scriptContent.length, estDurationSec: 585, estBasisWpm: 150,
    emphasisSpans: [], slideAnchors,
  },
};

/** 발급된 Take 가 없으면(목을 새로 켠 직후) 이 값으로 답합니다 */
const fallbackTake: TakeContext = {
  takeId: 't4',
  takeNumber: prepare.nextTakeNumber,
  pitchId: prepare.pitchId,
  pitchTitle: prepare.title,
  mode: 'COACHING',
  scriptMode: prepare.defaultScriptMode,
  timeLimitSec: prepare.timeLimitSec,
  status: 'READY',
  mission: prepare.lastMission,
};

/** POST /takes 로 발급할 때 화면이 고른 값을 기억해 둡니다 (새로고침 복귀용) */
const takeContexts = new Map<string, TakeContext>();

export const handlers = [
  ...authHandlers,
  ...sttHandlers,
  http.get('*/api/home', () => HttpResponse.json(home)),

  http.get('*/api/takes/:takeId/report', ({ params }) =>
    HttpResponse.json(params.takeId === 't2' ? reportExcluded : report),
  ),

  http.get('*/api/takes/:takeId/status', () => HttpResponse.json(analyzing)),

  http.post('*/api/takes/:takeId/complete', async ({ params }) =>
    HttpResponse.json({ takeId: params.takeId, status: 'ANALYZING' }, { status: 202 }),
  ),

  http.post('*/api/takes/:takeId/calibration', () => new HttpResponse(null, { status: 204 })),

  http.get('*/api/pitches/:pitchId/prepare', ({ params }) =>
    HttpResponse.json({ ...prepare, pitchId: String(params.pitchId) }),
  ),

  // ★ Take는 여기서만 생깁니다 (CLAUDE.md 8번). 홈·리포트의 'Take N 시작'은 이동만 합니다.
  http.post('*/api/takes', async ({ request }) => {
    const body = (await request.json()) as CreateTakeRequest;
    const seen = issuedTakes.get(body.clientSessionId);
    if (seen) return HttpResponse.json(seen, { status: 200 });

    const created: CreateTakeResponse = {
      takeId: `t${prepare.nextTakeNumber + issuedTakes.size}`,
      takeNumber: prepare.nextTakeNumber,
      status: 'READY',
    };
    issuedTakes.set(body.clientSessionId, created);
    takeContexts.set(created.takeId, {
      ...fallbackTake,
      takeId: created.takeId,
      takeNumber: created.takeNumber,
      pitchId: body.pitchId,
      mode: body.mode,
      scriptMode: body.scriptMode,
      status: 'READY',
    });
    return HttpResponse.json(created, { status: 201 });
  }),

  // ★ :takeId 보다 먼저 와야 합니다 — 안 그러면 in-progress 가 takeId 로 잡힙니다
  http.get('*/api/takes/in-progress', () => HttpResponse.json(null)),

  http.get('*/api/takes/:takeId', ({ params }) => {
    const takeId = String(params.takeId);
    return HttpResponse.json(takeContexts.get(takeId) ?? { ...fallbackTake, takeId });
  }),

  http.get('*/api/pitches/:pitchId', ({ params }) =>
    HttpResponse.json({ ...pitchDetail, id: String(params.pitchId) }),
  ),
];
