/**
 * ============================================================
 *  서버와의 계약. 명세 8-4 를 옮긴 것입니다.
 * ============================================================
 *
 * ── 왜 shared/api/ 아래가 아니라 src/types/ 인가 ────────────────────
 *
 * `shared/api/` 에는 쿼리 훅과 HTTP 클라이언트가 들어갑니다. 런타임 코드입니다.
 * 이 파일은 **타입만** 있고, API 를 거치지 않는 곳에서도 씁니다:
 *
 *   workers/gaze.contract.ts     워커. shared/api 를 참조하면 안 되는 곳
 *   workers/temporalVoter.ts     워커
 *   features/rehearsal/lib/db.ts IndexedDB. 서버 통신과 무관
 *   mocks/handlers.ts            목 서버. 쿼리 훅의 반대편
 *
 * 이걸 `shared/api/` 아래로 내리면 **워커가 `shared/api/` 를 참조**하게 됩니다.
 * 타입만 가져오면 런타임 코드가 딸려오지는 않지만, import 경로가 그렇게 생기면
 * 나중에 누군가 같은 경로에서 `client.ts` 를 가져오고, 그 순간 워커 번들에
 * TanStack Query 가 들어갑니다. 빌드는 통과하고 워커만 런타임에 죽습니다.
 *
 * 그래서 의존 방향을 이렇게 둡니다:
 *
 *   types/        런타임 코드 없음. 누구나 참조해도 된다 (잎사귀)
 *   shared/api/   types/ 를 참조한다. 역방향은 없다
 *
 * 명세가 바뀌면 여기부터 고칩니다.
 */

/**
 * 인터페이스 명세 8-4의 응답을 그대로 옮긴 타입.
 *
 * 규칙 두 가지 —
 *  1. 명세가 바뀌면 여기부터 고칩니다. 화면이 아니라.
 *  2. "없음"은 null이고 키는 유지합니다. optional(?)로 만들지 마세요.
 *     `gaze.cameraMs`가 없는 것과 null인 것은 화면에서 다르게 그려집니다.
 */

export type Ms = number;

export type Mode = 'COACHING' | 'EXAM';
/**
 * 3단계. OFF는 대본 영역 자체가 사라진다 (높이 0).
 *
 * ── FULL 이 없는 이유 (시안 09 기준, 2026-09-22 변경) ────────────────
 * 전에는 `FULL | HIGHLIGHT | KEYWORD | OFF` 넷이었습니다. FULL(전체 대본)과
 * HIGHLIGHT(전체 대본 + 강조)를 **HIGHLIGHT 하나로 합쳤습니다** — 화면에 보이는
 * 글은 둘 다 대본 전체이고, 강조가 붙느냐만 달랐습니다. 고르는 사람에게는
 * 같은 것이 둘로 보였습니다.
 *
 * 대본 영역 높이는 바뀌지 않습니다 — `--spacing-script-full` 과
 * `--spacing-script-highlight` 가 원래 둘 다 180px 이었습니다. 그래서 이 합치기는
 * CLAUDE.md 7번의 계측 조건에 걸리지 않고, 이 시점 이전 Take 와도 비교가 됩니다.
 *
 * ★ 서버가 아직 `FULL` 을 보낼 수 있습니다. 받는 쪽에서 접어서 씁니다 —
 *   `features/rehearsal/prepare/prepareStore.ts` 의 `normalizeScriptMode`.
 *   이 파일에는 런타임 코드를 두지 않습니다 (위 머리말의 잎사귀 규칙).
 */
export type ScriptMode = 'HIGHLIGHT' | 'KEYWORD' | 'OFF';
export type TakeStatus = 'READY' | 'RUNNING' | 'ANALYZING' | 'COMPLETED' | 'FAILED';

/** AI팀 파이프라인의 최종 출력. 4분할이 아니라 3값입니다. */
export type GazeZone = 'CAMERA' | 'BOTTOM' | 'UNCERTAIN';

export type GazeExcludedReason =
  | 'LOW_CONFIDENCE'
  | 'ENGINE_UNAVAILABLE'
  | 'CAMERA_LOST'
  | 'USER_DECLINED'
  | 'VALIDATION_FAILED'
  | 'UNCERTAIN_RATIO_EXCEEDED'
  /**
   * 판정은 나왔는데 IndexedDB 에 못 쌓은 구간이 있습니다 (저장소 가득 참 등).
   * 몇 초가 빠졌는지 알 수 없으므로 비율을 계산하면 조용히 틀린 숫자가 나옵니다.
   * 다른 사유들과 달리 **측정 자체는 정상이었다**는 점이 다릅니다.
   */
  | 'STORAGE_FAILED';

/** NORMAL 정상 · LIGHT 경량 모드 · OFF 시선 없이 진행 */
export type GazeEngineProfile = 'NORMAL' | 'LIGHT' | 'OFF';

export interface ApiError {
  code: string;
  /** 화면에 그대로 노출 가능한 문구 */
  message: string;
  detail: string | null;
}

/* ------------------------------------------------------------------ */
/* Pitch · 자료 · 대본                                                  */
/* ------------------------------------------------------------------ */

export interface Slide {
  slideNumber: number;
  imageUrl: string;
  /** 발표 중 만료되면 화면이 깨집니다. 최소 2시간 */
  imageUrlExpiresAt: string;
  keywords: { text: string; required: boolean }[];
}

export interface PitchDetail {
  id: string;
  title: string;
  timeLimitSec: number;
  presentationDate: string;
  bestTakeId: string | null;
  presentation: {
    id: string;
    version: number;
    pageCount: number;
    convertStatus: 'PROCESSING' | 'COMPLETED' | 'FAILED';
    slides: Slide[];
  };
  script: {
    id: string;
    version: number;
    content: string;
    charCount: number;
    estDurationSec: number;
    estBasisWpm: number;
    emphasisSpans: { start: number; end: number; type: string }[];
    slideAnchors: { slideNumber: number; charOffset: number }[];
  };
}

/* ------------------------------------------------------------------ */
/* 발표 종료 — POST /takes/{id}/complete                                */
/* ------------------------------------------------------------------ */

export interface GazeSegment {
  startMs: Ms;
  endMs: Ms;
  zone: GazeZone;
  confidence: number;
}

export interface CalibrationSummary {
  /** 2점 캘리브레이션 — 카메라 한 번, 화면 한 번 */
  points: 2;
  quality: 'GOOD' | 'FAIR' | 'POOR';
  /**
   * 두 기준이 얼마나 떨어져 있나. **모르면 null 입니다** —
   * A안에서 분류기는 등급(quality)만 주고 수치는 내지 않습니다.
   * 0 으로 채우면 "분리도가 0" 이라는 뜻이 되어 리포트가 거짓말을 합니다.
   */
  separability: number | null;
  coordinateSpace: 'raw' | 'mirrored';
  /** 해상도·배율·카메라 위치. 이게 다르면 다른 기기의 값이라 재사용 불가 */
  layoutSignature: string;
}

/**
 * 시간 필드 셋의 관계 — 이걸 안 맞추면 퍼센트가 전부 틀어집니다.
 *   measuredMs + uncertainMs === trackedMs
 *   모든 시선 퍼센트의 분모는 measuredMs (durationMs 아님)
 */
export interface GazePayload {
  /** 랜드마커 + gaze 모델 + 투표 규칙, 셋 다 담아야 합니다 */
  engineVersion: string;
  engineProfile: GazeEngineProfile;
  /** 1초 Temporal Voting — 구간의 최소 길이이기도 합니다 */
  decisionIntervalMs: Ms;
  excluded: boolean;
  excludedReason: GazeExcludedReason | null;
  /** 카메라가 살아 있고 얼굴이 잡힌 시간 */
  trackedMs: Ms;
  /** 판정에 성공한 시간 = CAMERA + BOTTOM */
  measuredMs: Ms;
  uncertainMs: Ms;
  calibration: CalibrationSummary | null;
  segments: GazeSegment[];
}

export interface CompleteRequest {
  /** 같은 값으로 재전송하면 서버는 새 Take를 만들지 않습니다 (P15 재시도) */
  clientSessionId: string;
  startedAt: string;
  endedAt: string;
  durationMs: Ms;
  mode: Mode;
  hiddenPanels: string[];
  slideEvents: { slideNumber: number; startMs: Ms; endMs: Ms }[];
  gaze: GazePayload;
  liveFeedbacks: {
    type: string;
    message: string;
    triggeredAtMs: Ms;
    confidence: number;
  }[];
  /** 띄우려다 참은 것과 사유 — 코칭 임계값 조정의 유일한 근거 */
  suppressedFeedbacks: { type: string; atMs: Ms; reason: string }[];
  clientPerf: {
    avgGazeFps: number;
    droppedFrames: number;
    degradedToLightAtMs: Ms | null;
  };
}

/* ------------------------------------------------------------------ */
/* 리포트 — GET /takes/{id}/report                                      */
/* ------------------------------------------------------------------ */

export interface Metric {
  value: number;
  /** 첫 Take는 null. 증감 화살표를 그리지 않습니다 */
  delta: number | null;
  direction: 'IMPROVED' | 'WORSENED' | 'SAME' | null;
}

export interface ReportGaze {
  excluded: boolean;
  excludedReason: GazeExcludedReason | null;
  engineVersion: string;
  engineProfile: GazeEngineProfile;
  decisionIntervalMs: Ms;
  trackedMs: Ms | null;
  measuredMs: Ms | null;
  uncertainMs: Ms | null;
  /** 제외 시 0이 아니라 null. 0이면 화면에 "청중을 0% 봤습니다"가 뜹니다 */
  cameraMs: Ms | null;
  bottomMs: Ms | null;
  uncertainRatio: number | null;
  coverageRatio: number | null;
  reliability: 'HIGH' | 'MEDIUM' | 'LOW' | null;
}

export interface Finding {
  id: string;
  type: string;
  polarity: 'ISSUE' | 'GOOD' | 'NEUTRAL';
  slideNumber: number;
  startMs: Ms;
  endMs: Ms;
  severity: number;
  title: string;
  message: string;
  evidence: { label: string; value: string }[];
}

export interface TakeReport {
  takeId: string;
  takeNumber: number;
  mode: Mode;
  scriptMode: ScriptMode;
  headline: string;
  summary: {
    durationMs: Ms;
    timeLimitMs: Ms;
    /** 음수가 여유 시간입니다. -2000 = 2초 남김 */
    overTimeMs: Ms;
    averageWpm: Metric;
    fillerCount: Metric;
    /** 대본 의존도의 새 주근거 — STT 전사와 대본 원문의 유사도 */
    scriptSimilarity: Metric;
    scriptDependencyCount: Metric;
    overallConfidence: number;
  };
  gaze: ReportGaze;
  findings: Finding[];
  missions: {
    id: string;
    priority: number;
    slideNumber: number;
    description: string;
    result: 'DONE' | 'PARTIAL' | 'MISSED' | null;
  }[];
  /** MVP에서는 항상 false. 영상은 서버로 가지 않습니다 */
  hasRecording: boolean;
}

/* ------------------------------------------------------------------ */
/* 홈 · 비교                                                            */
/* ------------------------------------------------------------------ */

export interface HomeResponse {
  nearestPitch: { title: string; daysUntil: number } | null;
  nextMission: {
    pitchId: string;
    nextTakeNumber: number;
    missions: { id: string; priority: number; description: string }[];
  } | null;
  weeklyTakeCount: number;
  weeklyDelta: number;
  pitches: {
    id: string;
    title: string;
    timeLimitSec: number;
    daysUntil: number;
    presentationVersion: number;
    scriptVersion: number;
    bestTakeId: string | null;
    takes: { id: string; takeNumber: number; status: TakeStatus; isBest: boolean }[];
  }[];
  inProgressTake: { takeId: string; pitchTitle: string; startedAt: string } | null;
}

export interface ComparisonResponse {
  bestTakeId: string | null;
  /** 행들의 engineVersion이 하나라도 다르면 false — 추세선을 그리지 않습니다 */
  gazeComparable: boolean;
  recommendedTakeId: string | null;
  recommendReasons: string[];
  rows: {
    takeId: string;
    takeNumber: number;
    date: string;
    durationMs: Ms;
    overTimeMs: Ms;
    averageWpm: number;
    fillerCount: number;
    cameraMs: Ms | null;
    bottomMs: Ms | null;
    measuredMs: Ms | null;
    scriptSimilarity: number;
    gazeEngineVersion: string;
    gazeReliability: 'HIGH' | 'MEDIUM' | 'LOW' | null;
    scriptDependencyCount: number;
    scriptMode: ScriptMode;
    isBest: boolean;
  }[];
  missionResults: {
    takeNumber: number;
    missions: { id: string; description: string; result: 'DONE' | 'PARTIAL' | 'MISSED' }[];
  }[];
}

export interface AnalysisStatus {
  status: TakeStatus;
  steps: {
    name: 'UPLOAD' | 'STT' | 'CONTENT' | 'REPORT';
    status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED';
  }[];
  estimatedRemainSec: number;
}

/* ------------------------------------------------------------------ */
/* 리허설 준비 (P4) · 장치 점검                                         */
/*                                                                     */
/* ★ 명세 8-4에 아직 없는 화면입니다. 시안 기준의 잠정 형태이고,        */
/*   명세가 나오면 다른 타입과 같이 여기서부터 고칩니다.                */
/* ------------------------------------------------------------------ */

/** 사용자가 직접 적은 평가 기준. 준비 화면에서는 읽기 전용입니다 */
export interface EvalCriterion {
  id: string;
  order: number;
  text: string;
}

export interface PrepareResponse {
  pitchId: string;
  title: string;
  /** Take가 스냅샷하는 값 — 준비 화면에서 고정됩니다 (CLAUDE.md 8번) */
  presentationVersion: number;
  scriptVersion: number;
  timeLimitSec: number;
  /** 이번에 만들어질 Take 번호. POST /takes의 응답과 같아야 합니다 */
  nextTakeNumber: number;
  /** 지난 Take가 남긴 다음 과제. 없으면 null — 배너를 그리지 않습니다 */
  lastMission: { id: string; description: string } | null;
  criteria: { version: number; readOnly: boolean; items: EvalCriterion[] };
  /** 지난 Take에서 고른 Script Mode. 화면의 초기 선택값입니다 */
  defaultScriptMode: ScriptMode;
}

export interface CreateTakeRequest {
  pitchId: string;
  /** 멱등키. IndexedDB 세션 키와 **같은 값**입니다 */
  clientSessionId: string;
  mode: Mode;
  scriptMode: ScriptMode;
  presentationVersion: number;
  scriptVersion: number;
  criteriaVersion: number;
}

export interface CreateTakeResponse {
  takeId: string;
  takeNumber: number;
  status: TakeStatus;
}

/* ------------------------------------------------------------------ */
/* 리허설 화면 (P5 · P5x)                                               */
/*                                                                     */
/* ★ 명세 8-4에 아직 없습니다. 준비 화면과 같은 이유로 잠정 형태입니다.  */
/*   리허설은 URL에 takeId 하나만 들고 들어옵니다 — 새로고침으로 돌아와도 */
/*   화면이 서야 해서, 그 하나로 필요한 걸 다 받아올 곳이 필요합니다.    */
/* ------------------------------------------------------------------ */

export interface TakeContext {
  takeId: string;
  takeNumber: number;
  pitchId: string;
  pitchTitle: string;
  /** Take가 시작될 때 고정된 값입니다. 화면이 임의로 바꾸지 않습니다 */
  mode: Mode;
  scriptMode: ScriptMode;
  timeLimitSec: number;
  status: TakeStatus;
  /** 이번 Take의 과제. 없으면 null */
  mission: { id: string; description: string } | null;
}
