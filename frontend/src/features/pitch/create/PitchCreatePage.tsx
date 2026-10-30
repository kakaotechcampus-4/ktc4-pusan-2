import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { useIsMutating, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useLocation, useNavigate, useParams } from 'react-router';
import { ApiFailure } from '@/shared/api/tokenStore';
import { CheckIcon } from './icons';
import { useSavePitchInfo } from './useSavePitchInfo';
import { loadPitch } from './loadPitch';
import { VersionRail } from './VersionRail';
import {
  selectBusy,
  selectCriteriaUnsaved,
  selectInfoUnsaved,
  useCreateStore,
} from './createStore';
import {
  infoOf,
  infoReady,
  resolveChosen,
  toPracticeCombo,
  type PaneNode,
  type DraftNode,
} from './lib/draft';
import { CriteriaPane } from './steps/CriteriaPane';
import { InfoPane } from './steps/InfoPane';
import { ScriptPane } from './steps/ScriptPane';
import { SlidePane } from './steps/SlidePane';
import { FinalReviewDialog } from './FinalReviewDialog';
import type { Chosen, PitchDraft } from './lib/draft';

/**
 * 새로 시작한 작성인지 보고, 그렇다면 앞 피치를 비운 뒤에 화면을 그립니다.
 *
 * 스토어는 앱 전체에 하나라 화면을 떠나도 남습니다. 피치 A 를 저장하고 홈에서 "새 피치 만들기"로
 * 들어오면 A 가 그대로 열려, 저장이 새 피치(POST) 대신 A 수정(PATCH)으로 갑니다.
 * 기록 항목(`location.key`)이 다르면 새로 시작한 것으로 봅니다 — `createStore` 의 `entryKey`.
 *
 * 비우기 전에 그리면 발표정보 칸이 A 의 값으로 한 번 그려지므로, 비울 때까지 아무것도 그리지 않습니다.
 * `useLayoutEffect` 라 화면에 A 가 비치지 않습니다.
 */
export function PitchCreatePage() {
  const { key } = useLocation();
  const entryKey = useCreateStore((s) => s.entryKey);
  const beginEntry = useCreateStore((s) => s.beginEntry);

  useLayoutEffect(() => {
    beginEntry(key);
  }, [key, beginEntry]);

  if (entryKey !== key) return null;
  return <PitchCreateScreen />;
}

/** 피치가 없거나 남의 것 — 다시 시도해도 같습니다. 그 밖(네트워크 · 5xx · PDF 를 못 엶)은 다시 시도합니다 */
const isMissing = (error: unknown) =>
  error instanceof ApiFailure && (error.status === 404 || error.status === 403);

/**
 * P13 피치 수정. **피치 생성 화면을 그대로 씁니다** — 들어올 때 서버의 피치를 읽어 채우는 것만 다릅니다.
 *
 * 생성 화면이 이미 버전 트리라, 수정은 "불러온 버전 위에 새 버전을 만드는 것"입니다. 저장한 버전은
 * 고치지 않고, 고치면 새 버전이 생기는 규칙도 같습니다. 발표정보는 `PATCH /pitches/update/{id}` 로 고칩니다.
 *
 * 생성 화면처럼 기록 항목(`location.key`)으로 "새로 들어옴"과 "장치 점검에서 돌아옴"을 가릅니다.
 * 돌아왔으면 다시 읽지 않고 고치던 그대로 보여 줍니다 (`createStore.hydrate`).
 */
export function PitchEditPage() {
  const { id = '' } = useParams();
  const { key } = useLocation();
  const entryKey = useCreateStore((s) => s.entryKey);
  const editing = useCreateStore((s) => s.pitchId);
  const hydrate = useCreateStore((s) => s.hydrate);
  const ready = entryKey === key && editing === id;

  const pitch = useQuery({
    queryKey: ['pitch-edit', id, key],
    queryFn: () => loadPitch(id),
    enabled: !ready && id !== '',
    // 한 번 읽어 채운 뒤에는 화면이 고치는 값이 맞습니다. 다시 읽어 덮지 않습니다
    staleTime: Infinity,
    // 화면을 떠나면 바로 버립니다. 다시 들어오면 그사이 올린 버전까지 서버에서 새로 읽어야 합니다 —
    // 남겨 두면 옛 결과로 채워져 사이드바 번호가 서버와 어긋납니다
    gcTime: 0,
    retry: false,
  });

  useLayoutEffect(() => {
    if (!ready && pitch.data) hydrate(key, id, pitch.data);
  }, [ready, pitch.data, hydrate, key, id]);

  useEffect(() => {
    if (pitch.error)
      console.error('[피치 수정] 피치를 불러오지 못했습니다', { id, error: pitch.error });
  }, [pitch.error, id]);

  if (ready) return <PitchCreateScreen />;
  if (pitch.isError) {
    return isMissing(pitch.error) ? (
      <EditNotice title="피치를 찾을 수 없어요" body="지워졌거나 열 수 없는 피치예요." />
    ) : (
      <EditNotice
        title="피치를 불러오지 못했어요"
        body="잠시 후 다시 시도해 주세요."
        action={
          <button
            type="button"
            onClick={() => {
              pitch.refetch().catch(() => undefined);
            }}
            className="h-11 rounded-md bg-coral-deep px-6 text-sm font-bold text-white hover:bg-ink"
          >
            다시 불러오기
          </button>
        }
      />
    );
  }
  return (
    <EditNotice
      title="피치를 불러오는 중이에요…"
      body="슬라이드 · 대본 · 평가기준을 가져오고 있어요."
      busy
    />
  );
}

/** 수정 화면을 그리기 전의 안내 한 장 — 불러오는 중 · 없음 · 실패 */
function EditNotice({
  title,
  body,
  action,
  busy = false,
}: {
  title: string;
  body: string;
  action?: ReactNode;
  busy?: boolean;
}) {
  return (
    <div className="pitch-create flex min-h-0 flex-1 flex-col bg-greige text-ink">
      <div className="flex flex-1 items-center justify-center p-6">
        <div
          role={busy ? 'status' : 'alert'}
          className="flex w-full max-w-md flex-col items-center gap-3 rounded-lg border border-line bg-panel p-8 text-center"
        >
          <h1 className="text-lg font-bold">{title}</h1>
          <p className="text-sm text-ink/80">{body}</p>
          <div className="mt-2 flex items-center gap-3">
            {action}
            <Link to="/" className="text-sm font-bold hover:text-ink">
              내 피치로
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}

const SECTIONS: { node: PaneNode; label: string }[] = [
  { node: 'slides', label: '슬라이드' },
  { node: 'script', label: '대본 연결' },
  { node: 'info', label: '발표정보' },
  { node: 'criteria', label: '평가기준' },
];

function scrollToSection(node: PaneNode) {
  const section = document.getElementById(`create-${node}`);
  section?.scrollIntoView({ block: 'start', behavior: 'instant' });
  section?.focus({ preventScroll: true });
}

function PitchCreateScreen() {
  const draft = useCreateStore((s) => s.draft);
  const node = useCreateStore((s) => s.node);
  const version = useCreateStore((s) => s.version);
  const pitchId = useCreateStore((s) => s.pitchId);
  const chosen = useCreateStore((s) => s.chosen);
  const infoEdits = useCreateStore((s) => s.infoEdits);
  const confirmed = useCreateStore((s) => s.infoConfirmed);
  const select = useCreateStore((s) => s.select);
  const unsaved = useCreateStore(selectInfoUnsaved);
  const savingResources = useCreateStore(selectBusy);
  const draftId = useCreateStore((s) => s.draftId);
  const criteriaSaving = useIsMutating({ mutationKey: ['pitch-criteria', draftId] });
  const queryClient = useQueryClient();
  const busy = savingResources || criteriaSaving > 0;
  const criteriaUnsaved = useCreateStore(selectCriteriaUnsaved);
  const sourceEdits = useCreateStore((s) => s.criteriaSourceEdits);
  const navigate = useNavigate();
  const save = useSavePitchInfo();
  const viewed = useCreateStore((s) => s.viewedVersions);
  const previousSelection = useRef({ node, version });
  const [focus, setFocus] = useState({ node, version, active: node });
  const active = focus.node === node && focus.version === version ? focus.active : node;
  const setActive = (active: PaneNode) => setFocus({ node, version, active });
  const [review, setReview] = useState<{ draft: PitchDraft; chosen: Chosen } | null>(null);
  const pendingSlides = useCreateStore((s) => s.pendingSlideReviews);
  const pendingCriteria = useCreateStore((s) => s.pendingCriteriaReviews);

  useEffect(() => {
    if (previousSelection.current.node !== node || previousSelection.current.version !== version)
      scrollToSection(node);
    previousSelection.current = { node, version };
  }, [node, version]);

  useEffect(() => {
    if (!unsaved && !busy && !criteriaUnsaved) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [unsaved, busy, criteriaUnsaved]);

  const selectedVersion = (kind: DraftNode) => (node === kind ? version : viewed[kind]);
  const slide =
    draft.slides.find((v) => v.version === selectedVersion('slides')) ??
    draft.slides.at(-1) ??
    null;
  const script =
    draft.scripts.find((v) => v.version === selectedVersion('script')) ??
    draft.scripts.at(-1) ??
    null;
  const criteria =
    draft.criteria.find((v) => v.version === selectedVersion('criteria')) ??
    draft.criteria.at(-1) ??
    null;
  const currentChoice = script
    ? { ...chosen, slides: script.slideVersion, script: script.version }
    : chosen;
  const practice = toPracticeCombo(draft, currentChoice);
  const resolved = resolveChosen(draft, currentChoice);
  const form = infoEdits ?? infoOf(draft);
  const validInfo = form.title.trim() !== '' && infoReady(form);
  const infoDone = !!pitchId && confirmed && !unsaved && validInfo;
  const slidesDone =
    !!resolved.slides?.pageCount && !pendingSlides.includes(resolved.slides.version);
  const criteriaNeedsReview =
    !!resolved.criteria && pendingCriteria.includes(resolved.criteria.version);
  const done: Record<PaneNode, boolean> = {
    slides: slidesDone,
    script: practice !== null,
    info: infoDone,
    criteria: !!resolved.criteria?.items.length && !criteriaUnsaved && !criteriaNeedsReview,
  };
  const complete = Number(done.slides) + Number(done.script) + Number(done.info);
  let next: PaneNode | null = null;
  if (!slidesDone) next = 'slides';
  else if (!practice) next = 'script';
  else if (!infoDone) next = 'info';
  else if (criteriaUnsaved || criteriaNeedsReview) next = 'criteria';

  const go = (target: PaneNode) => {
    let targetVersion = target === 'info' ? null : selectedVersion(target);
    if (target === 'slides' && resolved.slides && pendingSlides.includes(resolved.slides.version))
      targetVersion = resolved.slides.version;
    if (target === 'criteria') {
      const source = Object.entries(sourceEdits).find(
        ([version, text]) =>
          text.trim() !== '' &&
          text !== (draft.criteria.find((v) => v.version === Number(version))?.sourceText ?? ''),
      );
      const edited = draft.criteria.find((v) => v.edited);
      if (source) targetVersion = Number(source[0]) || null;
      else if (edited) targetVersion = edited.version;
      else if (criteriaNeedsReview) targetVersion = resolved.criteria?.version ?? null;
    }
    setActive(target);
    select(target, targetVersion);
    scrollToSection(target);
  };
  const start = () => {
    const current = useCreateStore.getState();
    if (!review || current.draft !== review.draft) return;
    const combo = toPracticeCombo(current.draft, review.chosen);
    const used = resolveChosen(current.draft, review.chosen);
    if (
      current.pitchId &&
      combo &&
      current.infoConfirmed &&
      infoReady(infoOf(current.draft)) &&
      !selectInfoUnsaved(current) &&
      !selectCriteriaUnsaved(current) &&
      !selectBusy(current) &&
      !current.pendingSlideReviews.includes(combo.slideVersion) &&
      (!used.criteria || !current.pendingCriteriaReviews.includes(used.criteria.version)) &&
      queryClient.isMutating({ mutationKey: ['pitch-criteria', current.draftId] }) === 0
    ) {
      current.chooseVersion('slides', review.chosen.slides);
      current.chooseVersion('script', review.chosen.script);
      navigate(`/pitch/${current.pitchId}/device-check`, { state: { practice: combo } });
    }
  };
  const labels: Record<PaneNode, string> = {
    slides: '슬라이드 확인하기 ↓',
    script: '대본 연결 확인하기 ↓',
    info: '발표정보 확인하기 ↓',
    criteria: '평가기준 확인하기 ↓',
  };
  const hints: Record<PaneNode, string> = {
    slides: 'PDF를 올리고 내용과 순서를 확인해 주세요.',
    script: '슬라이드에 맞춰 대본을 매핑하고 저장해 주세요.',
    info: '발표정보를 확인하고 저장해 주세요.',
    criteria: '평가기준을 정리·저장하고 결과를 확인해 주세요.',
  };

  let actionLabel = '최종 확인 →';
  if (busy) actionLabel = '저장하는 중…';
  else if (next) actionLabel = labels[next];
  let actionHint = `슬라이드 V${practice?.slideVersion} / 대본 V${practice?.scriptVersion} · 준비한 내용을 최종 확인해 주세요.`;
  if (busy) actionHint = '자료를 저장하고 있어요. 잠시만 기다려 주세요.';
  else if (next) actionHint = hints[next];

  return (
    <div className="pitch-create pitch-create-square min-h-[calc(100dvh-var(--spacing-app-header))] bg-greige pb-24 text-ink max-[760px]:pb-40 selection:bg-coral-wash selection:text-ink [&_textarea]:caret-ink">
      <div className="mx-auto grid max-w-page grid-cols-[236px_minmax(0,1fr)] max-[1100px]:grid-cols-[190px_minmax(0,1fr)] max-[760px]:block">
        <aside className="sticky top-6 self-start px-6 py-3 max-[1100px]:px-3 max-[760px]:static max-[760px]:p-4 [&>nav_h2]:mb-4 [&>nav_h2]:font-bold [&>nav_ol]:grid [&>nav_ol]:gap-2 max-[760px]:[&>nav_ol]:grid-cols-2 [&>nav_button]:flex [&>nav_button]:min-h-12 [&>nav_button]:w-full [&>nav_button]:items-center [&>nav_button]:gap-2.5 [&>nav_button]:p-2 [&>nav_button]:text-left [&>nav_button]:text-sm [&>nav_button:hover]:bg-cream [&>nav_button[aria-current]]:bg-coral-wash [&>nav_button[aria-current]]:font-bold [&>nav_button[aria-current]]:text-coral-deep [&>nav_small]:ml-auto [&>nav_small]:text-xs [&>nav_small]:text-stone">
          <nav aria-label="준비 순서">
            <h2>준비 순서</h2>
            <ol>
              {SECTIONS.map((section, index) => (
                <li key={section.node}>
                  <button
                    type="button"
                    onClick={() => go(section.node)}
                    aria-current={active === section.node ? 'step' : undefined}
                  >
                    <span className="grid size-[26px] shrink-0 place-items-center rounded-full bg-line text-xs text-ink [[aria-current]>&]:bg-coral-deep [[aria-current]>&]:text-white">
                      {index + 1}
                    </span>
                    <span>{section.label}</span>
                    {done[section.node] ? (
                      <span className="ml-auto text-ink" aria-label="완료">
                        <CheckIcon />
                      </span>
                    ) : null}
                    {!done[section.node] && section.node === 'criteria' && <small>선택</small>}
                  </button>
                </li>
              ))}
            </ol>
          </nav>
          <details className="create-versions mt-4 border-t border-line pt-2 max-[760px]:mt-2 [&>summary]:cursor-pointer [&>summary]:py-2 [&>summary]:text-sm [&>summary]:font-bold [&>nav]:mt-2">
            <summary>자료 버전 관리</summary>
            <VersionRail />
          </details>
        </aside>
        <main className="min-w-0 border-l border-line px-10 py-8 max-[1100px]:p-6 max-[760px]:border-l-0 max-[760px]:p-5 [&>section]:-mx-10 [&>section]:min-w-0 [&>section]:scroll-mt-6 [&>section]:bg-[color-mix(in_srgb,var(--color-panel)_55%,var(--color-cream))] [&>section[data-current]]:outline [&>section[data-current]]:outline-1 [&>section[data-current]]:-outline-offset-1 [&>section[data-current]]:outline-coral-deep [&>section]:px-10 [&>section]:py-8 [&>section:nth-of-type(even)]:bg-cream max-[1100px]:[&>section]:-mx-6 max-[1100px]:[&>section]:px-6 max-[760px]:[&>section]:-mx-5 max-[760px]:[&>section]:px-5">
          <div className="flex items-center justify-between gap-8 pb-6 max-[1100px]:gap-4 max-[760px]:flex-wrap [&_h1]:text-[1.75rem] [&_h1]:font-bold [&_h1]:tracking-tight [&_p]:mt-2 [&_p]:text-sm [&_p]:text-stone">
            <div>
              <h1>발표 준비</h1>
              <p>자료부터 연습 설정까지, 이 페이지에서 준비해요.</p>
            </div>
            <div className="basis-52 shrink-0 max-[1100px]:basis-40 max-[760px]:basis-full [&_strong]:text-coral-deep [&_progress]:mt-2 [&_progress]:block [&_progress]:h-1.5 [&_progress]:w-full [&_progress]:appearance-none [&_progress]:border-0 [&_progress]:bg-line [&_progress::-webkit-progress-bar]:bg-line [&_progress::-webkit-progress-value]:bg-coral-deep [&_progress::-moz-progress-bar]:bg-coral-deep">
              <p>
                필수 준비 <strong>{complete}/3</strong> 완료
              </p>
              <progress aria-label="필수 준비" max={3} value={complete} />
            </div>
          </div>
          <section
            data-current={active === 'slides' ? true : undefined}
            onFocusCapture={() => setActive('slides')}
            onPointerDown={() => setActive('slides')}
            id="create-slides"
            aria-label="슬라이드"
            tabIndex={-1}
          >
            <fieldset disabled={save.isPending} className="min-w-0">
              <SlidePane slide={slide} />
            </fieldset>
          </section>
          <section
            data-current={active === 'script' ? true : undefined}
            onFocusCapture={() => setActive('script')}
            onPointerDown={() => setActive('script')}
            id="create-script"
            aria-label="대본 연결"
            tabIndex={-1}
          >
            <ScriptPane key={script?.version ?? 'none'} script={script} />
          </section>
          <section
            data-current={active === 'info' ? true : undefined}
            onFocusCapture={() => setActive('info')}
            onPointerDown={() => setActive('info')}
            id="create-info"
            aria-label="발표정보"
            tabIndex={-1}
          >
            <InfoPane save={save} />
          </section>
          <section
            data-current={active === 'criteria' ? true : undefined}
            onFocusCapture={() => setActive('criteria')}
            onPointerDown={() => setActive('criteria')}
            id="create-criteria"
            aria-label="평가기준 (선택)"
            tabIndex={-1}
          >
            <CriteriaPane criteria={criteria} />
          </section>
        </main>
      </div>
      <footer className="fixed inset-x-0 bottom-0 z-20 border-t border-line bg-white [&>div]:mx-auto [&>div]:flex [&>div]:max-w-page [&>div]:items-center [&>div]:justify-between [&>div]:gap-6 [&>div]:px-10 [&>div]:py-4 [&_p]:text-sm max-[1100px]:[&>div]:gap-4 max-[1100px]:[&>div]:px-6 max-[760px]:[&>div]:flex-col max-[760px]:[&>div]:items-stretch max-[760px]:[&>div]:gap-3 max-[760px]:[&>div]:px-4 max-[760px]:[&>div]:py-3">
        <div>
          <p role="status">{actionHint}</p>
          <div className="flex shrink-0 gap-3 max-[760px]:flex-wrap max-[760px]:justify-end">
            <Link
              to="/"
              className="inline-flex min-h-11 items-center justify-center border border-line-strong px-5 py-3 text-sm font-bold hover:bg-cream"
            >
              내 피치로
            </Link>
            <button
              type="button"
              className="inline-flex min-h-11 items-center justify-center bg-coral-deep px-5 py-3 text-sm font-bold text-white hover:bg-ink disabled:cursor-not-allowed disabled:bg-line disabled:text-stone"
              disabled={busy}
              onClick={() => {
                if (next) go(next);
                else setReview({ draft, chosen: currentChoice });
              }}
            >
              {actionLabel}
            </button>
          </div>
        </div>
      </footer>
      {review &&
        review.draft === draft &&
        next === null &&
        !busy &&
        review.chosen.slides === currentChoice.slides &&
        review.chosen.script === currentChoice.script &&
        review.chosen.criteria === currentChoice.criteria && (
          <FinalReviewDialog
            draft={review.draft}
            chosen={review.chosen}
            onClose={() => setReview(null)}
            onConfirm={start}
          />
        )}
    </div>
  );
}
