import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router';
import { postCalibration, useCreateTake, usePrepare } from '@/shared/api/prepare';
import { usePitchDetail } from '@/shared/api/take';
import { setTakeId, startSession } from '@/features/rehearsal/lib/db';
import type { EvalCriterion, Mode, ScriptMode } from '@/types/api';
import { ScreenFrame, StageButton } from './ScreenFrame';
import { StagePreview } from './StagePreview';
import { toMessage } from '@/shared/api/errorMessage';
import { usePrepareStore } from './prepareStore';

/** 4단. Off는 대본 영역 자체가 사라집니다 (높이 0) */
const SCRIPT_MODES: { value: ScriptMode; label: string }[] = [
  { value: 'FULL', label: '전체 대본' },
  { value: 'HIGHLIGHT', label: '하이라이트' },
  { value: 'KEYWORD', label: '핵심 단어' },
  { value: 'OFF', label: 'Off' },
];

/**
 * 06 리허설 준비 (P4) — **Take가 생기는 유일한 화면**입니다.
 *
 * 시작 CTA가 하는 일 셋, 순서대로 —
 *   POST /takes                    takeId 발급 (clientSessionId 멱등키)
 *   POST /takes/{id}/calibration   품질 요약만 (기준 벡터는 브라우저에 남습니다)
 *   이동                            /takes/{id}/rehearsal | /exam
 *
 * ── 이 화면이 하지 않는 것 ──────────────────────────────────────────
 * **장치 점검과 캘리브레이션은 여기 없습니다.** 05 카메라 점검에서 끝내고 옵니다.
 * 여기서 또 물으면 같은 것을 두 번 확인하게 되고, 정작 이 화면에서 정해야 하는
 * 한 가지(대본을 어떻게 띄울 것인가)가 묻힙니다.
 * 기준을 못 잡고 들어온 경우에만 시작을 막고 05로 돌려보냅니다.
 *
 * WebSocket도 여기서 붙이지 않습니다. 리허설 화면이 takeId를 받아 붙입니다.
 *
 * Script Mode와 자료·대본·기준 버전이 여기서 고정됩니다. 시작하면 못 바꿉니다 —
 * 대본 영역 높이가 곧 시선 각도라서, 중간에 바꾸면 그 Take의 시선 값이
 * 앞뒤로 다른 조건에서 측정된 것이 됩니다.
 */
export function PreparePage() {
  const { pitchId = '' } = useParams();
  const navigate = useNavigate();
  const { data, isPending, isError, error, refetch } = usePrepare(pitchId);

  // 미리보기가 실제 자료·대본으로 서야 "이 화면으로 발표한다"가 전달됩니다.
  // 발표 중에 다시 받지 않도록 staleTime 은 무한입니다 (shared/api/take.ts).
  const pitch = usePitchDetail(data?.pitchId);

  const scriptMode = usePrepareStore((s) => s.scriptMode);
  const scriptModeTouched = usePrepareStore((s) => s.scriptModeTouched);
  const setScriptMode = usePrepareStore((s) => s.setScriptMode);
  const gazeDeclined = usePrepareStore((s) => s.gazeDeclined);
  const calibration = usePrepareStore((s) => s.calibration);

  const createTake = useCreateTake();
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  // 서버가 준 기본값은 사용자가 아직 안 고른 동안에만 반영합니다.
  // 조건 없이 덮으면 응답이 늦게 올 때 사용자가 고른 모드가 되돌아갑니다.
  useEffect(() => {
    if (data && !scriptModeTouched) setScriptMode(data.defaultScriptMode, false);
  }, [data, scriptModeTouched, setScriptMode]);

  const paragraphs = useMemo(
    () => (pitch.data?.script.content ?? '').split('\n\n').filter((p) => p.trim() !== ''),
    [pitch.data?.script.content],
  );

  /** 05에서 기준을 잡았거나, 소리만으로 가기로 했거나 */
  const gazeReady = calibration !== null || gazeDeclined;
  const canStart = data !== undefined && gazeReady && !starting;

  /** ★ Take는 여기서만 생깁니다. 이 함수를 다른 화면으로 복사하지 마세요 */
  const start = async (mode: Mode) => {
    if (!data || starting) return;
    setStarting(true);
    setStartError(null);

    try {
      // 세션이 먼저입니다 — clientSessionId가 POST /takes의 멱등키이자
      // IndexedDB에 쌓일 모든 기록의 키입니다 (업로드 재시도도 같은 값을 씁니다)
      const clientSessionId = await startSession();

      const take = await createTake.mutateAsync({
        pitchId: data.pitchId,
        clientSessionId,
        mode,
        scriptMode,
        presentationVersion: data.presentationVersion,
        scriptVersion: data.scriptVersion,
        criteriaVersion: data.criteria.version,
      });
      await setTakeId(clientSessionId, take.takeId);

      // 품질 요약만 갑니다. 기준 벡터는 브라우저에 남습니다 (CLAUDE.md 1번)
      if (calibration) await postCalibration(take.takeId, calibration);

      navigate(mode === 'EXAM' ? `/takes/${take.takeId}/exam` : `/takes/${take.takeId}/rehearsal`, {
        state: { clientSessionId, scriptMode },
      });
    } catch (e) {
      // 여기서 멈춰야 합니다. 실패한 채로 리허설로 넘어가면 takeId 없이 발표가 시작되고,
      // 그 Take는 어디에도 안 남습니다
      setStartError(toMessage(e));
      setStarting(false);
    }
  };

  const takeNumber = data?.nextTakeNumber ?? 0;
  const hint = startError
    ? startError
    : isError
      ? '준비 정보를 불러오지 못했어요'
      : !gazeReady
        ? '카메라 점검에서 시선 기준을 먼저 잡아주세요'
        : gazeDeclined
          ? '시선 측정 없이 진행합니다 — 말하기 지표만 리포트에 남습니다'
          : starting
            ? 'Take를 만드는 중…'
            : '시작 후에는 대본 표시를 바꿀 수 없습니다';

  return (
    <ScreenFrame
      screenNo="06"
      screenName="리허설 준비"
      entry="진입 · 카메라 점검의 시작하기"
      title={data?.title ?? '불러오는 중…'}
      subtitle={data ? `자료 v${data.presentationVersion} · 대본 v${data.scriptVersion}` : ''}
      badge={`TAKE ${data?.nextTakeNumber ?? '—'} · 준비`}
      onBack={() => navigate(-1)}
      hint={
        <span className={startError || !gazeReady ? 'text-coral' : undefined}>
          {hint}
          {!gazeReady && (
            <Link to={`/pitch/${pitchId}/device-check`} className="ml-2 underline">
              카메라 점검으로
            </Link>
          )}
        </span>
      }
      actions={
        <>
          <StageButton disabled={!canStart} onClick={() => start('EXAM').catch(() => undefined)}>
            실전 모드로 Take {takeNumber} 시작
          </StageButton>
          <StageButton
            variant="primary"
            disabled={!canStart}
            onClick={() => start('COACHING').catch(() => undefined)}
          >
            코칭 모드로 Take {takeNumber} 시작
          </StageButton>
        </>
      }
    >
      {isError ? (
        <div className="flex flex-col items-start gap-3 rounded-xl border border-coral bg-coral/10 p-5">
          <p className="text-sm">{toMessage(error)}</p>
          <StageButton onClick={() => refetch().catch(() => undefined)}>다시 시도</StageButton>
        </div>
      ) : isPending ? (
        <PrepareSkeleton />
      ) : (
        <div className="flex flex-col gap-4">
          {data.lastMission && (
            <div className="flex flex-wrap items-center gap-3 rounded-xl border border-coral bg-coral/10 px-4 py-3">
              <span className="font-mono text-[11px] font-bold tracking-wide text-coral">
                지난 TAKE MISSION
              </span>
              <span className="text-sm">{data.lastMission.description}</span>
            </div>
          )}

          <div className="grid gap-5 lg:grid-cols-[1fr_340px]">
            <div className="flex flex-col gap-4">
              <section>
                <div className="flex flex-wrap items-baseline gap-2">
                  <h2 className="text-sm font-bold">발표 중 대본 표시</h2>
                  <span className="text-xs text-stone">시작 후 변경 불가</span>
                </div>

                <div className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-4">
                  {SCRIPT_MODES.map((m) => {
                    const on = scriptMode === m.value;
                    return (
                      <button
                        key={m.value}
                        type="button"
                        aria-pressed={on}
                        onClick={() => setScriptMode(m.value)}
                        className={`rounded-lg border px-3 py-3 text-sm ${
                          on
                            ? 'border-ink-stage bg-stage-panel font-bold'
                            : 'border-dashed border-stage-panel text-stone hover:bg-stage-panel/60'
                        }`}
                      >
                        {m.label}
                      </button>
                    );
                  })}
                </div>
              </section>

              <section>
                <div className="flex flex-wrap items-baseline gap-2">
                  <h2 className="text-sm font-bold">발표 연습 화면 미리보기</h2>
                  <span className="ml-auto font-mono text-[11px] text-stone">
                    07 발표 연습 화면 · 축소 표시
                  </span>
                </div>

                <div className="mt-2">
                  {pitch.isPending ? (
                    <div className="h-115 animate-pulse rounded-xl bg-stage-panel" />
                  ) : (
                    <StagePreview
                      mode={scriptMode}
                      slides={pitch.data?.presentation.slides ?? []}
                      paragraphs={paragraphs}
                      timeLimitSec={pitch.data?.timeLimitSec ?? data.timeLimitSec}
                    />
                  )}
                </div>
              </section>
            </div>

            <CriteriaCard
              version={data.criteria.version}
              items={data.criteria.items}
              pitchId={data.pitchId}
              onEdit={() => navigate(`/pitch/${data.pitchId}/edit`)}
            />
          </div>
        </div>
      )}
    </ScreenFrame>
  );
}

/**
 * 내가 정한 평가 기준. 준비 화면에서는 **읽기 전용**입니다 —
 * 기준을 여기서 고치면 Take가 스냅샷한 버전과 리포트의 기준이 어긋납니다.
 */
function CriteriaCard({
  version,
  items,
  pitchId,
  onEdit,
}: {
  version: number;
  items: EvalCriterion[];
  pitchId: string;
  onEdit: () => void;
}) {
  return (
    <section className="h-fit rounded-xl border border-stage-panel bg-stage-panel/40 p-4">
      <header className="flex flex-wrap items-baseline gap-2">
        <h2 className="text-sm font-bold">내가 정한 평가 기준</h2>
        <span className="tabular ml-auto font-mono text-[11px] text-stone">
          CRIT V{version} · {items.length}개 · 읽기 전용
        </span>
      </header>

      {items.length === 0 ? (
        <div className="mt-3 flex flex-col items-start gap-2 rounded-lg bg-stage px-3 py-4">
          <p className="text-sm text-stone">아직 정한 기준이 없어요</p>
          <p className="text-xs text-stone">
            기준이 있으면 리포트가 &ldquo;무엇을 지켰나&rdquo;로 바뀝니다
          </p>
          <button
            type="button"
            onClick={onEdit}
            className="text-xs font-semibold text-coral hover:underline"
            aria-label={`Pitch ${pitchId}의 평가 기준 정하기`}
          >
            기준 정하러 가기
          </button>
        </div>
      ) : (
        <ol className="mt-3 flex flex-col gap-1.5">
          {items.map((c) => (
            <li key={c.id} className="flex gap-3 rounded-lg bg-stage px-3 py-2.5">
              <span className="tabular font-mono text-xs text-stone">
                {String(c.order).padStart(2, '0')}
              </span>
              <span className="text-sm">{c.text}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function PrepareSkeleton() {
  return (
    <div className="grid animate-pulse gap-5 lg:grid-cols-[1fr_340px]">
      <div className="flex flex-col gap-4">
        <div className="h-16 rounded-xl bg-stage-panel" />
        <div className="h-115 rounded-xl bg-stage-panel" />
      </div>
      <div className="h-80 rounded-xl bg-stage-panel" />
    </div>
  );
}
