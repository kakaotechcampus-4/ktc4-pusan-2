import { useEffect, useLayoutEffect, useRef, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useLocation, useNavigate, useParams } from 'react-router';
import { ApiFailure } from '@/shared/api/tokenStore';
import { CheckIcon } from './icons';
import { useSavePitchInfo } from './useSavePitchInfo';
import './create.css';
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
  const busy = useCreateStore(selectBusy);
  const criteriaUnsaved = useCreateStore(selectCriteriaUnsaved);
  const navigate = useNavigate();
  const save = useSavePitchInfo();
  const viewed = useCreateStore((s) => s.viewedVersions);
  const mounted = useRef(false);

  useEffect(() => {
    if (mounted.current) scrollToSection(node);
    mounted.current = true;
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
  const slidesDone = (resolved.slides?.pageCount ?? 0) > 0;
  const done: Record<PaneNode, boolean> = {
    slides: slidesDone,
    script: practice !== null,
    info: infoDone,
    criteria: !!resolved.criteria?.items.length && !criteriaUnsaved,
  };
  const complete = Number(done.slides) + Number(done.script) + Number(done.info);
  let next: PaneNode | null = null;
  if (!slidesDone) next = 'slides';
  else if (!practice) next = 'script';
  else if (!infoDone) next = 'info';
  else if (criteriaUnsaved) next = 'criteria';

  const go = (target: PaneNode) => {
    select(target, target === 'info' ? null : selectedVersion(target));
    scrollToSection(target);
  };
  const start = () => {
    // 저장 직후의 상태를 읽어 이전 제목·시간이나 다른 자료 조합을 넘기지 않습니다.
    if (script) useCreateStore.getState().saveMapping(script.version);
    const current = useCreateStore.getState();
    const combo = toPracticeCombo(current.draft, current.chosen);
    if (
      current.pitchId &&
      combo &&
      current.infoConfirmed &&
      !selectInfoUnsaved(current) &&
      !selectCriteriaUnsaved(current) &&
      !selectBusy(current)
    )
      navigate(`/pitch/${current.pitchId}/device-check`, { state: { practice: combo } });
  };
  const saveAndStart = next === 'info' && node === 'info' && validInfo;
  const labels: Record<PaneNode, string> = {
    slides: '슬라이드 올리기 ↓',
    script: '대본 연결 확인하기 ↓',
    info: '발표정보 확인하기 ↓',
    criteria: '평가기준 저장하기 ↓',
  };
  const hints: Record<PaneNode, string> = {
    slides: '발표에 사용할 PDF부터 올려 주세요.',
    script: '슬라이드에 맞춰 대본을 매핑하고 저장해 주세요.',
    info: '발표정보를 저장하면 준비가 끝나요.',
    criteria: '고친 평가기준을 먼저 저장해 주세요.',
  };

  let actionLabel = '시작 전 점검 →';
  if (busy) actionLabel = '저장하는 중…';
  else if (saveAndStart) actionLabel = '저장하고 시작 전 점검 →';
  else if (next) actionLabel = labels[next];
  let actionHint = `슬라이드 V${practice?.slideVersion} / 대본 V${practice?.scriptVersion} · 시작 전 점검으로 이동해요.`;
  if (busy) actionHint = '자료를 저장하고 있어요. 잠시만 기다려 주세요.';
  else if (next) actionHint = hints[next];

  return (
    <div className="pitch-create create-page">
      <div className="create-layout">
        <aside className="create-sidebar">
          <nav aria-label="준비 순서">
            <h2>준비 순서</h2>
            <ol>
              {SECTIONS.map((section, index) => (
                <li key={section.node}>
                  <button
                    type="button"
                    onClick={() => go(section.node)}
                    aria-current={node === section.node ? 'step' : undefined}
                  >
                    <span className="create-step-number">{index + 1}</span>
                    <span>{section.label}</span>
                    {done[section.node] ? (
                      <span className="create-step-done" aria-label="완료">
                        <CheckIcon />
                      </span>
                    ) : null}
                    {!done[section.node] && section.node === 'criteria' && <small>선택</small>}
                  </button>
                </li>
              ))}
            </ol>
          </nav>
          <details className="create-versions">
            <summary>자료 버전 관리</summary>
            <VersionRail />
          </details>
        </aside>
        <main className="create-content">
          <div className="create-intro">
            <div>
              <h1>발표 준비</h1>
              <p>자료부터 연습 설정까지, 이 페이지에서 준비해요.</p>
            </div>
            <div className="create-progress">
              <p>
                필수 준비 <strong>{complete}/3</strong> 완료
              </p>
              <progress aria-label="필수 준비" max={3} value={complete} />
            </div>
          </div>
          <section id="create-slides" aria-label="슬라이드" tabIndex={-1}>
            <fieldset disabled={save.isPending} className="min-w-0">
              <SlidePane slide={slide} />
            </fieldset>
          </section>
          <section id="create-script" aria-label="대본 연결" tabIndex={-1}>
            <ScriptPane key={script?.version ?? 'none'} script={script} />
          </section>
          <section id="create-info" aria-label="발표정보" tabIndex={-1}>
            <InfoPane save={save} />
          </section>
          <section id="create-criteria" aria-label="평가기준 (선택)" tabIndex={-1}>
            <CriteriaPane criteria={criteria} />
          </section>
        </main>
      </div>
      <footer className="create-action-bar">
        <div>
          <p role="status">{actionHint}</p>
          <div className="create-actions">
            <Link to="/" className="create-secondary">
              내 피치로
            </Link>
            <button
              type="button"
              className="create-primary"
              disabled={busy}
              onClick={() => {
                if (saveAndStart)
                  save.mutate(undefined, {
                    onSuccess: (saved) => {
                      if (saved) start();
                    },
                  });
                else if (next) go(next);
                else start();
              }}
            >
              {actionLabel}
            </button>
          </div>
        </div>
      </footer>
    </div>
  );
}
