import type { ZoneReference } from '@/workers/gaze.contract';

/**
 * 시선 보정 기준 — **이 탭의 메모리에만** 둡니다.
 *
 * 기준(`model`)에는 얼굴 측정값(얼굴 위치 · 크기, 홍채 크기, 카메라까지 거리)이 들어 있어
 * AI 시선 문서가 브라우저 저장소(IndexedDB · localStorage)에도 두지 말라고 합니다
 * (`docs/ai/features/gaze-tracking.md` 12-1 · 12-2). 장치 점검에서 리허설로는 새로고침 없이
 * 화면만 옮기므로, 모듈 변수 하나로 리허설 워커까지 넘어갑니다. 워커에는 메시지로 복사돼
 * 들어가므로 문서의 `restore`(구조 복제)와 같습니다.
 *
 * 새로고침하거나 탭을 닫으면 사라집니다. 리허설 중 새로고침이면 그 뒤 시선은 기록하지 않고,
 * 그 전까지 잰 1초 기록은 그대로 씁니다 (`gazeExclusion.ts` `CALIBRATION_LOST`).
 *
 * 한 탭에서 동시에 도는 Take 는 하나라 칸도 하나입니다. 새로 잡으면 덮어씁니다.
 */
let kept: { layoutSignature: string; engineVersion: string; ref: ZoneReference } | null = null;

/** 장치 점검이 잡은 기준을 둡니다. 키는 FE 가 붙입니다 (`layoutSignature.ts`) */
export function keepZoneRef(layoutSignature: string, ref: ZoneReference, engineVersion: string) {
  kept = { layoutSignature, engineVersion, ref };
}

/**
 * 이번 Take 의 기준을 꺼냅니다. 없으면 `null` 입니다.
 *
 * 화면 배치(`layoutSignature`)가 다르면 다른 기기 · 배치의 기준이라 쓰지 않습니다.
 * **엔진이 다르면 버립니다.** 다른 엔진의 기준을 넣으면 판정이 조용히 틀어집니다.
 */
export function takeZoneRef(layoutSignature: string, engineVersion: string): ZoneReference | null {
  if (!kept) return null;
  if (kept.layoutSignature !== layoutSignature || kept.engineVersion !== engineVersion) return null;
  return kept.ref;
}

/** 기준을 버립니다. 장치 점검에 들어올 때와 Take 를 끝낼 때 부릅니다 */
export function forgetZoneRef() {
  kept = null;
}
