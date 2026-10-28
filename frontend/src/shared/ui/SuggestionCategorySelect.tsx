import { useId } from 'react';
import { suggestionCategories } from '@/shared/lib/suggestionCategory';
import type { SuggestionCategory } from '@/types/serviceReview';

export function SuggestionCategorySelect({
  value,
  onChange,
  disabled,
}: {
  value: SuggestionCategory;
  onChange: (value: SuggestionCategory) => void;
  disabled: boolean;
}) {
  const id = useId();
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <label htmlFor={id} className="text-sm font-bold">
        의견 유형
      </label>
      <select
        id={id}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value as SuggestionCategory)}
        className="min-h-11 w-full min-w-0 border-2 border-ink bg-white p-3 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink disabled:opacity-60"
      >
        {Object.entries(suggestionCategories).map(([category, label]) => (
          <option key={category} value={category}>
            {label}
          </option>
        ))}
      </select>
    </div>
  );
}
