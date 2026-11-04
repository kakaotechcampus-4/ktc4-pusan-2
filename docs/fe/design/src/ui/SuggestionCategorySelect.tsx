// 제품 `frontend/src/shared/ui/SuggestionCategorySelect.tsx` · `shared/lib/suggestionCategory.ts`에서 복사해 시작했다. 설계서가 관리한다.
import { useId } from 'react';

const suggestionCategories = {
  POSITIVE: '좋았던 점',
  IMPROVEMENT: '개선할 점',
  PROBLEM: '문제가 있었던 점',
  QUESTION: '질문',
  OTHER: '기타',
};
export type SuggestionCategory = keyof typeof suggestionCategories;

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
        className="min-h-11 w-full min-w-0 border-2 border-ink bg-white p-3 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink disabled:border-line-strong disabled:bg-cream"
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
