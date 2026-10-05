import type { PitchRequest, PitchSavedResponse } from '@/types/pitch';
import type { UploadPresentationResponse } from '@/types/presentation';
import type { ScriptCreated, ScriptDetail } from '@/types/script';
import type { StandardsPosted } from '@/types/standards';
import type { InfoForm } from './draft';

/**
 * BE 응답 ↔ 피치 생성 화면. **옮기는 규칙은 이 파일에만 둡니다.**
 *
 * ── 왜 따로 있나 ────────────────────────────────────────────────────
 *
 * `types/pitch.ts` · `types/script.ts` · `types/standards.ts` · `types/presentation.ts` 는
 * BE DTO 를 필드 이름 그대로(snake_case) 옮긴 것이고, 화면은 `lib/draft.ts` 의 모양을 씁니다.
 * 그 사이를 화면 곳곳에서 바꾸면 BE 가 응답을 고쳤을 때 고칠 곳을 찾아다녀야 합니다.
 *
 * 여기 모아 두면 BE 가 바뀌었을 때 고칠 곳이 한 군데이고, 서버 없이 테스트로
 * 고정할 수 있습니다 (beAdapter.test.ts — 픽스처는 BE 응답 모양 그대로입니다).
 * 시선 쪽 `workers/aiAdapter.ts` 와 같은 자리입니다.
 */

/* ------------------------------------------------------------------ */
/* 피치 — POST /pitches/add · PUT /pitches/update/{id}                   */
/* ------------------------------------------------------------------ */

/** 발표정보 화면 값 → `PitchDTO`. 날짜를 안 골랐으면 null 입니다 (BE `date | None`) */
export function toPitchRequest(form: InfoForm): PitchRequest {
  return {
    title: form.title.trim(),
    time_limit_sec: form.timeLimitSec,
    upper_deviation: form.upperToleranceSec,
    lower_deviation: form.lowerToleranceSec,
    presentation_date: form.presentationDate || null,
  };
}

/** 생성 · 수정 모두 `{"message", "pitch_id"}` 를 돌려줍니다 */
export const fromPitchSaved = (res: PitchSavedResponse): string => res.pitch_id;

/* ------------------------------------------------------------------ */
/* 발표자료 — POST /pitches/add/{id}/presentation                       */
/* ------------------------------------------------------------------ */

export interface UploadedFile {
  fileUrl: string;
  presentationVersionId: string;
}

/** 컨트롤러가 `{"message", "presentation": {...}}` 로 감싸 돌려줍니다 */
export function fromUploadedPresentation(res: UploadPresentationResponse): UploadedFile {
  return {
    fileUrl: res.presentation.file_url,
    presentationVersionId: res.presentation.presentation_version_id,
  };
}

/* ------------------------------------------------------------------ */
/* 평가기준 — POST /pitches/add/{id}/standards                          */
/* ------------------------------------------------------------------ */

export interface ParsedStandards {
  standards: string[];
  /** 서버가 기준으로 넣지 못한 부분. 다 들어갔으면 null */
  exceptText: string | null;
}

/**
 * 나눈 결과. BE 가 아직 나누지 못해 결과 자리가 비어 오면 null 입니다.
 *
 * ★ 컨트롤러가 결과(`StandardTextResponseDTO`)를 `pitch_id` 키 **안에** 감싸 돌려줍니다.
 *   BE 가 감싸기를 풀면 여기 한 줄만 바꾸면 됩니다.
 */
export function fromStandardsPosted(res: StandardsPosted): ParsedStandards | null {
  const result = res.pitch_id;
  if (!result) return null;
  return {
    standards: result.standards.map((s) => s.standard),
    exceptText: result.except_standard ?? null,
  };
}

/* ------------------------------------------------------------------ */
/* 대본 — POST /pitches/{id}/scripts → GET …/scripts/{id} (폴링)          */
/* ------------------------------------------------------------------ */

/** 202 응답의 서버 버전. 이 id 로 폴링하고, 다시 나누기도 이 id 로 요청합니다 */
export function fromScriptCreated(res: ScriptCreated): { id: string; version: number } {
  return { id: res.script_version_id, version: res.version };
}

export type ScriptProgress =
  | { status: 'pending' }
  | {
      status: 'done';
      /** 슬라이드별 본문. slide_number 순서입니다 */
      blocks: string[];
      /** false 는 구분자가 없어 대본 전체를 한 슬라이드로 둔 **정상** 결과 */
      segmented: boolean | null;
    }
  | { status: 'failed'; errorCode: string | null };

/**
 * 폴링 응답 → 화면이 할 일.
 *
 * `slides` 는 순서가 보장되지 않는다고 보고 `slide_number` 로 정렬합니다.
 * 본문은 AI 가 구분자 · 소제목을 뺀 `content` 를 씁니다 — `original_content` 는 원문입니다.
 */
export function fromScriptDetail(detail: ScriptDetail): ScriptProgress {
  if (detail.parse_status === 'PENDING') return { status: 'pending' };
  if (detail.parse_status === 'FAILED') return { status: 'failed', errorCode: detail.error_code };
  return {
    status: 'done',
    blocks: [...detail.slides]
      .sort((a, b) => a.slide_number - b.slide_number)
      .map((slide) => slide.content),
    segmented: detail.segmented,
  };
}
