import type { ScreenBody } from '@/shared/lib/chatScreen';
import type { useCreateStore } from './createStore';
import {
  countChars,
  infoOf,
  shownVersion,
  type DraftNode,
  type PaneNode,
  type ScriptVersion,
  type SlideVersion,
} from './lib/draft';

/**
 * 지금 구역의 서버 버전 ID — 슬라이드는 `presentation_versions.id`, 대본은 올린 사본의 id.
 * 평가 기준은 FE 가 서버 id 를 들고 있지 않고, 발표정보는 버전이 없어 null 입니다
 */
function serverVersionId(
  step: PaneNode,
  slides: SlideVersion | null,
  script: ScriptVersion | null,
): string | null {
  switch (step) {
    case 'slides':
      return slides?.presentationVersionId ?? null;
    case 'script':
      return script?.remote?.id ?? null;
    default:
      return null;
  }
}

type CreateState = ReturnType<typeof useCreateStore.getState>;

/** 이 화면 상태가 읽는 것 — `createStore` 를 **읽기만** 합니다 (12-4 "기존 화면 수정을 줄이는 법") */
export type PitchScreenSource = Pick<
  CreateState,
  'draft' | 'node' | 'version' | 'viewedVersions' | 'infoEdits' | 'criteriaSourceEdits' | 'pitchId'
>;

/**
 * 피치 만들기 · 수정 화면의 상태 (12-4 F2 "화면 상태" 표, AI `PitchScreen`).
 *
 * - `step` 은 화면의 현재 구역(`PitchCreateScreen` 의 `active`)이고, 값은 **지금 보고 있는 버전** 기준입니다 —
 *   갈래마다 그 갈래를 보고 있으면 고른 버전, 아니면 마지막으로 본 버전(`viewedVersions`), 없으면 최신.
 * - 제목 · 시간은 고치는 중인 값(`infoEdits`)이 먼저, 평가 기준 원문은 그 버전의 `criteriaSourceEdits` 가 먼저입니다.
 * - 대본은 본문을 싣지 않고 글자 수만 — 화면에 보이는 글자 수와 같은 계산(`countChars`, 공백 뺌)입니다.
 * - `saved` 는 서버에 저장한 값. 한 번도 저장하지 않았으면(`pitchId` 없음) null 이고, 칸마다 FE 가 모르면 null 입니다.
 * - `changed` 는 저장값과 다른 칸(제목 · 시간 · 평가 기준 원문). 저장값을 모르는 칸은 넣지 않습니다
 */
export function pitchScreen(
  name: 'pitch_create' | 'pitch_edit',
  step: PaneNode,
  s: PitchScreenSource,
): Extract<ScreenBody, { name: 'pitch_create' | 'pitch_edit' }> {
  // 화면과 같은 규칙으로 고릅니다(`shownVersion`) — 고른 버전이 목록에 없으면 화면처럼 최신을 봅니다
  const viewed = (node: DraftNode) => (s.node === node ? s.version : s.viewedVersions[node]);
  const slides = shownVersion(s.draft.slides, viewed('slides'));
  const script = shownVersion(s.draft.scripts, viewed('script'));
  const criteria = shownVersion(s.draft.criteria, viewed('criteria'));

  const info = s.infoEdits ?? infoOf(s.draft);
  const criteriaText =
    s.criteriaSourceEdits[criteria?.version ?? 0] ?? criteria?.sourceText ?? null;
  const draft = {
    title: info.title.trim() === '' ? null : info.title,
    slide_count: slides?.pageCount ?? null,
    script_chars: script ? countChars(script.text) : null,
    time_limit_sec: Number.isInteger(info.timeLimitSec) ? info.timeLimitSec : null,
    criteria_text: criteriaText === '' ? null : criteriaText,
  };

  // 저장한 값 — 발표정보는 저장하면 `draft` 에 들어가고 고치는 중인 값은 `infoEdits` 에 따로 있습니다.
  // 대본은 고치면 서버 사본(`remote`)을 놓아 저장된 글을 들고 있지 않고, 평가 기준은 고친 뒤 저장 전이면 서버 원문을 모릅니다
  const saved =
    s.pitchId === null
      ? null
      : {
          title: s.draft.title === '' ? null : s.draft.title,
          time_limit_sec: Number.isInteger(s.draft.timeLimitSec) ? s.draft.timeLimitSec : null,
          criteria_text: criteria?.saved && !criteria.edited ? (criteria.sourceText ?? null) : null,
          script_chars: script?.remote ? countChars(script.text) : null,
        };

  const changed: ('title' | 'time_limit_sec' | 'criteria_text')[] = [];
  if (saved) {
    if (saved.title !== null && draft.title !== saved.title) changed.push('title');
    if (saved.time_limit_sec !== null && draft.time_limit_sec !== saved.time_limit_sec)
      changed.push('time_limit_sec');
    if (saved.criteria_text !== null && draft.criteria_text !== saved.criteria_text)
      changed.push('criteria_text');
  }

  const current = step === 'info' ? null : { slides, script, criteria }[step];
  return {
    name,
    step,
    version_label: current ? `V${current.version}` : null,
    version_id: serverVersionId(step, slides, script),
    saved,
    draft,
    changed,
  };
}
