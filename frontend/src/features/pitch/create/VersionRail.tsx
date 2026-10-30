import { useRef, useState } from 'react';
import { useCreateStore } from './createStore';
import type { DraftNode } from './lib/draft';
import { CaretIcon, DocIcon, FolderIcon } from './icons';
import { VersionPreviewDialog } from './VersionPreviewDialog';

const groups: { node: DraftNode; label: string }[] = [
  { node: 'slides', label: '슬라이드' },
  { node: 'script', label: '대본' },
  { node: 'criteria', label: '평가기준' },
];

export function VersionRail() {
  const draft = useCreateStore((s) => s.draft);
  const [preview, setPreview] = useState<{ node: DraftNode; version: number } | null>(null);
  const returnFocus = useRef<HTMLButtonElement | null>(null);
  const versions = { slides: draft.slides, script: draft.scripts, criteria: draft.criteria };
  return (
    <nav aria-label="자료 버전 열람">
      <ul className="create-version-tree grid max-h-[min(24rem,max(4rem,calc(100dvh-var(--spacing-app-header)-27rem)))] gap-2 overflow-y-auto [&_summary]:flex [&_summary]:cursor-pointer [&_summary]:list-none [&_summary]:items-center [&_summary]:gap-2 [&_summary]:px-1 [&_summary]:py-2 [&_summary]:text-sm [&_summary]:font-bold [&_summary::-webkit-details-marker]:hidden [&_details:not([open])_summary>svg:first-child]:-rotate-90 [&_details>ul]:ml-6 [&_details>ul]:border-l [&_details>ul]:border-line-strong [&_details>ul>li]:relative [&_details>ul>li]:pl-3 [&_details>ul>li]:before:absolute [&_details>ul>li]:before:left-0 [&_details>ul>li]:before:top-1/2 [&_details>ul>li]:before:w-3 [&_details>ul>li]:before:border-t [&_details>ul>li]:before:border-line-strong [&_button:hover]:bg-coral-wash">
        {groups.map((group) => (
          <li key={group.node}>
            <details>
              <summary>
                <CaretIcon open />
                <FolderIcon />
                <span>{group.label}</span>
                <span className="ml-auto text-xs">{versions[group.node].length}</span>
              </summary>
              <ul>
                {versions[group.node].map((item) => (
                  <li key={item.version}>
                    <button
                      type="button"
                      className="flex min-h-11 w-full items-center gap-2 px-2 py-1.5 text-left text-sm hover:bg-coral-wash"
                      aria-label={`${group.label} V${item.version} 보기`}
                      aria-haspopup="dialog"
                      onClick={(event) => {
                        returnFocus.current = event.currentTarget;
                        setPreview({ node: group.node, version: item.version });
                      }}
                    >
                      <DocIcon className="h-4 w-4 shrink-0 stroke-ink" />V{item.version}
                    </button>
                  </li>
                ))}
              </ul>
              {versions[group.node].length === 0 && (
                <p className="py-2 pl-10 text-xs">저장된 버전이 없어요.</p>
              )}
            </details>
          </li>
        ))}
      </ul>
      {preview && (
        <VersionPreviewDialog
          node={preview.node}
          version={preview.version}
          returnFocus={returnFocus}
          onClose={() => setPreview(null)}
        />
      )}
    </nav>
  );
}
