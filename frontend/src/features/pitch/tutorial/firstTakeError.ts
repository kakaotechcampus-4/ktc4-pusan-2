/** 화면에 공개해도 안전한 튜토리얼 복구 안내만 이 오류로 전달합니다. */
export class FirstTakeError extends Error {
  constructor(message: string, readonly pitchId?: string) {
    super(message);
    this.name = 'FirstTakeError';
  }
}
