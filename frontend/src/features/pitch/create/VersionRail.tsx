import { selectInfoUnsaved, useCreateStore } from './createStore';
import {
  latestUploadedSlides,
  pendingVersion,
  resolveChosen,
  STEPS,
  toPracticeCombo,
  type DraftNode,
  type PitchDraft,
} from './lib/draft';

/**
 * 좌측 "발표 자료". 단계 표시줄과 같은 순서로 단계 이름이 있고, 그 갈래를 저장하면 아래로
 * 버전 목록과 "+ 새 버전"이 펼쳐집니다 (시안 `pitch-create`). 저장 전에는 단계 이름만 보입니다.
 *
 * ★ 슬라이드 · 대본 · 평가기준이 **각자** 버전을 올립니다 (슬라이드 V1 · 대본 V3 · 평가기준 V2).
 *   대본만 세 번 고치는 일이 정상 흐름이고, 그때 슬라이드를 다시 올리게 하면 안 됩니다.
 *   저장한 버전은 고치지 않으니 고치는 길이 "+ 새 버전"이고, 그 버튼은 여기에만 둡니다.
 *
 * 여기서는 **보기만** 고릅니다. 연습할 조합은 대본 매핑을 저장할 때 정해집니다 —
 * 지난 버전을 열어 보다가 실수로 그것으로 연습이 시작되지 않게. 그 조합에 "연습 중"을 붙입니다.
 */

interface RailGroup {
  versions: number[];
  /** 본문에 띄울 기본 버전. 없으면 null */
  latest: number | null;
  /** 그 갈래를 한 번이라도 저장했나. 저장해야 버전 목록이 펼쳐집니다 */
  saved: boolean;
  /** 연습에 들고 갈 버전 — "연습 중" */
  practicing: number | null;
  /** "+ 새 버전" — 고르는 게 아니라 **새 버전을 만듭니다** */
  onAdd: () => void;
  /** 저장 안 한 버전. 있으면 "+ 새 버전"을 막고 이 버전을 저장하라고 안내합니다 (`pendingVersion`) */
  pending: number | null;
  /** 막았을 때 무엇을 하면 되는지. 갈래마다 '저장'의 뜻이 달라 따로 받습니다 */
  pendingNote: string | null;
}

/** 지금 본문에 띄운 줄. 왼쪽 강조선 + 옅은 배경 */
const rowTone = (active: boolean) =>
  active ? 'border-coral bg-coral-wash font-bold' : 'border-transparent hover:bg-panel';

/** 막힌 "+ 새 버전" 아래 안내. 버전이 저장돼 있는지에 따라 할 일이 다릅니다 */
function pendingNote(draft: PitchDraft, node: DraftNode): string | null {
  const pending = pendingVersion(draft, node);
  if (pending === null) return null;
  if (node === 'criteria') {
    // 평가기준은 정리하면 서버에 저장됩니다(#463). 막히는 건 고친 내용을 저장하지 않았거나 아직 정리하지 않았을 때입니다
    const v = draft.criteria.find((c) => c.version === pending);
    return v?.saved
      ? `V${pending}의 고친 내용을 저장하면 만들 수 있어요`
      : `V${pending}을 정리하면 만들 수 있어요`;
  }
  return `V${pending}을 저장하면 만들 수 있어요`;
}

function Versions({ node, group }: { node: DraftNode; group: RailGroup }) {
  const showing = useCreateStore((s) => s.node === node);
  const version = useCreateStore((s) => s.version);
  const select = useCreateStore((s) => s.select);

  return (
    <ul className="ml-4 mt-0.5 flex flex-col gap-0.5 border-l border-line-strong pl-2">
      {group.versions.map((v) => {
        const active = showing && (version ?? group.latest) === v;
        return (
          <li key={v}>
            <button
              type="button"
              onClick={() => select(node, v)}
              aria-current={active ? 'true' : undefined}
              className={`tabular flex w-full items-center justify-between rounded-r border-l-2 px-2 py-1 text-left text-sm ${rowTone(active)}`}
            >
              V{v}
              {v === group.practicing && <span className="text-xs text-stone">연습 중</span>}
              {v === group.pending && <span className="text-xs font-bold text-coral">저장 전</span>}
            </button>
          </li>
        );
      })}
      <li>
        <button
          type="button"
          onClick={group.onAdd}
          disabled={group.pending !== null}
          className="w-full px-2 py-1 text-left text-sm text-stone hover:text-ink disabled:cursor-not-allowed disabled:text-line-strong"
        >
          + 새 버전
        </button>
        {group.pendingNote !== null && (
          <p className="px-2 pb-1 text-xs text-stone">{group.pendingNote}</p>
        )}
      </li>
    </ul>
  );
}

export function VersionRail() {
  const draft = useCreateStore((s) => s.draft);
  const node = useCreateStore((s) => s.node);
  const chosen = useCreateStore((s) => s.chosen);
  const select = useCreateStore((s) => s.select);
  const infoUnsaved = useCreateStore(selectInfoUnsaved);
  // 새 작성은 파일 업로드까지 끝나야 다음 단계로 갑니다. 수정 화면은 기존 피치 단계를 그대로 엽니다.
  const canLeaveSlides = useCreateStore(
    (s) => s.pitchId !== null && (s.autoTitle === null || latestUploadedSlides(s.draft) !== null),
  );
  const addSlideVersion = useCreateStore((s) => s.addSlideVersion);
  const addScriptVersion = useCreateStore((s) => s.addScriptVersion);
  const addCriteriaVersion = useCreateStore((s) => s.addCriteriaVersion);

  const latestSlide = draft.slides.at(-1);
  const combo = toPracticeCombo(draft, chosen);

  const groups: Record<DraftNode, RailGroup> = {
    slides: {
      versions: draft.slides.map((v) => v.version),
      latest: latestSlide?.version ?? null,
      saved: latestUploadedSlides(draft) !== null,
      practicing: combo?.slideVersion ?? null,
      // 올릴 자리가 이미 열려 있으면(없음 · 파일 대기) 빈 버전을 또 만들지 않고 그 자리를 띄웁니다.
      // 파일을 받는 순간 `attachSlides` 가 버전을 채우거나 만듭니다
      onAdd: () =>
        latestSlide === undefined || latestSlide.pageCount === null
          ? select('slides')
          : addSlideVersion(),
      pending: pendingVersion(draft, 'slides'),
      pendingNote: pendingNote(draft, 'slides'),
    },
    script: {
      versions: draft.scripts.map((v) => v.version),
      latest: draft.scripts.at(-1)?.version ?? null,
      saved: draft.scripts.some((v) => v.saved),
      practicing: combo?.scriptVersion ?? null,
      // 직전 버전의 글을 물려받습니다 — V1 → V2 → V3 는 고쳐 쓴 흔적입니다
      onAdd: () => addScriptVersion(draft.scripts.at(-1)?.text ?? ''),
      pending: pendingVersion(draft, 'script'),
      pendingNote: pendingNote(draft, 'script'),
    },
    criteria: {
      versions: draft.criteria.map((v) => v.version),
      latest: draft.criteria.at(-1)?.version ?? null,
      saved: draft.criteria.some((v) => v.saved),
      // 평가기준은 장치 점검에 넘기지 않습니다 — 서버에 저장된 마지막 버전으로 채점합니다
      practicing: resolveChosen(draft, chosen).criteria?.version ?? null,
      onAdd: addCriteriaVersion,
      pending: pendingVersion(draft, 'criteria'),
      pendingNote: pendingNote(draft, 'criteria'),
    },
  };

  return (
    <nav aria-label="발표 자료">
      <p className="mb-2 border-b border-line pb-2 text-sm font-bold">발표 자료</p>
      <ul className="flex flex-col gap-1">
        {STEPS.map((s) => {
          const group = s.node === 'info' ? null : groups[s.node];
          const open = group?.saved === true;
          // 버전이 펼쳐져 있으면 강조는 버전 줄이 맡습니다
          const active = s.node === node && !open;
          return (
            <li key={s.node}>
              <button
                type="button"
                onClick={() => select(s.node)}
                disabled={!canLeaveSlides && s.node !== 'slides'}
                aria-current={active ? 'step' : undefined}
                className={`flex w-full items-center justify-between gap-2 border-l-2 px-3 py-2 text-left text-sm disabled:cursor-not-allowed disabled:text-stone ${rowTone(active)}`}
              >
                <span>{s.label}</span>
                {/* 고치다 다른 화면으로 왔을 때 — 저장하지 않은 값이 남아 있다는 것을 여기서 압니다 */}
                {s.node === 'info' && infoUnsaved && (
                  <span className="text-xs font-bold text-coral">저장 안 됨</span>
                )}
              </button>
              {group && open && s.node !== 'info' && <Versions node={s.node} group={group} />}
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
