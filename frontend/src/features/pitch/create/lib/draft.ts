import type { Ms, PracticeCombo } from '@/types/api';

/**
 * 피치 생성 중의 초안 상태와 그 위에서 도는 규칙.
 *
 * 서버에는 피치(`POST /pitches/add`) 아래로 발표자료 · 대본 · 평가기준이 **각자** 버전을 올립니다.
 * 이 파일은 그 버전들을 화면이 들고 있는 모양입니다 — 서버가 준 id 는 각 버전 안에 둡니다.
 *
 * 계산을 컴포넌트에서 꺼내 둔 이유 — 진행 조건(`gate`)은 화면 다섯 장에서
 * 매번 다르게 보이는데, 눈으로 읽어서는 어느 조합이 빠졌는지 알 수 없습니다.
 */

/** 사이드바의 세 갈래. 각자 버전을 올립니다 */
export type DraftNode = 'slides' | 'script' | 'criteria';

/**
 * 본문에 띄울 수 있는 것. 세 갈래에 더해 맨 위의 **발표 정보**(제목 · 날짜)가 있습니다.
 * 발표 정보는 피치 한 판에 하나뿐이라 버전이 없습니다 — 그래서 `DraftNode` 와 나눕니다.
 */
export type PaneNode = 'info' | DraftNode;

/** 발표 제목 최대 글자 수. BE 의 `title: StringConstraints(min_length=1, max_length=50)` 과 같습니다 */
export const MAX_TITLE_CHARS = 50;

/** 발표시간 범위(분). 입력 칸이 아니라 −/+ 로만 바꿉니다 */
export const MIN_TIME_LIMIT_MIN = 1;
export const MAX_TIME_LIMIT_MIN = 60;

/**
 * 허용 오차(초). 목표보다 **짧게**(하한) · **길게**(상한) 발표해도 되는 시간을 따로 둡니다 —
 * 목업의 "−30초 / +1분". 서버의 `PitchSaveRequestDTO.lower_deviation` · `upper_deviation` 과 같은 모양입니다.
 */
export const TOLERANCE_STEP_SEC = 30;
export const MAX_TOLERANCE_SEC = 5 * 60;

/** "5분" · "30초" · "1분 30초". 0 이면 "0초" — 빈칸보다 "없음"이 분명합니다 */
export function formatDuration(sec: number): string {
  const minutes = Math.floor(sec / 60);
  const seconds = sec % 60;
  if (minutes === 0) return `${seconds}초`;
  if (seconds === 0) return `${minutes}분`;
  return `${minutes}분 ${seconds}초`;
}

/** 정보 줄의 "5분 (−30초 / +1분)". 오차가 둘 다 0 이면 괄호를 붙이지 않습니다 */
export function formatTimeLimit(limitSec: number, lowerSec: number, upperSec: number): string {
  const limit = formatDuration(limitSec);
  if (lowerSec === 0 && upperSec === 0) return limit;
  return `${limit} (−${formatDuration(lowerSec)} / +${formatDuration(upperSec)})`;
}

/** "4분 30초 ~ 6분". 하한이 목표보다 길면 0 에서 멈춥니다 */
export function formatAllowedRange(limitSec: number, lowerSec: number, upperSec: number): string {
  const from = Math.max(0, limitSec - lowerSec);
  return `${formatDuration(from)} ~ ${formatDuration(limitSec + upperSec)}`;
}

/** 목업 하단 — "기준은 최대 5개까지. 적을수록 피드백이 선명해져요." */
export const MAX_CRITERIA = 5;

/** 업로드 제한. 목업의 "최대 40MB" */
export const MAX_SLIDE_BYTES = 40 * 1024 * 1024;

export interface SlideVersion {
  version: number;
  /**
   * 업로드가 끝나고 받은 URL 로 PDF 를 열어야 정해집니다. 그 전(빈 버전 · 올리는 중)에는 null.
   * 서버는 장수를 주지 않습니다 — pdf.js 가 연 문서의 `numPages` 입니다.
   */
  pageCount: number | null;
  /**
   * 업로드 응답의 presigned URL. 뷰어가 이것으로 PDF 를 엽니다.
   * ★ 1시간 뒤 만료됩니다. 한 번 연 문서는 메모리에 있어 화면은 그대로지만,
   *   새로고침 뒤에 다시 열려면 조회 API 로 새 URL 을 받아야 합니다.
   */
  fileUrl?: string;
  /** 서버의 presentation_versions.id. Take 를 만들 때 이 버전을 가리킵니다 */
  presentationVersionId?: string;
  /** 뷰어 위에 띄우는 파일 이름. 서버 응답에는 없어 고른 파일에서 가져옵니다 */
  fileName?: string;
}

/** 업로드 결과로 버전에 채워 넣는 값 */
export type UploadedSlides = Required<
  Pick<SlideVersion, 'fileUrl' | 'presentationVersionId' | 'fileName'>
> & {
  pageCount: number;
};

/**
 * 슬라이드 업로드 진행 상태. 버전과 따로 둡니다 — 올리는 도중에는 아직 버전에 채울 것이 없고,
 * 실패하면 버전을 만들지 않은 채로 "다시 시도"할 파일만 들고 있어야 합니다.
 *
 * `upload` 는 서버에 못 올린 것, `open` 은 올렸지만 받은 URL 로 PDF 를 못 연 것입니다
 * (깨진 파일이거나, 실서버에서 S3 CORS 가 막혀 있거나).
 *
 * `replace` 는 "파일 교체"로 올리는 중인 버전입니다. null 이면 빈 자리에 새로 올리는 것입니다.
 */
export type SlideUpload =
  | { status: 'idle' }
  | { status: 'uploading'; file: File; replace: number | null }
  | { status: 'failed'; file: File; replace: number | null; reason: 'upload' | 'open' };

/**
 * 대본을 서버에 올려 슬라이드로 나누는 진행 상태. 흐름은 `scriptParse.ts` 가 몹니다.
 * `failed` 의 `message` 는 화면에 그대로 띄울 문구입니다.
 */
export type ScriptParse =
  { status: 'idle' } | { status: 'pending' } | { status: 'failed'; message: string };

export interface ScriptVersion {
  version: number;
  text: string;
  /**
   * 서버에 올린 버전 (`script_version_id` · 서버 버전 번호). 올리기 전이면 null.
   * 글을 고치면 서버의 것과 달라지므로 비웁니다 — 다음 매핑에서 새 버전으로 올라갑니다.
   *
   * ★ 서버 번호와 사이드바 번호(`version`)는 다를 수 있습니다. 사이드바는 화면에서 만든 초안까지
   *   세고, 서버는 올린 것만 셉니다. 사이드바를 `GET /resources` 로 채우게 되면 하나로 맞춥니다.
   */
  remote: { id: string; version: number } | null;
  parse: ScriptParse;
  /** 서버가 구분자로 나눴나. false 는 구분자가 없어 대본 전체를 한 슬라이드로 둔 정상 결과 */
  segmented: boolean | null;
  /**
   * 이 대본을 나눌 슬라이드 버전 — 목업의 "연결할 슬라이드 V1 · 12장".
   * 매핑은 장수에 맞춰 나누므로 어느 슬라이드에 맞춘 것인지를 대본이 들고 있어야 합니다.
   */
  slideVersion: number | null;
  /** 서버가 나눈 슬라이드별 본문 (`slides[].content`, slide_number 순). 나누기 전이면 null */
  blocks: string[] | null;
  /**
   * 매핑 확인 화면에서 "저장"을 눌렀나. 저장한 조합(슬라이드 + 대본)으로 연습합니다.
   * 글이나 연결한 슬라이드가 바뀌면 다시 false 가 됩니다.
   */
  saved: boolean;
}

export interface DraftCriterion {
  id: string;
  text: string;
}

export interface CriteriaVersion {
  version: number;
  items: DraftCriterion[];
  /**
   * 서버에 저장됐나. BE 는 나누면서 바로 저장하므로(`POST /standards`) 정리가 끝나면 true 입니다.
   * "+ 새 버전"으로 만든 빈 버전만 false 이고, 그런 버전은 연습에 쓰지 않습니다.
   * 저장한 버전은 고치지 않고, 다시 정리하면 새 버전이 됩니다.
   */
  saved: boolean;
  /**
   * 자연어로 적은 원문. 서버가 이것을 `items` 로 나눕니다.
   * 다시 열었을 때 무엇을 적어서 이 목록이 나왔는지 보여 주려고 함께 둡니다.
   * 손으로만 만든 버전에는 없습니다.
   */
  sourceText?: string;
  /** 서버가 기준으로 넣지 못한 부분(`except_standard`). 5개를 넘었거나 평가할 수 없는 문장 */
  exceptText?: string | null;
}

export interface PitchDraft {
  title: string;
  /** ISO yyyy-mm-dd. 안 정했으면 빈 문자열 */
  presentationDate: string;
  timeLimitSec: number;
  /** 목표보다 짧게 발표해도 되는 시간(초) */
  lowerToleranceSec: number;
  /** 목표보다 길게 발표해도 되는 시간(초) */
  upperToleranceSec: number;
  slides: SlideVersion[];
  scripts: ScriptVersion[];
  criteria: CriteriaVersion[];
}

/** 발표정보 화면이 고치는 값. 피치 한 판에 하나뿐이라 버전이 없습니다 */
export type InfoForm = Pick<
  PitchDraft,
  'title' | 'presentationDate' | 'timeLimitSec' | 'lowerToleranceSec' | 'upperToleranceSec'
>;

export const infoOf = (d: PitchDraft): InfoForm => ({
  title: d.title,
  presentationDate: d.presentationDate,
  timeLimitSec: d.timeLimitSec,
  lowerToleranceSec: d.lowerToleranceSec,
  upperToleranceSec: d.upperToleranceSec,
});

export const infoChanged = (a: InfoForm, b: InfoForm): boolean =>
  (Object.keys(a) as (keyof InfoForm)[]).some((k) => a[k] !== b[k]);

/** 각 갈래의 **최신** 버전. 사이드바가 굵게 보여 주는 그것입니다 */
export const latestSlides = (d: PitchDraft): SlideVersion | null => d.slides.at(-1) ?? null;
export const latestScript = (d: PitchDraft): ScriptVersion | null => d.scripts.at(-1) ?? null;
export const latestCriteria = (d: PitchDraft): CriteriaVersion | null => d.criteria.at(-1) ?? null;

/** 저장까지 끝난 마지막 평가기준. 정리만 해 둔 버전은 연습에 들고 가지 않습니다 */
export const latestSavedCriteria = (d: PitchDraft): CriteriaVersion | null =>
  d.criteria.findLast((v) => v.saved) ?? null;

/** 파일까지 올라간 마지막 슬라이드. 대본에 연결할 기본값입니다 */
export const latestUploadedSlides = (d: PitchDraft): SlideVersion | null =>
  d.slides.findLast((v) => v.pageCount !== null) ?? null;

/* ------------------------------------------------------------------ */
/* 연습에 들고 갈 버전 고르기                                            */
/* ------------------------------------------------------------------ */

/**
 * 이번 연습이 쓸 버전. **셋을 따로 고릅니다** — 슬라이드는 V2, 대본은 V1,
 * 평가기준은 V1 같은 조합이 정상입니다. 자료를 고쳤다고 대본까지 새것을 써야 할
 * 이유가 없고, 반대로 대본만 다듬어 보는 연습도 흔합니다.
 *
 * `null` 은 "최신" 입니다. 번호로 박아 두지 않는 이유 — 사용자가 고르지 않았는데
 * V1 로 고정해 두면, 뒤에 V2 를 올려도 계속 V1 로 연습하게 됩니다.
 *
 * 슬라이드와 대본은 매핑 확인 화면에서 "저장"할 때 함께 정해집니다 — 목업의
 * "저장 완료 · 슬라이드 V1 / 대본 V4 · 이 조합으로 연습을 진행해요".
 *
 * ★ 이 셋이 Take 가 **스냅샷하는 값**입니다 (CLAUDE.md 8번). 한 번 정해지면
 *   그 Take 의 리포트는 영원히 이 버전들을 근거로 읽힙니다.
 */
export interface Chosen {
  slides: number | null;
  script: number | null;
  criteria: number | null;
}

export const CHOOSE_LATEST: Chosen = { slides: null, script: null, criteria: null };

export interface Resolved {
  slides: SlideVersion | null;
  script: ScriptVersion | null;
  criteria: CriteriaVersion | null;
}

/**
 * 고른 번호를 실제 버전으로 바꿉니다.
 *
 * 고른 번호가 없어졌으면(지웠거나 아직 안 만들었으면) 최신으로 떨어집니다 —
 * 없는 버전을 가리킨 채로 연습을 시작하는 것보다 낫습니다.
 * 평가기준은 **서버에 저장된 것만** 봅니다. 빈 버전으로 채점하면 안 됩니다.
 */
export function resolveChosen(draft: PitchDraft, chosen: Chosen): Resolved {
  const savedCriteria = draft.criteria.filter((v) => v.saved);
  return {
    slides: draft.slides.find((v) => v.version === chosen.slides) ?? latestSlides(draft),
    script: draft.scripts.find((v) => v.version === chosen.script) ?? latestScript(draft),
    criteria:
      savedCriteria.find((v) => v.version === chosen.criteria) ?? latestSavedCriteria(draft),
  };
}

/**
 * 장치 점검에 넘길 이번 연습의 조합. BE 는 이 조합을 Take 에 박아 둡니다 (`TakeInitRequestDTO`).
 *
 * 서버 id 가 하나라도 없으면 null 입니다 — 올리지 않은 슬라이드나 서버에서 나누지 않은 대본으로는
 * Take 를 만들 수 없습니다. 매핑을 저장했다면 둘 다 있습니다 (업로드 응답 · 대본 파싱 응답).
 */
export function toPracticeCombo(draft: PitchDraft, chosen: Chosen): PracticeCombo | null {
  const { slides, script } = resolveChosen(draft, chosen);
  if (
    !slides?.presentationVersionId ||
    !script?.remote ||
    !script.saved ||
    script.parse.status !== 'idle' ||
    script.slideVersion !== slides.version ||
    !slides.pageCount ||
    script.blocks?.length !== slides.pageCount
  )
    return null;
  return {
    title: draft.title,
    presentationVersionId: slides.presentationVersionId,
    scriptVersionId: script.remote.id,
    slideVersion: slides.version,
    scriptVersion: script.version,
    goalTimeSec: draft.timeLimitSec,
  };
}

/* ------------------------------------------------------------------ */
/* 대본 분량 → 예상 시간                                                */
/* ------------------------------------------------------------------ */

/**
 * 한국어 발표의 분당 글자 수.
 *
 * ★ 잠정값입니다. 목업의 "1,284자 · 예상 04:52" 에서 역산했습니다
 *   (1,284 ÷ 292초 × 60 ≈ 264). 서버가 분당 글자 수 기준(`estBasisWpm`)을
 *   내려주므로, 그 값이 정해지면 **서버 값을 쓰고 이 상수는 지웁니다.**
 *   두 곳에서 다른 기준으로 계산하면 준비 화면과 생성 화면의 숫자가 어긋납니다.
 */
export const EST_CHARS_PER_MIN = 264;

/** 공백을 뺀 글자 수 — 목업의 "1,284자" 가 세는 방식입니다 */
export function countChars(text: string): number {
  return text.replace(/\s/g, '').length;
}

export function estimateDurationMs(text: string): Ms {
  return Math.round((countChars(text) / EST_CHARS_PER_MIN) * 60_000);
}

/** mm:ss. 시계(`shared/lib/clock`)와 같은 모양이지만 부호가 필요 없습니다 */
export function formatEstimate(ms: Ms): string {
  const total = Math.round(ms / 1000);
  const mm = String(Math.floor(total / 60)).padStart(2, '0');
  const ss = String(total % 60).padStart(2, '0');
  return `${mm}:${ss}`;
}
