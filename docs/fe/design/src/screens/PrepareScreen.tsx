// 제품 시작 전 세팅(`frontend/src/features/rehearsal/prepare/DeviceCheckPage.tsx`)의 설계서 샘플.
import { useState } from 'react';
import { Button } from '@/ui/Button';
import { Card } from '@/ui/Panel';
import { Badge } from '@/ui/Badge';
import { ScriptModeChoice, type ScriptMode } from '@/ui/ScriptModeChoice';
import { PITCH, type Navigate } from './shared';

/** 상태별 점검 결과. 제품은 카메라 · 마이크 · 시선 기준이 모두 돼야 시작한다(DeviceCheckPage `ready`) */
function prepareChecks(stateId: string) {
  const camera = stateId !== 'no-camera';
  const mic = camera && stateId !== 'mic-error';
  const gaze = stateId === 'default';
  // 안내 줄은 막고 있는 것 하나만, 제품 deviceCheckHint의 순서대로
  let hint = '점검이 끝났어요. 평소 목소리를 재 두면 작게 말할 때 더 정확히 알려 드려요 (선택)';
  if (!camera) hint = '카메라를 켜야 점검을 시작할 수 있습니다';
  else if (!mic) hint = '마이크에 대고 한 마디 해보세요';
  else if (!gaze) hint = '시선 기준을 먼저 잡아야 연습을 시작할 수 있습니다';
  return { camera, mic, gaze, hint, ready: camera && mic && gaze };
}

export function PrepareScreen({ stateId, onNavigate }: { stateId: string; onNavigate: Navigate }) {
  const [mode, setMode] = useState<ScriptMode>('HIGHLIGHT');
  const check = prepareChecks(stateId);
  const exam = mode === 'OFF';
  const done = [check.camera, check.mic, check.gaze].filter(Boolean).length;
  let gazeAction = '누르면 시작';
  if (check.gaze) gazeAction = '다시 잡기';
  else if (!check.camera) gazeAction = '카메라 먼저';

  return (
    // 제품 `prepare/ScreenFrame.tsx`의 껍데기
    <div className="pitch-create min-h-[40rem] bg-greige px-4 py-5 text-ink">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-3">
        <h1 className="sr-only">시작 전 세팅</h1>
        <div className="border-2 border-ink bg-panel shadow-[4px_4px_0_var(--color-line)]">
          <header className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-4 sm:px-6">
            <button
              type="button"
              onClick={() => onNavigate('pitch-create')}
              className="border-2 border-ink px-2.5 py-1 text-sm font-bold hover:bg-cream"
              aria-label="뒤로"
            >
              ←
            </button>
            <span className="text-sm">뒤로 · {PITCH}</span>
            <span className="text-sm text-stone">· 슬라이드 V1 · 대본 V2</span>
            <span className="tabular ml-auto font-mono text-xs tracking-wide text-stone">
              TAKE 3 · 시작 전 세팅
            </span>
          </header>

          <div className="grid gap-5 px-3 py-5 sm:px-6 lg:grid-cols-[minmax(0,1fr)_340px]">
            <div className="flex min-w-0 flex-col gap-4">
              {/* 카메라 자기 화면 자리. 제품은 여기서 AI 카메라 화면 모듈이 시선 기준을 잡는다 */}
              <div className="relative grid aspect-video max-h-[360px] w-full place-items-center bg-stage text-sm text-ink-stage">
                {check.camera ? (
                  <span>카메라 미리보기 · 얼굴 인식됨</span>
                ) : (
                  <button
                    type="button"
                    onClick={() => onNavigate('prepare-mission', 'calibration')}
                    className="min-h-11 rounded-full bg-coral-deep px-4 py-2 text-xs font-semibold text-white hover:bg-ink"
                  >
                    카메라·마이크 켜기
                  </button>
                )}
              </div>
              <Card
                title="점검 항목"
                action={<span className="tabular font-mono text-xs text-ink/80">{done} / 3</span>}
              >
                <ul className="flex flex-col gap-2">
                  <CheckRow
                    done={check.camera}
                    label={check.camera ? '카메라 · 얼굴 인식됨' : '카메라 · 영상 없음'}
                  />
                  <CheckRow
                    done={check.mic}
                    label={check.mic ? '마이크 입력 확인됨' : '마이크 입력 없음'}
                  />
                  <CheckRow
                    done={check.gaze}
                    label={check.gaze ? '시선 기준 3 / 3' : '시선 기준 · 화면 가운데 · 렌즈 · 대본'}
                    action={gazeAction}
                    // 샘플은 기준 잡기를 건너뛰고 끝난 상태로 넘어간다
                    onAction={
                      check.camera && check.mic && !check.gaze
                        ? () => onNavigate('prepare-mission', 'default')
                        : undefined
                    }
                  />
                  <CheckRow
                    done={false}
                    optional
                    label="평소 목소리 · 문단 읽기"
                    action="누르고 읽기"
                  />
                </ul>
              </Card>
            </div>

            <div className="flex min-w-0 flex-col gap-4">
              <section className="border-2 border-ink bg-panel p-5 shadow-[4px_4px_0_var(--color-line)]">
                <div className="flex items-center justify-between gap-2">
                  <h2 className="text-lg font-bold">이번 Take 미션</h2>
                  <Badge tone="plain">Take 2에서 정함</Badge>
                </div>
                <ol className="mt-3 flex flex-col gap-2">
                  {['문장 끝에서 청중을 바라보기', '3번 장에서 숫자를 천천히 말하기'].map(
                    (m, i) => (
                      <li key={m} className="flex gap-3 border-t border-ink/15 py-3">
                        <span className="tabular flex h-6 w-6 shrink-0 items-center justify-center bg-coral-deep text-xs font-bold text-white">
                          {i + 1}
                        </span>
                        <p className="min-w-0 text-sm font-bold">{m}</p>
                      </li>
                    ),
                  )}
                </ol>
                <p className="mt-3 text-xs leading-relaxed text-ink/80">
                  {exam
                    ? '실전 모드에서는 코치가 알려 주지 않아요. 끝난 뒤 리포트에서 결과를 확인해요.'
                    : '발표 중에 이 미션을 기준으로 코치가 알려 드려요.'}
                </p>
              </section>
              <ScriptModeChoice value={mode} onChange={setMode} />
            </div>
          </div>

          <footer className="flex flex-wrap items-center gap-3 border-t border-line px-4 py-4 sm:px-6">
            <p className="text-sm text-stone">{check.hint}</p>
            <div className="ml-auto flex flex-wrap gap-3">
              <Button
                disabled={!check.ready}
                onClick={() =>
                  onNavigate('stage', mode === 'HIGHLIGHT' ? 'default' : stageState(mode))
                }
              >
                {exam ? 'Take 3 실전 모드로 시작' : 'Take 3 시작하기'}
              </Button>
            </div>
          </footer>
        </div>
      </div>
    </div>
  );
}

function stageState(mode: ScriptMode) {
  return mode === 'OFF' ? 'exam' : 'keyword';
}

/** 제품 `prepare/CheckCard.tsx`의 한 줄. 꼭 할 항목 중 안 된 것만 코랄 테두리로 튀게 둔다 */
function CheckRow({
  done,
  label,
  optional = false,
  action,
  onAction,
}: {
  done: boolean;
  label: string;
  optional?: boolean;
  action?: string;
  onAction?: () => void;
}) {
  const body = (
    <>
      <span aria-hidden className="text-xs">
        {done ? '✓' : '○'}
      </span>
      <span className="min-w-0 flex-1 text-sm">{label}</span>
      {optional && !done && <span className="text-xs text-ink/80">선택</span>}
      {action && (
        <span className="ml-auto shrink-0 border border-current px-2 py-1 text-sm font-bold">
          {action}
        </span>
      )}
    </>
  );
  const tone =
    done || optional ? 'border-line text-ink/80' : 'border-coral-deep bg-coral-wash text-ink';
  const row = `flex w-full items-center gap-3 border-2 px-3 py-2 text-left ${tone}`;
  return (
    <li>
      {action ? (
        <button type="button" className={row} disabled={!onAction} onClick={onAction}>
          {body}
        </button>
      ) : (
        <div className={row}>{body}</div>
      )}
    </li>
  );
}
