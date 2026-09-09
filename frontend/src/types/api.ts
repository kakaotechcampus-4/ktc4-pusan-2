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
/** 4단계. OFF는 대본 영역 자체가 사라진다 (높이 0) */
export type ScriptMode = 'FULL' | 'HIGHLIGHT' | 'KEYWORD' | 'OFF';
export type TakeStatus = 'READY' | 'RUNNING' | 'ANALYZING' | 'COMPLETED' | 'FAILED';

/** AI팀 파이프라인의 최종 출력. 4분할이 아니라 3값입니다. */
export type GazeZone = 'CAMERA' | 'BOTTOM' | 'UNCERTAIN';

export type GazeExcludedReason =
  | 'LOW_CONFIDENCE'
  | 'ENGINE_UNAVAILABLE'
  | 'CAMERA_LOST'
  | 'USER_DECLINED'
  | 'VALIDATION_FAILED'
  | 'UNCERTAIN_RATIO_EXCEEDED';

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
  separability: number;
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
  audioFileKey: string;
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
