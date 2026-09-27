import type { Ms } from '@/types/api';

/**
 * 피치 생성 중의 초안 상태와 그 위에서 도는 규칙.
 *
 * 서버가 아직 없습니다 — `POST /pitches/add` 와 `POST /{id}/upload` 만 있고,
 * 목업이 요구하는 것(슬라이드·대본·평가기준이 **각자** 버전을 올린다)과 모양이 다릅니다.
 * 그래서 이 파일은 **화면이 무엇을 요구하는지를 타입으로 먼저 적은 것**이고,
 * 서버 계약이 정해지면 `types/api.ts` 로 올라갑니다.
 *
 * 계산을 컴포넌트에서 꺼내 둔 이유 — 진행 조건(`gate`)은 화면 다섯 장에서
 * 매번 다르게 보이는데, 눈으로 읽어서는 어느 조합이 빠졌는지 알 수 없습니다.
 */

/** 사이드바의 세 갈래. 본문에 무엇을 띄울지가 여기서 정해집니다 */
export type DraftNode = 'slides' | 'script' | 'criteria';

/**
 * 평가기준의 채점 방식. 목업의 `자동 채점` · `발화 대조` 두 가지입니다.
 *
 * ★ 이 값은 아직 어디에도 정의돼 있지 않습니다 — 목업에만 있습니다.
 *   `types/api.ts` 의 `EvalCriterion` 은 `{ id, order, text }` 뿐이라
 *   필드가 하나 빠져 있습니다. 서버와 합의되면 그쪽으로 옮깁니다.
 */
export type ScoringMode = 'AUTO' | 'TRANSCRIPT';

export const SCORING_LABEL: Record<ScoringMode, string> = {
  AUTO: '자동 채점',
  TRANSCRIPT: '발화 대조',
};

/** 목업 하단 — "기준은 최대 5개까지. 적을수록 피드백이 선명해져요." */
export const MAX_CRITERIA = 5;

/** 업로드 제한. 목업의 "최대 40MB" */
export const MAX_SLIDE_BYTES = 40 * 1024 * 1024;

export interface SlideVersion {
  version: number;
  /** PDF 변환이 끝나야 정해집니다. 변환 전에는 null */
  pageCount: number | null;
}

export interface ScriptVersion {
  version: number;
  text: string;
  /** 매핑 실행 결과. 슬라이드 장수만큼의 블록입니다. 미실행이면 null */
  blocks: string[] | null;
}

export interface DraftCriterion {
  id: string;
  text: string;
  scoring: ScoringMode;
}

export interface CriteriaVersion {
  version: number;
  items: DraftCriterion[];
}

export interface PitchDraft {
  title: string;
  /** ISO yyyy-mm-dd. 안 정했으면 빈 문자열 */
  presentationDate: string;
  timeLimitSec: number;
  slides: SlideVersion[];
  scripts: ScriptVersion[];
  criteria: CriteriaVersion[];
}

/** 각 갈래의 **최신** 버전. 사이드바가 굵게 보여 주는 그것입니다 */
export const latestSlides = (d: PitchDraft): SlideVersion | null => d.slides.at(-1) ?? null;
export const latestScript = (d: PitchDraft): ScriptVersion | null => d.scripts.at(-1) ?? null;
export const latestCriteria = (d: PitchDraft): CriteriaVersion | null => d.criteria.at(-1) ?? null;

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
 */
export function resolveChosen(draft: PitchDraft, chosen: Chosen): Resolved {
  return {
    slides: draft.slides.find((v) => v.version === chosen.slides) ?? latestSlides(draft),
    script: draft.scripts.find((v) => v.version === chosen.script) ?? latestScript(draft),
    criteria: draft.criteria.find((v) => v.version === chosen.criteria) ?? latestCriteria(draft),
  };
}

/* ------------------------------------------------------------------ */
/* 대본 분량 → 예상 시간                                                */
/* ------------------------------------------------------------------ */

/**
 * 한국어 발표의 분당 글자 수.
 *
 * ★ 잠정값입니다. 목업의 "1,284자 · 예상 04:52" 에서 역산했습니다
 *   (1,284 ÷ 292초 × 60 ≈ 264). 서버가 `PitchDetail.script.estBasisWpm` 을
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
