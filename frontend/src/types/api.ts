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

/**
 * 연습 방식. 코치가 발표 중에 말을 거느냐입니다 - 대본 표시(ScriptMode)와는 별개입니다.
 *
 * 서버 필드 이름은 그대로 `mode` 입니다 (BE DTO·DB 계약). 그래서 요청·응답에서는
 * `mode: PracticeMode` 로 보입니다. 이름을 맞추려면 BE 와 같이 바꿔야 합니다.
 */
export type PracticeMode = 'COACHING' | 'EXAM';
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
  /**
   * 시선 기준(보정)을 잃었습니다 — 리허설 중 새로고침 등. 기준은 탭 메모리에만 있어서입니다.
   * 카메라 끊김처럼 **제외하지 않고** 그 뒤 기록만 멈춥니다 (`gazeExclusion.ts`).
   */
  | 'CALIBRATION_LOST'
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

/* ------------------------------------------------------------------ */
/* Pitch · 자료 · 대본                                                  */
/*                                                                     */
/* 슬라이드와 대본은 BE 가 나눠서 줍니다 — 발표자료는 `types/presentation.ts`  */
/* (PDF URL 하나, FE 가 pdf.js 로 장마다 그림), 대본은 `types/script.ts`      */
/* (슬라이드별 본문 · 키워드). 장마다 이미지를 받던 옛 명세(8-4)의             */
/* `PitchDetail` · `Slide` 는 BE 에 없어 걷어냈습니다.                         */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/* 시선 구간 요약 — 옛 `/complete` 본문의 `gaze` 모양                    */
/*                                                                     */
/* `/complete` 는 BE 가 만들지 않기로 했습니다(#134). 종료는 `types/take.ts` 의 */
/* `TakeEndRequest`, 기록은 발표 중 WS `metrics` 입니다. 아래 `GazePayload` 는 */
/* 지금 `/dev/media` 가 저장한 기록을 접어 보여 주는 데만 씁니다.              */
/* ------------------------------------------------------------------ */

export interface GazeSegment {
  startMs: Ms;
  endMs: Ms;
  zone: GazeZone;
  confidence: number;
}

export interface CalibrationSummary {
  /**
   * 기준점 수. AI v1.1 은 3점입니다 — 화면 가운데 · 렌즈 · 대본 자리.
   * (v1.0 은 렌즈 · 대본 2점이었습니다)
   */
  points: 3;
  /**
   * GOOD 검사 통과 · FAIR 통과했지만 경고 · POOR 검사 불합격이지만 모델은 있어 진행함.
   * POOR 인 Take 의 시선 숫자는 믿음이 낮습니다 — 리포트가 그 점을 알려야 합니다.
   */
  quality: 'GOOD' | 'FAIR' | 'POOR';
  /**
   * 두 기준이 얼마나 떨어져 있나 (AI `separability`). **모르면 null 입니다** —
   * 더미 분류기처럼 수치를 내지 않는 경우입니다.
   * 0 으로 채우면 "분리도가 0" 이라는 뜻이 되어 리포트가 거짓말을 합니다.
   */
  separability: number | null;
  /** 보정 표본을 하나씩 빼고 맞힌 비율 0–1 (AI `loo_accuracy`). 모르면 null */
  looAccuracy: number | null;
  /** 합격이어도 붙는 경고 (AI `warnings`, 예: `SCREEN_MERGED: …`) */
  warnings: string[];
  /** 이 보정을 만든 엔진. Take 끼리 비교할 때 같은 엔진인지 봅니다 */
  engineVersion: string;
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

/* ------------------------------------------------------------------ */
/* 리포트 시선 — 새 리포트 응답은 types/report.ts                        */
/* ------------------------------------------------------------------ */

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

/* ------------------------------------------------------------------ */
/* 리허설 준비 (P4) · 장치 점검                                         */
/*                                                                     */
/* ★ 명세 8-4에 아직 없는 화면입니다. 시안 기준의 잠정 형태이고,        */
/*   명세가 나오면 다른 타입과 같이 여기서부터 고칩니다.                */
/* ------------------------------------------------------------------ */

/**
 * 장치 점검 준비 정보 — `GET /api/pitches/{pitch_id}/prepare` (BE #296 `PrepareDTO`). BE 그대로 snake_case 입니다.
 * 화면이 쓰는 모양으로는 `features/rehearsal/prepare/prepareInfo.ts` 에서 바꿉니다.
 *
 * 피치 생성을 거치지 않고(홈 · 새로고침) 들어와도 이번 연습의 버전을 알게 해 줍니다.
 */
export interface PrepareResponse {
  pitch_id: string;
  title: string;
  time_limit_sec: number;
  /**
   * 대본 장 수와 쪽수가 같은 가장 최근 발표자료. 맞는 것이 없으면 가장 최근 것이라 쪽수가 다를 수 있습니다.
   * 발표자료가 없으면 null — Take 를 만들 수 없습니다.
   * `page_count` 는 BE #298 부터 옵니다. 쪽수를 아직 세지 않은 옛 발표자료는 null 입니다.
   */
  presentation: { id: string; version: number; page_count?: number | null } | null;
  /**
   * 나누기가 끝난 가장 최근 대본. 없으면 null — Take 를 만들 수 없습니다.
   * `slide_count`(나눈 장 수)는 BE #298 부터 옵니다.
   */
  script: { id: string; version: number; slide_count?: number } | null;
  /** `script` 보다 새 대본이 아직 나누는 중(PENDING)이거나 실패(FAILED)했으면 그 대본. 대개 null */
  newer_script: {
    id: string;
    version: number;
    parse_status: 'PENDING' | 'FAILED';
    error_code: string | null;
  } | null;
  /** 지금 기준 다음 번호. 실제로 매긴 번호는 Take 생성 응답(`take_number`)이 맞습니다 */
  next_take_number: number;
  /** 가장 최근 Take 의 대본 표시 방식. Take 가 없으면 HIGHLIGHT */
  default_script_mode: ScriptMode;
  /**
   * 이번 Take 미션 (BE #409) — 리뷰가 끝난 가장 최근 Take 가 정한 다음 미션.
   * 리뷰가 끝난 Take 가 없으면(첫 Take) null, 리뷰는 끝났지만 다음 미션이 없으면 `items: []`.
   * `source_take_number` 가 `next_take_number - 1` 보다 작을 수 있습니다 — 뒤 Take 가 아직 분석 중이거나
   * 리뷰에 실패했을 때인데, 어느 쪽인지는 이 응답으로 알 수 없습니다.
   */
  missions: {
    source_take_number: number;
    items: {
      mission_id: string;
      priority: number;
      /** 보통 GAZE · SPEED · VOLUME · PAUSE · FILLER · CONTENT · TIME. BE 가 문자열로 내려 모르는 값이 올 수 있습니다 */
      area: string;
      slide_number: number | null;
      description: string;
      target: { metric: string; operator: string; value: number };
      /** 리뷰한 Take 기준 문장("이번 Take 7번")이라 다음 Take 화면에서는 보이지 않습니다 */
      reason: string | null;
    }[];
  } | null;
}

/**
 * 리허설 화면이 무대를 여는 데 필요한 것. 장치 점검이 Take 를 만들며 묶어 넘기고,
 * 새로고침에 대비해 IndexedDB 세션에도 남깁니다 (`setSessionTicket`).
 *
 * BE 에는 Take 를 읽는 API 가 없어서(`GET /takes/{id}` 없음) 리허설은 이 값으로
 * 발표자료(`GET /presentations/{id}`)와 대본(`GET /scripts/{id}`)을 받습니다.
 */
export interface RehearsalTicket {
  pitchId: string;
  title: string;
  presentationVersionId: string;
  scriptVersionId: string;
  timeLimitSec: number;
  mode: PracticeMode;
  scriptMode: ScriptMode;
  /** 몇 번째 Take 인가. Take 생성 응답(`take_number`)이 실제로 매긴 번호입니다 */
  takeNumber: number | null;
}

/**
 * 피치 생성에서 매핑을 저장하며 정한 **이번 연습의 조합**. "다음 →" 이 장치 점검에
 * `location.state.practice` 로 넘깁니다. 장치 점검은 이 값으로 Take 를 만들고,
 * 없으면(홈에서 바로 들어옴 · 새로고침) `/prepare` 의 버전을 씁니다.
 *
 * 두 화면이 서로 다른 feature 라 직접 import 하지 않고 이 타입만 함께 씁니다.
 */
export interface PracticeCombo {
  /** 장치 점검 머리말. `/prepare` 없이 들어와도 무슨 발표인지 보이게 */
  title: string;
  presentationVersionId: string;
  scriptVersionId: string;
  /** 화면에 보여 줄 번호 — "자료 v1 · 대본 v3" */
  slideVersion: number;
  scriptVersion: number;
  goalTimeSec: number;
}
