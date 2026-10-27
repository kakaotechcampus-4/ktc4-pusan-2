export interface ServiceReviewRequest {
  rating: number;
  suggestion?: string | null;
}

export interface ServiceReviewReceipt {
  submitted: true;
  rating: number;
  created_at: string;
}
export interface ServiceReviewState {
  has_report: boolean;
  submitted: boolean;
  display_count: number;
  display_limit: number;
  should_prompt: boolean;
}
export interface ServiceReviewImpression {
  should_open: boolean;
  submitted: boolean;
  display_count: number;
  display_limit: number;
  reason: 'ALREADY_SUBMITTED' | 'NO_REPORT' | 'DISPLAY_LIMIT_REACHED' | null;
}
export interface SuggestionRequest {
  request_id: string;
  content: string;
}
export interface SuggestionReceipt {
  id: string;
  source: 'MY_PAGE';
  created_at: string;
}
