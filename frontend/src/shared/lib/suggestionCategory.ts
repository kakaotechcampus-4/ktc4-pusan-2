import type { SuggestionCategory } from '@/types/serviceReview';

export const suggestionCategories = {
  POSITIVE: '좋았던 점',
  IMPROVEMENT: '개선할 점',
  PROBLEM: '문제가 있었던 점',
  QUESTION: '질문',
  OTHER: '기타',
} satisfies Record<SuggestionCategory, string>;
