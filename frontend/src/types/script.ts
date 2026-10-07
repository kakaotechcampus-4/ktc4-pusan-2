/**
 * 대본 — `POST /api/pitches/{pitch_id}/scripts` 로 올리고, 슬라이드 분리(AI)는 BE 가
 * 백그라운드에서 합니다. FE 는 `GET …/scripts/{script_version_id}` 를 폴링합니다.
 * BE 그대로 snake_case 입니다 (`ScriptCreatedDTO` · `ScriptDetailDTO`).
 */

export type ScriptParseStatus = 'PENDING' | 'DONE' | 'FAILED';

export interface ScriptCreateRequest {
  /** textarea 내용 그대로. BE 가 빈 값 · 50,000자 초과 · NUL 을 400 INVALID_SCRIPT 로 막습니다 */
  content: string;
}

/** 202 응답. 원문 저장까지만 끝난 상태입니다 */
export interface ScriptCreated {
  script_version_id: string;
  version: number;
  parse_status: ScriptParseStatus;
}

export interface ScriptSlide {
  slide_number: number;
  /** AI 가 구분자 · 소제목을 뺀 본문 */
  content: string;
  keywords: string[];
  /** content 안의 문자 위치. end 는 포함하지 않습니다 (content[start:end]) */
  highlights: { start: number; end: number }[];
}

/** 폴링 응답. 상태와 상관없이 같은 키로 옵니다 */
export interface ScriptDetail {
  script_version_id: string;
  version: number;
  original_content: string | null;
  parse_status: ScriptParseStatus;
  /** DONE 일 때만 값이 있습니다. false 는 구분자가 없어 대본 전체를 한 슬라이드로 둔 **정상** 결과 */
  segmented: boolean | null;
  slides: ScriptSlide[];
  terms: string[];
  /** FAILED 일 때의 원인. FE 는 코드마다 다르게 하지 않고 "다시 시도" 하나로 받습니다 */
  error_code: string | null;
}

/** `POST …/scripts/{id}/parse` — FAILED 인 대본을 다시 나눕니다 */
export interface ParseRequested {
  script_version_id: string;
  parse_status: ScriptParseStatus;
}
