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
  formatAllowedRange,
} from '../lib/draft';
import { PaneHeading } from './PaneHeading';

function saveLabel(pending: boolean, unsaved: boolean): string {
  if (pending) return '저장하는 중…';
  if (unsaved) return '발표정보 저장';
  return '저장됨';
}

function TimeField({
  label,
  unit,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  unit: string;
  value: number | null;
  min: number;
  max: number;
  onChange: (value: number | null) => void;
}) {
  const id = useId();
  const invalid =
    value === null ||
    !Number.isFinite(value) ||
    value < min ||
    value > max ||
    (unit === '초' && !Number.isInteger(value));
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <label htmlFor={id} className="text-sm font-bold">
        {label}
      </label>
      <div className="flex items-center gap-2">
        <input
          id={id}
          type="number"
          min={min}
          max={max}
          step={unit === '초' ? 1 : 'any'}
          required
          value={value ?? ''}
          aria-invalid={invalid || undefined}
          aria-describedby={invalid ? `${id}-error` : undefined}
          onChange={(event) =>
            onChange(
              Number.isFinite(event.target.valueAsNumber) ? event.target.valueAsNumber : null,
            )
          }
          className="h-12 min-w-0 w-full border border-stone bg-white px-3 font-bold tabular-nums"
        />
        <span className="shrink-0 text-sm">{unit}</span>
      </div>
      {invalid && (
        <p id={`${id}-error`} className="text-xs">
          {min}~{max}
          {unit} 사이로 입력해 주세요.
        </p>
      )}
    </div>
  );
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
  const dirty = pitchId === null || !confirmed || infoChanged(saved, form);
  const valid = form.title.trim() !== '' && infoReady(form);

  return (
    <div>
      <PaneHeading title="3. 발표정보" subtitle="발표 제목과 목표 시간을 확인해 주세요." />
      <fieldset disabled={save.isPending || uploading} className="flex min-w-0 flex-col gap-5">
        <div className="flex flex-col gap-2">
          <div className="flex items-baseline justify-between">
            <label htmlFor={`${id}-title`} className="text-sm font-bold">
              발표 제목
            </label>
            <span className="text-xs text-stone tabular-nums">
              {form.title.length} / {MAX_TITLE_CHARS}
            </span>
          </div>
          <input
            id={`${id}-title`}
            placeholder="발표 제목을 입력해 주세요"
            value={form.title}
            maxLength={MAX_TITLE_CHARS}
            onChange={(event) => patch({ title: event.target.value })}
            className="h-12 border border-stone bg-white px-3 text-base placeholder:text-stone"
          />
        </div>
        <div className="grid gap-6 min-[900px]:grid-cols-3">
          <TimeField
            label="목표 발표시간"
            unit="분"
            min={MIN_TIME_LIMIT_MIN}
            max={MAX_TIME_LIMIT_MIN}
            value={form.timeLimitSec === 0 ? null : form.timeLimitSec / 60}
            onChange={(minutes) => {
              const timeLimitSec = Math.round((minutes ?? 0) * 60);
              patch({
                timeLimitSec,
                lowerToleranceSec:
                  form.lowerToleranceSec === null
                    ? null
                    : Math.min(form.lowerToleranceSec, Math.max(0, timeLimitSec)),
              });
            }}
          />
          <TimeField
            label="짧게 허용"
            unit="초"
            min={0}
            max={Math.min(MAX_TOLERANCE_SEC, form.timeLimitSec)}
            value={form.lowerToleranceSec}
            onChange={(lowerToleranceSec) => patch({ lowerToleranceSec })}
          />
          <TimeField
            label="길게 허용"
            unit="초"
            min={0}
            max={MAX_TOLERANCE_SEC}
            value={form.upperToleranceSec}
            onChange={(upperToleranceSec) => patch({ upperToleranceSec })}
          />
        </div>
        <div className="flex flex-wrap items-center gap-4">
          <button
            type="button"
            disabled={!dirty || !valid || save.isPending}
            onClick={() => save.mutate()}
            className="min-h-11 bg-coral-deep px-5 py-3 text-sm font-bold text-white hover:bg-ink disabled:cursor-not-allowed disabled:bg-line disabled:text-stone"
          >
            {saveLabel(save.isPending, dirty || !valid)}
          </button>
          {infoReady(form) && (
            <p className="text-sm text-stone">
              허용 범위{' '}
              {formatAllowedRange(
                form.timeLimitSec,
                form.lowerToleranceSec,
                form.upperToleranceSec,
              )}
            </p>
          )}
        </div>
        {save.isError && (
          <p role="alert" className="text-sm font-bold">
            발표정보를 저장하지 못했어요. {toMessage(save.error)}
          </p>
        )}
      </fieldset>
    </div>
  );
}
