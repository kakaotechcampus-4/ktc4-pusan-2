/** #540 FE preview only. This is not an agreed BE contract. */
export interface ServiceReviewRequest {
  rating: number;
}

export interface ServiceReviewReceipt {
  take_id: string;
  rating: number;
}
