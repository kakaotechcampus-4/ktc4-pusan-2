import { useId } from 'react';
import { toMessage } from '@/shared/api/errorMessage';
import { useCreateStore } from '../createStore';
import type { useSavePitchInfo } from '../useSavePitchInfo';
import {
  infoChanged,
  infoOf,
  infoReady,
  MAX_TIME_LIMIT_MIN,
  MAX_TITLE_CHARS,
  MAX_TOLERANCE_SEC,
  MIN_TIME_LIMIT_MIN,
  TOLERANCE_STEP_SEC,
  formatAllowedRange,
  formatDuration,
} from '../lib/draft';
import { PaneHeading } from './PaneHeading';

/**
 * 목업 발표정보 — 제목 · 목표 발표시간 · 하한/상한 허용오차.
 *
 * 피치 한 판에 하나뿐이라 버전이 없습니다. 고치는 동안은 스토어의 `infoEdits` 에 두었다가
 * **저장할 때 한 번에** 넣습니다 — 위 정보 줄이 입력 중인 글자를 따라 깜빡이지 않고,
 * 다른 화면에 다녀와도 고치던 값이 남습니다.
 *
 * 처음 저장하면 피치가 생기고(`POST /pitches/add`), 그 뒤로는 고칩니다(`PUT /pitches/update/{id}`).
 * 발표자료 · 대본 · 평가기준은 여기서 받은 pitch_id 아래로 올라갑니다.
 *
 * 허용오차를 위아래로 나눈 이유 — "5분 발표, 30초 짧은 건 괜찮지만 1분까지는 넘어도 된다"
 * 처럼 두 쪽이 다른 경우가 흔합니다. 서버도 `lower_deviation` · `upper_deviation` 으로 나눠 받습니다.
 */

function saveLabel(pending: boolean, unsaved: boolean): string {
  if (pending) return '저장하는 중…';
  if (unsaved) return '저장';
  return '저장됨';
}

const stepButton =
  'flex items-center justify-center rounded-md border border-line-strong bg-white text-xl hover:bg-panel disabled:cursor-not-allowed disabled:text-line-strong disabled:hover:bg-white';

function Stepper({
  label,
  value,
  onDecrease,
  onIncrease,
  canDecrease,
  canIncrease,
  size,
}: {
  label: string;
  value: string;
  onDecrease: () => void;
  onIncrease: () => void;
  canDecrease: boolean;
  canIncrease: boolean;
  size: 'lg' | 'sm';
}) {
  const box = size === 'lg' ? 'h-12 w-16' : 'h-10 w-12';
  return (
    <div className="flex items-stretch gap-2">
      <button
        type="button"
        aria-label={`${label} 줄이기`}
        disabled={!canDecrease}
        onClick={onDecrease}
        className={`${stepButton} ${box}`}
      >
        −
      </button>
      <span
        aria-live="polite"
        aria-label={`${label} ${value}`}
        className={[
          'tabular flex flex-1 items-center justify-center rounded-md bg-panel font-bold',
          size === 'lg' ? 'text-lg' : 'text-base',
        ].join(' ')}
      >
        {value}
      </span>
      <button
        type="button"
        aria-label={`${label} 늘리기`}
        disabled={!canIncrease}
        onClick={onIncrease}
        className={`${stepButton} ${box}`}
      >
        +
      </button>
    </div>
  );
}

/**
 * 허용오차 한 칸. `valueSec` 가 null 이면 **모르는 값**입니다 (수정 화면, `PitchDraft.lowerToleranceSec`).
 * 그때 −는 0초, +는 한 칸(30초)에서 시작합니다 — 사용자가 정해야 저장할 수 있습니다.
 */
function ToleranceField({
  label,
  hint,
  valueSec,
  maxSec,
  onChange,
}: {
  label: string;
  hint: string;
  valueSec: number | null;
  maxSec: number;
  onChange: (sec: number) => void;
}) {
  const unknown = valueSec === null;
  const sec = valueSec ?? 0;
  return (
    <div className="flex flex-1 flex-col gap-2">
      <p className="text-sm font-bold">{label}</p>
      <Stepper
        label={label}
        value={unknown ? '확인 필요' : formatDuration(sec)}
        size="sm"
        canDecrease={unknown || sec > 0}
        canIncrease={sec + TOLERANCE_STEP_SEC <= maxSec}
        onDecrease={() => onChange(unknown ? 0 : sec - TOLERANCE_STEP_SEC)}
        onIncrease={() => onChange(sec + TOLERANCE_STEP_SEC)}
      />
      <p className={unknown ? 'text-xs font-bold text-ink' : 'text-xs text-ink/80'}>
        {unknown ? '저장된 값을 불러오지 못했어요. 다시 정해 주세요' : hint}
      </p>
    </div>
  );
}

/** 저장 버튼에 올리는 막힌 이유. 막히지 않았으면 undefined */
function blockedReason(titleMissing: boolean, toleranceUnknown: boolean): string | undefined {
  if (titleMissing) return '발표 제목을 먼저 입력해 주세요';
  if (toleranceUnknown) return '허용오차를 정해 주세요 — 저장된 값을 불러오지 못했어요';
  return undefined;
}

export function InfoPane({ save }: { save: ReturnType<typeof useSavePitchInfo> }) {
  const draft = useCreateStore((s) => s.draft);
  const edits = useCreateStore((s) => s.infoEdits);
  const patch = useCreateStore((s) => s.editInfo);
  const pitchId = useCreateStore((s) => s.pitchId);
  const confirmed = useCreateStore((s) => s.infoConfirmed);
  const uploading = useCreateStore((s) => s.slideUpload.status === 'uploading');
  const id = useId();

  const saved = infoOf(draft);
  const form = edits ?? saved;

  const minutes = Math.round(form.timeLimitSec / 60);
  const setMinutes = (m: number) => {
    const limitSec = m * 60;
    // 목표가 줄면 하한도 함께 줄입니다 — "3분 발표에 하한 5분" 은 0초 밑으로 내려갑니다
    patch({
      timeLimitSec: limitSec,
      lowerToleranceSec:
        form.lowerToleranceSec === null ? null : Math.min(form.lowerToleranceSec, limitSec),
    });
  };

  // 피치가 아직 없으면 고친 게 없어도 저장해야 합니다 — 기본값 그대로 만들 수도 있습니다
  const dirty = pitchId === null || !confirmed || infoChanged(saved, form);
  const titleMissing = form.title.trim() === '';
  const toleranceUnknown = !infoReady(form);
  const blocked = blockedReason(titleMissing, toleranceUnknown);

  return (
    <div className="flex flex-1 flex-col">
      <PaneHeading title="3. 발표정보" subtitle="발표 제목과 목표 시간을 확인해 주세요." />

      <fieldset
        disabled={save.isPending || uploading}
        className="min-w-0 flex flex-col gap-5 border border-line bg-white p-6"
      >
        <div className="flex flex-col gap-2">
          <div className="flex items-baseline justify-between">
            <label htmlFor={`${id}-title`} className="text-sm font-bold">
              발표 제목
            </label>
            <span className="tabular text-xs text-ink/80" aria-live="polite">
              {form.title.length} / {MAX_TITLE_CHARS}
            </span>
          </div>
          <input
            id={`${id}-title`}
            placeholder="발표 제목을 입력해 주세요"
            value={form.title}
            // BE 가 50자를 넘으면 422 로 거절합니다 — 저장할 때 알게 하지 않고 입력에서 막습니다
            maxLength={MAX_TITLE_CHARS}
            onChange={(e) => patch({ title: e.target.value })}
            className="h-12 rounded-md border border-line-strong bg-white px-4 text-base focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink placeholder:text-ink/80 focus:border-ink"
          />
        </div>

        <div className="flex flex-col gap-5">
          <div className="flex flex-col gap-2">
            <p className="text-sm font-bold">목표 발표시간</p>
            <Stepper
              label="목표 발표시간"
              value={`${minutes}분`}
              size="lg"
              canDecrease={minutes > MIN_TIME_LIMIT_MIN}
              canIncrease={minutes < MAX_TIME_LIMIT_MIN}
              onDecrease={() => setMinutes(minutes - 1)}
              onIncrease={() => setMinutes(minutes + 1)}
            />
          </div>

          <div className="flex flex-col gap-6 sm:flex-row">
            <ToleranceField
              label="짧게 허용"
              hint="목표보다 짧게 발표해도 되는 시간"
              valueSec={form.lowerToleranceSec}
              maxSec={Math.min(MAX_TOLERANCE_SEC, form.timeLimitSec)}
              onChange={(sec) => patch({ lowerToleranceSec: sec })}
            />
            <div className="hidden w-px bg-line sm:block" aria-hidden="true" />
            <ToleranceField
              label="길게 허용"
              hint="목표보다 길게 발표해도 되는 시간"
              valueSec={form.upperToleranceSec}
              maxSec={MAX_TOLERANCE_SEC}
              onChange={(sec) => patch({ upperToleranceSec: sec })}
            />
          </div>

          <div className="mt-auto flex flex-wrap items-center justify-between gap-4 border-t border-line pt-5">
            <p className="flex items-baseline gap-4 text-sm">
              <span className="text-ink/80">허용 범위</span>
              <span className="tabular text-lg font-bold">
                {formatAllowedRange(
                  form.timeLimitSec,
                  form.lowerToleranceSec,
                  form.upperToleranceSec,
                )}
              </span>
            </p>
            <button
              type="button"
              disabled={!dirty || blocked !== undefined || save.isPending}
              onClick={() => {
                save.mutate();
              }}
              title={blocked}
              className="h-12 w-44 shrink-0 rounded-md bg-coral-deep text-base font-bold text-white hover:bg-ink disabled:cursor-not-allowed disabled:bg-line disabled:text-ink/80"
            >
              {saveLabel(save.isPending, dirty || blocked !== undefined) === '저장'
                ? '발표정보 저장'
                : saveLabel(save.isPending, dirty || blocked !== undefined)}
            </button>
          </div>
          {save.isError && (
            <p role="alert" className="-mt-4 text-right text-sm font-bold text-ink">
              발표정보를 저장하지 못했어요. {toMessage(save.error)}
            </p>
          )}
        </div>
      </fieldset>
    </div>
  );
}
