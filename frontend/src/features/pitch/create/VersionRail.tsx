import { useState } from 'react';
import { selectInfoUnsaved, useCreateStore } from './createStore';
import type { DraftNode } from './lib/draft';
import { CaretIcon, ChevronIcon, DocIcon, FolderIcon, PixelMark } from './icons';

/**
 * 좌측 "발표 자료" 트리. 발표정보 아래로 평가기준 · 슬라이드 · 대본 폴더가 있고, 각자 버전이 달립니다.
 *
 * ★ 셋이 **각자** 버전을 올립니다 (평가기준 V2 · 슬라이드 V1 · 대본 V3).
 *   이 화면이 마법사(다음→다음)가 아니라 한 판인 이유가 여기 있습니다 —
 *   대본만 세 번 고치는 일이 정상 흐름이고, 그때 슬라이드를 다시 올리게 하면 안 됩니다.
 *
 * 여기서는 **보기만** 고릅니다. 연습할 조합은 대본 매핑을 저장할 때 정해집니다 —
 * 지난 버전을 열어 보다가 실수로 그것으로 연습이 시작되지 않게.
 */

interface RailGroup {
  node: DraftNode;
  label: string;
  versions: number[];
  /** 본문에 띄울 기본 버전. 없으면 null */
  latest: number | null;
  /** "+ 새 버전" — 고르는 게 아니라 **새 버전을 만듭니다** */
  onAdd: () => void;
}

/** 지금 본문에 띄운 줄. 목업의 왼쪽 강조선 + 옅은 배경 */
const rowTone = (active: boolean) =>
  active
    ? 'border-l-2 border-coral bg-coral-wash font-bold'
    : 'border-l-2 border-transparent hover:bg-panel';

/** 트리의 가지 선. 폴더 아이콘 아래에서 내려와 각 줄로 꺾입니다 */
const branch =
  'relative pl-4 before:absolute before:left-0 before:top-0 before:h-1/2 before:w-3 before:border-b before:border-l before:border-line-strong';

function GroupBlock({ group }: { group: RailGroup }) {
  const node = useCreateStore((s) => s.node);
  const version = useCreateStore((s) => s.version);
  const select = useCreateStore((s) => s.select);
  const [open, setOpen] = useState(true);

  const showing = node === group.node;

  return (
    <li>
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-label={`${group.label} ${open ? '접기' : '펼치기'}`}
          className="flex h-6 w-4 shrink-0 items-center justify-center"
        >
          <CaretIcon open={open} />
        </button>

        {/* 폴더를 누르면 그 갈래의 최신(또는 빈 화면)을 본문에 띄웁니다 */}
        <button
          type="button"
          onClick={() => select(group.node)}
          className="flex min-w-0 flex-1 items-center gap-2 py-1.5 text-left text-sm font-bold"
        >
          <FolderIcon />
          <span>{group.label}</span>
          <span className="ml-auto">
            <ChevronIcon up={showing} />
          </span>
        </button>
      </div>

      {open && (
        <ul className="ml-[1.6rem] mt-1 flex flex-col gap-0.5">
          {group.versions.map((v) => {
            const active = showing && (version ?? group.latest) === v;
            return (
              <li key={v} className={branch}>
                <button
                  type="button"
                  onClick={() => select(group.node, v)}
                  aria-current={active ? 'true' : undefined}
                  className={`flex w-full items-center gap-2 rounded-r px-2 py-1.5 text-left text-sm ${rowTone(active)}`}
                >
                  <DocIcon className="h-4 w-4 shrink-0 stroke-ink" />V{v}
                </button>
              </li>
            );
          })}

          <li className={branch}>
            <button
              type="button"
              onClick={group.onAdd}
              className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-sm text-stone hover:text-ink"
            >
              <span aria-hidden="true" className="w-4 text-center text-base leading-none">
                +
              </span>
              새 버전
            </button>
          </li>
        </ul>
      )}
    </li>
  );
}

/** 맨 위 "발표정보". 버전이 없어 접을 것도 고를 것도 없습니다 */
function InfoItem() {
  const node = useCreateStore((s) => s.node);
  const select = useCreateStore((s) => s.select);
  const unsaved = useCreateStore(selectInfoUnsaved);
  const active = node === 'info';

  return (
    <li>
      <button
        type="button"
        onClick={() => select('info')}
        aria-current={active ? 'true' : undefined}
        className={`flex w-full items-center gap-2.5 rounded-r px-3 py-2 text-left text-sm ${rowTone(active)}`}
      >
        <DocIcon className="h-4 w-4 shrink-0 stroke-ink" />
        발표정보
        {/* 고치다 다른 화면으로 왔을 때 — 저장하지 않은 값이 남아 있다는 것을 여기서 압니다 */}
        {unsaved && <span className="ml-auto text-xs font-bold text-coral">저장 안 됨</span>}
      </button>
    </li>
  );
}

export function VersionRail() {
  const draft = useCreateStore((s) => s.draft);
  const select = useCreateStore((s) => s.select);
  const addSlideVersion = useCreateStore((s) => s.addSlideVersion);
  const addScriptVersion = useCreateStore((s) => s.addScriptVersion);
  const addCriteriaVersion = useCreateStore((s) => s.addCriteriaVersion);

  const latestSlide = draft.slides.at(-1);

  // 목업의 순서입니다 — 무엇을 평가받을지 먼저 정하고, 자료와 대본을 준비합니다
  const groups: RailGroup[] = [
    {
      node: 'criteria',
      label: '평가기준',
      versions: draft.criteria.map((v) => v.version),
      latest: draft.criteria.at(-1)?.version ?? null,
      onAdd: addCriteriaVersion,
    },
    {
      node: 'slides',
      label: '슬라이드',
      versions: draft.slides.map((v) => v.version),
      latest: latestSlide?.version ?? null,
      // 올릴 자리가 이미 열려 있으면(없음 · 파일 대기) 빈 버전을 또 만들지 않고 그 자리를 띄웁니다.
      // 파일을 받는 순간 `attachSlides` 가 버전을 채우거나 만듭니다
      onAdd: () =>
        latestSlide === undefined || latestSlide.pageCount === null
          ? select('slides')
          : addSlideVersion(),
    },
    {
      node: 'script',
      label: '대본',
      versions: draft.scripts.map((v) => v.version),
      latest: draft.scripts.at(-1)?.version ?? null,
      // 직전 버전의 글을 물려받습니다 — 목업의 V1 → V2 → V3 는 고쳐 쓴 흔적입니다
      onAdd: () => addScriptVersion(draft.scripts.at(-1)?.text ?? ''),
    },
  ];

  return (
    <nav aria-label="발표 자료" className="flex min-h-0 flex-1 flex-col">
      <div className="mb-3 flex items-center justify-between border-b border-line-strong pb-3">
        <h2 className="text-sm font-bold">발표 자료</h2>
        <PixelMark />
      </div>
      <ul className="flex flex-col gap-3 overflow-y-auto">
        <InfoItem />
        {groups.map((g) => (
          <GroupBlock key={g.node} group={g} />
        ))}
      </ul>
    </nav>
  );
}
