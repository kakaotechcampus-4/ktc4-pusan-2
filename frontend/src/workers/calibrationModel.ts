import { CONFIG_HASH } from '@/vendor/gaze/engine/config';

/**
 * 저장해 둔 보정 결과(`ZoneReference.model`)를 지금 엔진에 넣어도 되는지.
 *
 * ── 왜 버전 문자열만으로는 부족한가 ────────────────────────────────
 * 저장 키에 쓰는 엔진 버전(`gaze_v1.1.0+head_pose+reference_anchor_v1`)은 **출력의 뜻**이
 * 바뀔 때만 오릅니다. 임계값 같은 설정은 버전을 그대로 둔 채 바뀔 수 있고, 엔진의 `calibrate()` 는
 * 모델의 모양(schema)만 보고 설정은 확인하지 않습니다. 그래서 설정이 바뀐 엔진에 옛 보정이
 * 조용히 들어가 판정 기준이 어긋납니다.
 *
 * 엔진은 보정을 만들 때 그때의 설정 해시를 `configHash` 로 모델에 붙여 둡니다
 * (`vendor/gaze/engine/engine.ts`). 그 값이 지금 설정과 같을 때만 씁니다.
 * 다르면 저장된 기준이 없는 것으로 보고 다시 보정을 받습니다.
 *
 * `config.ts` 만 불러옵니다 — 엔진 진입점(`index.ts`)을 부르면 MediaPipe 까지 메인 번들에 딸려 옵니다.
 */
export function fitsCurrentEngine(model: unknown): boolean {
  if (typeof model !== 'object' || model === null) return false;
  return (model as { configHash?: unknown }).configHash === CONFIG_HASH;
}
