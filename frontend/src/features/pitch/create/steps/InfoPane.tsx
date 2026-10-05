import { useId } from 'react';
import { useMutation } from '@tanstack/react-query';
import { toMessage } from '@/shared/api/errorMessage';
import { createPitch, updatePitch } from '@/shared/api/pitch';
import type { PitchRequest } from '@/types/pitch';
import { useCreateStore } from '../createStore';
import { fromPitchSaved, toPitchRequest } from '../lib/beAdapter';
import { dotDate } from '../lib/date';
import {
  infoChanged,
  infoOf,
  MAX_TIME_LIMIT_MIN,
  MAX_TOLERANCE_SEC,
  MIN_TIME_LIMIT_MIN,
  TOLERANCE_STEP_SEC,
  formatAllowedRange,
  formatDuration,
} from '../lib/draft';
import { DatePicker } from './DatePicker';
import { PaneHeading } from './PaneHeading';

/**
 * 목업 발표정보 — 제목 · 날짜 · 목표 발표시간 · 하한/상한 허용오차.
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

function ToleranceField({
  label,
  hint,
  valueSec,
  maxSec,
  onChange,
}: {
  label: string;
  hint: string;
  valueSec: number;
  maxSec: number;
  onChange: (sec: number) => void;
}) {
  return (
    <div className="flex flex-1 flex-col gap-2">
      <p className="text-sm font-bold">{label}</p>
      <Stepper
        label={label}
        value={formatDuration(valueSec)}
        size="sm"
        canDecrease={valueSec > 0}
        canIncrease={valueSec + TOLERANCE_STEP_SEC <= maxSec}
        onDecrease={() => onChange(valueSec - TOLERANCE_STEP_SEC)}
        onIncrease={() => onChange(valueSec + TOLERANCE_STEP_SEC)}
      />
      <p className="text-xs text-stone">{hint}</p>
    </div>
  );
}

export function InfoPane() {
  const draft = useCreateStore((s) => s.draft);
  const edits = useCreateStore((s) => s.infoEdits);
  const patch = useCreateStore((s) => s.editInfo);
  const setMeta = useCreateStore((s) => s.setMeta);
  const pitchId = useCreateStore((s) => s.pitchId);
  const setPitchId = useCreateStore((s) => s.setPitchId);
  const id = useId();

  const saved = infoOf(draft);
  const form = edits ?? saved;

  const minutes = Math.round(form.timeLimitSec / 60);
  const setMinutes = (m: number) => {
    const limitSec = m * 60;
    // 목표가 줄면 하한도 함께 줄입니다 — "3분 발표에 하한 5분" 은 0초 밑으로 내려갑니다
    patch({
      timeLimitSec: limitSec,
      lowerToleranceSec: Math.min(form.lowerToleranceSec, limitSec),
    });
  };

  const save = useMutation({
    mutationFn: (body: PitchRequest) => (pitchId ? updatePitch(pitchId, body) : createPitch(body)),
    onSuccess: (res, body) => {
      setPitchId(fromPitchSaved(res));
      setMeta({ ...form, title: body.title });
    },
  });

  // 피치가 아직 없으면 고친 게 없어도 저장해야 합니다 — 기본값 그대로 만들 수도 있습니다
  const dirty = pitchId === null || infoChanged(saved, form);
  const titleMissing = form.title.trim() === '';

  return (
    <div className="flex flex-1 flex-col">
      <PaneHeading title="발표정보" subtitle="발표의 기본 정보를 설정해 주세요." />

      <div className="flex flex-col gap-6 rounded-lg border border-line bg-white p-7">
        <div className="flex flex-col gap-2">
          <label htmlFor={`${id}-title`} className="text-sm font-bold">
            발표 제목
          </label>
          <input
            id={`${id}-title`}
            placeholder="발표 제목을 입력해 주세요"
            value={form.title}
            onChange={(e) => patch({ title: e.target.value })}
            className="h-12 rounded-md border border-line-strong bg-white px-4 text-base outline-none placeholder:text-stone focus:border-ink"
          />
        </div>

        <div className="grid grid-cols-1 gap-8 lg:grid-cols-2 lg:gap-0">
          {/* ── 왼쪽: 발표 날짜 ── */}
          <div className="flex flex-col gap-3 lg:border-r lg:border-line lg:pr-8">
            <p className="text-sm font-bold">발표 날짜</p>
            <DatePicker
              value={form.presentationDate}
              onChange={(iso) => patch({ presentationDate: iso })}
            />
            <p className="flex items-baseline gap-4 text-sm">
              <span className="text-stone">선택한 날짜</span>
              <span className="tabular text-lg font-bold">
                {form.presentationDate ? dotDate(form.presentationDate) : '—'}
              </span>
            </p>
          </div>

          {/* ── 오른쪽: 발표시간 ── */}
          <div className="flex flex-col gap-8 lg:pl-8">
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

            <div className="flex gap-6">
              <ToleranceField
                label="하한 허용오차"
                hint="목표보다 짧게 발표해도 되는 시간"
                valueSec={form.lowerToleranceSec}
                maxSec={Math.min(MAX_TOLERANCE_SEC, form.timeLimitSec)}
                onChange={(sec) => patch({ lowerToleranceSec: sec })}
              />
              <div className="w-px bg-line" aria-hidden="true" />
              <ToleranceField
                label="상한 허용오차"
                hint="목표보다 길게 발표해도 되는 시간"
                valueSec={form.upperToleranceSec}
                maxSec={MAX_TOLERANCE_SEC}
                onChange={(sec) => patch({ upperToleranceSec: sec })}
              />
            </div>

            <div className="mt-auto flex items-center justify-between gap-4 border-t border-line pt-5">
              <p className="flex items-baseline gap-4 text-sm">
                <span className="text-stone">허용 범위</span>
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
                disabled={!dirty || titleMissing || save.isPending}
                onClick={() => save.mutate(toPitchRequest(form))}
                title={titleMissing ? '발표 제목을 먼저 입력해 주세요' : undefined}
                className="h-12 w-44 shrink-0 rounded-md bg-coral text-base font-bold text-white hover:bg-coral-deep disabled:cursor-not-allowed disabled:bg-line disabled:text-stone"
              >
                {saveLabel(save.isPending, dirty || titleMissing)}
              </button>
            </div>
            {save.isError && (
              <p role="alert" className="-mt-4 text-right text-sm font-bold text-coral">
                발표정보를 저장하지 못했어요. {toMessage(save.error)}
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
