import type { MessageBasis } from '@/types/chat';
import type { PitchResources } from '@/types/pitch';

/**
 * 근거 표시 · 최신 아님 (`docs/ai/features/chat-agent.md` 12-4 F5 · 6-8).
 *
 * - 배지: "Take 5 · 대본 V2 · 기준 v4 기준". 대본 버전 이름은 피치 만들기 화면과 같게(그 피치의 버전 목록에서 번호를
 *   찾아 "V2"), 기준 번호는 BE 가 준 값 그대로입니다. 버전 이름을 찾지 못하면 그 칸은 빼고 ID 를 보이지 않습니다.
 * - 최신 아님: 사유마다 6-8 표의 문구. 표의 예("Take 6", "V3")처럼 화면이 모르는 값은 빼고 씁니다.
 *   모르는 사유는 "일부 기준이 바뀌었어요". 같은 문구는 한 번만 보입니다
 */

/** 한 피치의 배지 글 — 읽은 Take · 대본 버전 · 기준 버전. 보일 것이 없으면 null */
export function basisBadge(
  pitch: MessageBasis['pitches'][number],
  resources: PitchResources | undefined,
): string | null {
  const takes = [...new Set(pitch.takes.map((t) => t.take_number))].sort((a, b) => a - b);
  const script = resources?.script_versions.find((v) => v.id === pitch.script_version_id);
  const parts = [
    takes.length > 0 ? `Take ${takes.join(', ')}` : null,
    script ? `대본 V${script.version}` : null,
    pitch.standard_version !== null ? `기준 v${pitch.standard_version}` : null,
  ].filter((p): p is string => p !== null);
  return parts.length > 0 ? `${parts.join(' · ')} 기준` : null;
}

/** 근거 표시를 읽습니다 — 모양이 다르면(예전 BE 등) 피치가 없는 것으로 */
export function basisPitches(basis: unknown): MessageBasis['pitches'] {
  const pitches = (basis as { pitches?: unknown } | null)?.pitches;
  if (!Array.isArray(pitches)) return [];
  return pitches
    .filter(
      (p): p is MessageBasis['pitches'][number] =>
        typeof p === 'object' &&
        p !== null &&
        typeof (p as { pitch_id?: unknown }).pitch_id === 'string' &&
        Array.isArray((p as { takes?: unknown }).takes),
    )
    .map((p) => ({
      ...p,
      script_version_id: p.script_version_id ?? null,
      presentation_version_id: p.presentation_version_id ?? null,
      standard_version: Number.isInteger(p.standard_version) ? p.standard_version : null,
      takes: p.takes.filter((t) => Number.isInteger(t?.take_number)),
    }));
}

const STALE_TEXT: Record<string, string> = {
  NEW_TAKE: '이 답 뒤에 새 Take가 있어요',
  RESULT_REVISED: '이 Take의 결과가 다시 계산됐어요',
  SCRIPT_VERSION_CHANGED: '대본이 바뀌었어요',
  CRITERIA_VERSION_CHANGED: '평가 기준이 바뀌었어요',
  GOAL_CHANGED: '대화 목표가 바뀌었어요',
  SOURCE_DELETED: '근거가 된 기록이 지워졌어요',
  PRESENTATION_VERSION_CHANGED: '자료 또는 판정 규칙이 바뀌어 현재 기준과 다를 수 있어요',
  RULE_VERSION_CHANGED: '자료 또는 판정 규칙이 바뀌어 현재 기준과 다를 수 있어요',
  RESULT_UNAVAILABLE: '자료 또는 판정 규칙이 바뀌어 현재 기준과 다를 수 있어요',
  VERSION_UNKNOWN: '자료 또는 판정 규칙이 바뀌어 현재 기준과 다를 수 있어요',
  CORPUS_UPDATED: '발표법 자료가 갱신됐어요',
};

export const UNKNOWN_STALE_TEXT = '일부 기준이 바뀌었어요';

/** 최신 아님 사유 → 문구, 같은 문구는 한 번 (L3-27) */
export function staleTexts(reasons: readonly unknown[] | null | undefined): string[] {
  const texts = (reasons ?? []).map((r) =>
    typeof r === 'string' && Object.hasOwn(STALE_TEXT, r) ? STALE_TEXT[r]! : UNKNOWN_STALE_TEXT,
  );
  return [...new Set(texts)];
}
