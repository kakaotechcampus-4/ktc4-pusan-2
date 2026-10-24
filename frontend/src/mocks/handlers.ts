import { homeHandlers } from './home';
import { http, HttpResponse } from 'msw';
import { authHandlers } from './auth';
import { sttHandlers } from './stt';
import { presentationHandlers } from './presentation';
import { standardsHandlers } from './standards';
import { pitchHandlers } from './pitch';
import { pitchEditHandlers } from './pitchEdit';
import { scriptHandlers } from './scripts';
import { takeHistoryHandlers } from './takeHistory';
import type { TakeCreated, TakeCreateRequest } from '@/types/take';
import type { ScriptDetail } from '@/types/script';
import { reportHandlers } from './report';
import type { HomeResponse, PrepareResponse } from '@/types/api';

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

/**
 * 장치 점검 준비 정보 — BE #296 `PrepareDTO` 모양 그대로(쪽수 · 장 수는 #298). 홈의 p1과 같은 Pitch입니다 —
 * 화면마다 제목·버전·Take 번호가 어긋나면 시연에서 바로 티가 납니다.
 */
const prepare: PrepareResponse = {
  pitch_id: 'p1',
  title: '캡스톤 최종 발표',
  time_limit_sec: 600,
  presentation: { id: 'pv2', version: 2, page_count: 12 },
  script: { id: 'sv2', version: 2, slide_count: 12 },
  newer_script: null,
  next_take_number: 4,
  default_script_mode: 'HIGHLIGHT',
  // BE #409 — 리포트 목(Take 3 리뷰 `next_missions`)과 같은 미션입니다
  missions: {
    source_take_number: 3,
    items: [
      {
        mission_id: 'm1',
        priority: 1,
        area: 'FILLER',
        slide_number: null,
        description: '추임새 5번 이하',
        target: { metric: 'filler_count', operator: 'LTE', value: 5 },
        reason: '직접 정한 기준인데 이번 Take 7번',
      },
      {
        mission_id: 'm2',
        priority: 2,
        area: 'GAZE',
        slide_number: 3,
        description: '3번 장 대본 응시 40% 이하',
        target: { metric: 'script_ratio', operator: 'LTE', value: 0.4 },
        reason: '3번 장 이번 Take 0.62, 이전 Take 0.70',
      },
    ],
  },
};

/**
 * 준비 정보가 다른 상태도 눈으로 보려고, 주소의 피치 id 로 고릅니다.
 *   /pitch/prep-newer/device-check     새 대본(v3)이 아직 나누는 중 — 옛 대본으로 연습한다는 안내
 *   /pitch/prep-no-script/device-check 쓸 수 있는 대본이 없음 — 시작을 막음
 *   /pitch/prep-mismatch/device-check  쪽수(10)와 장 수(12)가 다름 — 시작을 막음
 *   /pitch/prep-first/device-check     첫 Take — 미션 없음(null)
 *   /pitch/prep-no-mission/device-check   리뷰는 끝났지만 다음 미션이 없음(items: [])
 *   /pitch/prep-older-mission/device-check Take 3 리뷰가 없어 Take 2 리뷰의 미션
 */
function prepareFor(pitchId: string): PrepareResponse {
  const base = { ...prepare, pitch_id: pitchId };
  switch (pitchId) {
    case 'prep-newer':
      return {
        ...base,
        newer_script: { id: 'sv3', version: 3, parse_status: 'PENDING', error_code: null },
      };
    case 'prep-no-script':
      return { ...base, script: null };
    case 'prep-mismatch':
      return { ...base, presentation: { id: 'pv2', version: 2, page_count: 10 } };
    case 'prep-first':
      return { ...base, next_take_number: 1, missions: null };
    case 'prep-no-mission':
      return { ...base, missions: { source_take_number: 3, items: [] } };
    case 'prep-older-mission':
      return { ...base, missions: { ...prepare.missions!, source_take_number: 2 } };
    default:
      return base;
  }
}

/** 발급한 Take 수와, 세션 id(멱등 키)별로 처음 만든 Take */
let issuedTakes = 0;
const takesBySession = new Map<string, TakeCreated>();

/**
 * 목 데모 대본 — 슬라이드당 한 문단. 아래 `sampleScript` 가 BE 모양으로 묶어 리허설에 줍니다.
 * 발표자료(PDF)는 목에 없어서, 이 데모로 들어온 리허설은 슬라이드 자리표시를 그립니다.
 */
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

/**
 * 준비 화면(`prepare.scriptVersionId`)이 가리키는 대본. 홈 → 준비 → 리허설로 들어오는 목 데모가
 * 피치 생성을 거치지 않아도 대본이 보이게, BE 의 `GET /scripts/{id}` 모양으로 둡니다.
 */
const sampleScript: ScriptDetail = {
  script_version_id: prepare.script!.id,
  version: prepare.script!.version,
  original_content: SCRIPT_PARAGRAPHS.map((p, i) => `슬라이드 ${i + 1}\n${p}`).join('\n'),
  parse_status: 'DONE',
  segmented: true,
  slides: SCRIPT_PARAGRAPHS.map((content, i) => ({
    slide_number: i + 1,
    content,
    keywords: SLIDE_KEYWORDS[i] ?? [],
    highlights: [],
  })),
  terms: [],
  error_code: null,
};

export const handlers = [
  // ★ 대본 목(scriptHandlers)보다 먼저 와야 합니다 — 그쪽은 모르는 id 에 404 를 돌려줍니다
  http.get(`*/api/pitches/:pitchId/scripts/${prepare.script!.id}`, () =>
    HttpResponse.json(sampleScript),
  ),
  // 수정 화면 목 — 업로드 · 대본 목보다 먼저 와야 합니다 (그쪽은 모르는 id 에 404)
  ...pitchEditHandlers,
  ...homeHandlers,
  ...takeHistoryHandlers,
  ...authHandlers,
  ...sttHandlers,
  ...presentationHandlers,
  ...standardsHandlers,
  ...pitchHandlers,
  ...scriptHandlers,
  http.get('*/api/home', () => HttpResponse.json(home)),

  ...reportHandlers,

  // BE 모양 그대로 — PUT /pitches/{id}/takes/{take_id} 에 TakeUpdateRequestDTO, 응답 {message, take_id}.
  // 발표 종료가 시작 · 종료 시각을 여기로 적습니다 (옛 /complete 는 BE 가 만들지 않기로 함, #134)
  http.put('*/api/pitches/:pitchId/takes/:takeId', ({ params }) =>
    HttpResponse.json({ message: 'Take updated successfully', take_id: String(params.takeId) }),
  ),

  // BE 모양 그대로 — 창을 닫았다 다시 연 Take 를 실패로 끝냅니다. 응답 {message, take_id, status}
  http.post('*/api/pitches/:pitchId/takes/:takeId/abandon', ({ params }) =>
    HttpResponse.json({
      message: 'Take abandoned',
      take_id: String(params.takeId),
      status: 'FAILED',
    }),
  ),

  // BE 모양 그대로 — POST /pitches/{id}/takes/{take_id}/calibration, 응답 {message, calibration_id}
  http.post('*/api/pitches/:pitchId/takes/:takeId/calibration', () =>
    HttpResponse.json({ message: 'Calibration created successfully', calibration_id: 'cal1' }),
  ),

  http.get('*/api/pitches/:pitchId/prepare', ({ params }) =>
    HttpResponse.json(prepareFor(String(params.pitchId))),
  ),

  // ★ Take는 여기서만 생깁니다 (CLAUDE.md 8번). 홈·리포트의 'Take N 시작'은 이동만 합니다.
  //   BE 모양 그대로입니다 — POST /pitches/{id}/takes/ 에 TakeInitRequestDTO, 응답은 {message, take_id, take_number}.
  //   BE 처럼 같은 client_session_id 로 다시 오면 새로 만들지 않고 처음 Take 를 돌려줍니다 (#296)
  http.post('*/api/pitches/:pitchId/takes/', async ({ request }) => {
    const body = (await request.json()) as TakeCreateRequest;
    const again = body.client_session_id ? takesBySession.get(body.client_session_id) : undefined;
    if (again) return HttpResponse.json(again);
    const takeNumber = prepare.next_take_number + issuedTakes;
    issuedTakes += 1;
    const res: TakeCreated = {
      message: 'Take created successfully',
      take_id: `t${takeNumber}`,
      take_number: takeNumber,
    };
    if (body.client_session_id) takesBySession.set(body.client_session_id, res);
    return HttpResponse.json(res);
  }),

  http.get('*/api/takes/in-progress', () => HttpResponse.json(null)),
];
