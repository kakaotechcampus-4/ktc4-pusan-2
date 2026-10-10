import { useEffect } from 'react';
import { Link, useNavigate } from 'react-router';
import { PitchCoachWordmark } from '@/shared/ui/PitchCoachWordmark';
import { Mascot } from './Mascot';
import { InfoBar } from './InfoBar';
import { VersionRail } from './VersionRail';
import { selectBusy, selectInfoUnsaved, useCreateStore } from './createStore';
import {
  latestCriteria,
  latestScript,
  latestSlides,
  toPracticeCombo,
  type PaneNode,
  type ScriptVersion,
} from './lib/draft';
import { CriteriaPane } from './steps/CriteriaPane';
import { InfoPane } from './steps/InfoPane';
import { ScriptPane } from './steps/ScriptPane';
import { SlidePane } from './steps/SlidePane';

/**
 * P3 피치 생성. 목업의 발표정보 · 평가기준 · 슬라이드 · 대본 입력 · 매핑 확인이 **한 라우트**입니다.
 *
 * 마법사(다음 → 다음)가 아닌 이유 — 사이드바가 버전 트리라 아무 때나 지난
 * 버전으로 돌아갈 수 있고, 목업에서 대본만 세 번 고친 흔적(V3)이 그 증거입니다.
 * "다음" 버튼은 단계 이동이 아니라 **연습 시작**이고, 매핑 확인 화면에만 있습니다.
 *
 * 본문은 사이드바에서 고른 것(`node` + `version`)이 정합니다.
 */

/** 사이드바 아래 치치의 말풍선. 지금 화면에서 할 일을 한마디로 */
function mascotLine(node: PaneNode, script: ScriptVersion | null): string {
  if (node === 'criteria') return '기준을 정하면\n피드백이 선명해져!';
  if (node === 'slides') return '발표할 자료를\n확인해 봐!';
  if (node === 'script' && script?.parse.status === 'pending')
    return '수정한 대본을\n연결하고 있어!';
  if (node === 'script' && script?.blocks && !script.remote)
    return '수정한 문장을\n다시 매핑해 줘!';
  if (node === 'script' && script?.blocks && !script.saved)
    return '슬라이드와 대본을\n확인하고 저장해 줘!';
  if (node === 'script' && script?.saved) return '저장 완료!\n이제 연습하자.';
  if (node === 'script') return '한 문장씩\n준비해 보자!';
  return '준비부터\n차근차근!';
}

export function PitchCreatePage() {
  const draft = useCreateStore((s) => s.draft);
  const node = useCreateStore((s) => s.node);
  const version = useCreateStore((s) => s.version);
  const pitchId = useCreateStore((s) => s.pitchId);
  const chosen = useCreateStore((s) => s.chosen);
  const navigate = useNavigate();
  const unsaved = useCreateStore(selectInfoUnsaved);
  const busy = useCreateStore(selectBusy);

  // 탭을 닫거나 새로고침하면 저장하지 않은 발표정보와 진행 중인 업로드 · 나누기가 사라집니다.
  // 브라우저 기본 확인 창만 띄웁니다 — 문구는 브라우저가 정하고 바꿀 수 없습니다
  useEffect(() => {
    if (!unsaved && !busy) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [unsaved, busy]);

  const pickedScript =
    node === 'script'
      ? (draft.scripts.find((v) => v.version === version) ?? latestScript(draft))
      : null;

  /*
    매핑 확인의 "다음" — 장치 점검 → 준비 화면 → 시작 CTA 순서입니다.

    ★ 여기서 POST /takes 를 부르지 않습니다 (CLAUDE.md 8번) —
      Take 는 준비 화면의 시작 CTA 에서만 생깁니다. 여기서 만들면
      점검하다 그만둔 만큼 빈 Take 가 쌓이고 takeNumber 가 어긋납니다.

    ★ 매핑을 저장하며 고른 조합(슬라이드 + 대본)을 장치 점검에 넘깁니다. BE 는 이 조합을
      Take 에 박아 두므로(TakeInitRequestDTO), "어느 슬라이드에 맞춘 대본인가"가 남는 곳은 거기뿐입니다.
  */
  const start = () => {
    const practice = toPracticeCombo(draft, chosen);
    if (pitchId && practice) navigate(`/pitch/${pitchId}/device-check`, { state: { practice } });
  };

  // 고른 버전이 없으면 그 갈래의 최신을 봅니다 — 사이드바가 강조한 것
  const pane = (() => {
    if (node === 'info') return <InfoPane />;
    if (node === 'slides') {
      const picked = draft.slides.find((v) => v.version === version) ?? latestSlides(draft);
      return <SlidePane slide={picked} />;
    }
    // 확인 창 없이 바로 장치 점검으로 갑니다. 조합은 매핑 확인 화면에 이미 보이고,
    // 조합이 맞지 않으면 "다음" 이 막혀 있습니다 (ScriptPane `canStart`)
    if (node === 'script') return <ScriptPane script={pickedScript} onStart={start} />;
    const picked = draft.criteria.find((v) => v.version === version) ?? latestCriteria(draft);
    return <CriteriaPane criteria={picked} />;
  })();

  return (
    <div className="pitch-create flex h-dvh flex-col bg-greige text-ink">
      <header className="flex shrink-0 items-center justify-between border-b border-line-strong bg-panel px-6 py-4">
        <Link to="/" aria-label="PITCH COACH 홈">
          <PitchCoachWordmark />
        </Link>
        <Link to="/" className="text-sm font-bold hover:text-coral-deep">
          ← 내 피치
        </Link>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* ── 좌측: 발표 자료 트리 · 치치 ─────────────────────────── */}
        <aside className="flex w-sidebar shrink-0 flex-col gap-4 overflow-y-auto border-r border-line-strong bg-cream/60 p-4">
          <VersionRail />

          <Mascot message={mascotLine(node, pickedScript)} />
        </aside>

        {/* ── 우측: 경로 · 발표 정보 줄 · 본문 ────────────────────── */}
        <main className="flex min-w-0 flex-1 flex-col gap-3 overflow-auto p-4 xl:p-6">
          <nav aria-label="경로" className="flex items-center gap-2 text-xs text-stone">
            <Link to="/" className="hover:text-ink">
              내 피치
            </Link>
            <span aria-hidden="true">›</span>
            <span aria-current="page" className="text-ink">
              {draft.title.trim() || '새 피치'}
            </span>
          </nav>

          <InfoBar />
          <section className="flex min-h-[32rem] flex-auto shrink-0 flex-col rounded-lg border border-line bg-panel p-5 xl:p-6">
            {pane}
          </section>
        </main>
      </div>
    </div>
  );
}
