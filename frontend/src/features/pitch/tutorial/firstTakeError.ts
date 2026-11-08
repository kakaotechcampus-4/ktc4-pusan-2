/** 화면에 공개해도 안전한 튜토리얼 복구 안내만 이 오류로 전달합니다. */
export class FirstTakeError extends Error {
  readonly pitchId?: string;

  constructor(message: string, pitchId?: string) {
    super(message);
    this.name = 'FirstTakeError';
    this.pitchId = pitchId;
  }
}
