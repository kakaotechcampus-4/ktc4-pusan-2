import { describe, expect, it } from 'vitest';
import { isScreenBody } from '@/shared/lib/chatScreen';
import { pitchScreen, type PitchScreenSource } from './chatScreen';
import type { CriteriaVersion, PitchDraft, ScriptVersion, SlideVersion } from './lib/draft';

const slide = (version: number, pageCount: number | null, id?: string): SlideVersion =>
  ({ version, pageCount, presentationVersionId: id }) as SlideVersion;
const script = (version: number, text: string, remoteId: string | null = null): ScriptVersion =>
  ({
    version,
    text,
    remote: remoteId ? { id: remoteId, version } : null,
    parse: { status: 'idle' },
    segmented: null,
  }) as ScriptVersion;
const criteria = (version: number, extra: Partial<CriteriaVersion> = {}): CriteriaVersion => ({
  version,
  items: [],
  saved: true,
  ...extra,
});

function source(over: Partial<PitchScreenSource> = {}, draft: Partial<PitchDraft> = {}) {
  return {
    draft: {
      title: '졸업 발표',
      presentationDate: '',
      timeLimitSec: 300,
      lowerToleranceSec: 0,
      upperToleranceSec: 0,
      slides: [slide(1, 10, 'pv-1'), slide(2, 12, 'pv-2')],
      scripts: [script(1, '첫 대본', 'sv-1'), script(2, '안녕 하세요\n반갑습니다', 'sv-2')],
      criteria: [criteria(1, { sourceText: '말 속도를 일정하게.' })],
      ...draft,
    },
    node: 'script',
    version: null,
    viewedVersions: { slides: null, script: null, criteria: null },
    infoEdits: null,
    criteriaSourceEdits: {},
    pitchId: 'p-1',
    ...over,
  } satisfies PitchScreenSource;
}

describe('피치 만들기 · 수정 화면 상태 (12-4 F2)', () => {
  it('계약 모양이고, 대본은 본문 없이 화면과 같은 글자 수(공백 뺌)만 싣는다', () => {
    const screen = pitchScreen('pitch_edit', 'script', source());
    expect(isScreenBody(screen)).toBe(true);
    expect(screen).toEqual({
      name: 'pitch_edit',
      step: 'script',
      version_label: 'V2',
      version_id: 'sv-2',
      saved: {
        title: '졸업 발표',
        time_limit_sec: 300,
        criteria_text: '말 속도를 일정하게.',
        script_chars: 10,
      },
      draft: {
        title: '졸업 발표',
        slide_count: 12,
        script_chars: 10,
        time_limit_sec: 300,
        criteria_text: '말 속도를 일정하게.',
      },
      changed: [],
    });
    expect(JSON.stringify(screen)).not.toContain('반갑습니다');
  });

  it('값은 지금 보고 있는 버전 기준 — 그 갈래를 보면 고른 버전, 아니면 마지막으로 본 버전', () => {
    const s = source({
      node: 'slides',
      version: 1,
      viewedVersions: { slides: null, script: 1, criteria: null },
    });
    const screen = pitchScreen('pitch_edit', 'slides', s);
    expect(screen).toMatchObject({ version_label: 'V1', version_id: 'pv-1' });
    expect(screen.draft).toMatchObject({ slide_count: 10, script_chars: 3 });
  });

  it('고른 버전이 목록에 없으면 화면처럼 최신 기준 (shownVersion)', () => {
    const s = source({ node: 'slides', version: 9 });
    expect(pitchScreen('pitch_edit', 'slides', s)).toMatchObject({
      version_label: 'V2',
      version_id: 'pv-2',
      draft: { slide_count: 12 },
    });
  });

  it('고치는 중인 제목 · 시간과 평가 기준 원문이 먼저이고, 저장값과 다른 칸을 changed 에 적는다', () => {
    const s = source({
      infoEdits: {
        title: '최종 발표',
        presentationDate: '',
        timeLimitSec: 420,
        lowerToleranceSec: 0,
        upperToleranceSec: 0,
      },
      criteriaSourceEdits: { 1: '청중을 보며 말하기.' },
    });
    const screen = pitchScreen('pitch_edit', 'info', s);
    expect(screen).toMatchObject({
      version_label: null,
      version_id: null,
      draft: { title: '최종 발표', time_limit_sec: 420, criteria_text: '청중을 보며 말하기.' },
      changed: ['title', 'time_limit_sec', 'criteria_text'],
    });
  });

  it('한 번도 저장하지 않았으면 saved 는 null, changed 는 비어 있다', () => {
    const s = source({ pitchId: null }, { title: '', slides: [], scripts: [], criteria: [] });
    const screen = pitchScreen('pitch_create', 'slides', s);
    expect(isScreenBody(screen)).toBe(true);
    expect(screen).toMatchObject({
      name: 'pitch_create',
      version_label: null,
      saved: null,
      draft: { title: null, slide_count: null, script_chars: null, criteria_text: null },
      changed: [],
    });
  });

  it('FE 가 모르는 저장값은 null — 고친 대본 · 저장 전에 고친 평가 기준', () => {
    const s = source(
      {},
      {
        scripts: [script(1, '고친 대본')],
        criteria: [criteria(1, { sourceText: '원문', edited: true })],
      },
    );
    const screen = pitchScreen('pitch_edit', 'criteria', s);
    expect(screen).toMatchObject({
      saved: { script_chars: null, criteria_text: null },
      version_id: null,
    });
    // 저장값을 모르는 칸은 바뀌었다고 하지 않습니다
    expect(screen.changed).not.toContain('criteria_text');
  });

  it('제목은 앞뒤 공백을 지운 값으로 저장값과 견준다 — 공백만 다르면 바뀌지 않았다', () => {
    const edits = (title: string) => ({
      infoEdits: {
        title,
        presentationDate: '',
        timeLimitSec: 300,
        lowerToleranceSec: 0,
        upperToleranceSec: 0,
      },
    });
    expect(pitchScreen('pitch_edit', 'info', source(edits('졸업 발표 '))).changed).toEqual([]);
    expect(pitchScreen('pitch_edit', 'info', source(edits('졸업 발표2'))).changed).toEqual([
      'title',
    ]);
  });

  it('평가 기준 버전이 없으면 key 0 에 적어 둔 원문을 싣는다', () => {
    const s = source({ criteriaSourceEdits: { 0: '첫 원문' } }, { criteria: [] });
    expect(pitchScreen('pitch_create', 'criteria', s).draft.criteria_text).toBe('첫 원문');
  });
});
