import type { MeasuredWith } from '../media/level';

/**
 * 기준 목소리(`base_level_db`) — **이 탭의 메모리에만** 둡니다 (#1179).
 *
 * 목소리 크기는 장치 점검에서 잰 기준과의 차이로 판정합니다. 연속으로 연습할 때 Take 마다 다시 읽게 하면
 * 부담이 크고, 읽을 때마다 목소리가 조금씩 달라 기준도 흔들립니다. 그래서 **같은 환경이면** 직전에 잰 값을
 * 다시 씁니다. 같은 환경은 아래를 모두 만족할 때입니다.
 *   - 같은 탭 (새로고침 · 탭 닫기면 사라집니다)
 *   - 크기를 실제로 잰 트랙(`MeasuredWith`)이 같은 `deviceId`
 *   - 그 트랙의 자동 음량 조절이 꺼짐 — 켜져 있으면 브라우저가 크기를 바꿔 기준이 맞지 않습니다
 *   - 처음 잰 시각부터 `BASE_VOICE_REUSE_MS` 안. 다시 써도 시각을 갱신하지 않습니다
 * `deviceId` 나 자동 음량 조절 상태를 모르면 저장도 재사용도 하지 않습니다. 피치가 달라도 조건이 맞으면 씁니다.
 *
 * **장치 별칭은 같은 마이크로 보지 않습니다** (#1184 리뷰). 크롬은 장치를 고르지 않고 연 마이크에 `default` ·
 * `communications` 같은 별칭을 줄 수 있어, Take 사이에 OS 기본 마이크를 바꿔도 id 가 같습니다. 그래서 별칭이면
 * 저장 · 재사용하지 않고, 실제 기기 묶음(`groupId`)까지 같아야 같은 마이크로 봅니다. 브라우저가 실제로 어떤 id 를
 * 주는지는 실기기 확인이 남아 있어, 그때까지는 막는 쪽입니다.
 *
 * 시선 기준(`zoneRefMemory.ts`)을 지우는 흐름(장치 점검 진입 · Take 끝)에서는 지우지 않습니다 — 그래야
 * 다음 Take 에서 다시 쓸 수 있습니다. 탈출구("발표 처음 목소리를 기준으로")로 시작한 Take 는 넣지 않습니다.
 */

/** 다시 쓸 수 있는 시간. 첫 값이고 AI 측정 실험(#1182)에 따라 바뀔 수 있습니다 */
export const BASE_VOICE_REUSE_MS = 30 * 60_000;

let kept: { levelDb: number; deviceId: string; groupId: string; measuredAt: number } | null = null;

/** 실제 기기를 가리키지 않는 장치 id (크롬) */
const DEVICE_ALIASES = new Set(['default', 'communications']);

/** 저장 · 재사용할 수 있는 측정인가 — 실제 장치를 알고, 자동 음량 조절이 꺼졌다고 확인된 때만입니다 */
function knownSource(source: MeasuredWith | null): source is MeasuredWith {
  return (
    source !== null &&
    source.deviceId !== '' &&
    !DEVICE_ALIASES.has(source.deviceId) &&
    source.autoGainOff === true
  );
}

/** 이번에 잰 기준 목소리를 둡니다. 조건이 안 맞으면 두지 않습니다 (앞에 둔 값은 그대로) */
export function keepBaseVoice(levelDb: number, source: MeasuredWith | null, now: number) {
  if (!knownSource(source)) return;
  kept = { levelDb, deviceId: source.deviceId, groupId: source.groupId, measuredAt: now };
}

export interface RememberedBaseVoice {
  levelDb: number;
  /** 처음 잰 시각 (`performance.now()`) */
  measuredAt: number;
}

/** 지금 마이크로 다시 쓸 수 있는 기준 목소리. 없거나 조건이 안 맞으면 null */
export function recallBaseVoice(
  source: MeasuredWith | null,
  now: number,
): RememberedBaseVoice | null {
  if (!kept || !knownSource(source)) return null;
  if (source.deviceId !== kept.deviceId || source.groupId !== kept.groupId) return null;
  // `now` 가 잰 시각보다 앞일 수 있습니다 — 화면은 시계를 간격을 두고 읽습니다. 그때는 방금 잰 것입니다
  if (now - kept.measuredAt >= BASE_VOICE_REUSE_MS) return null;
  return { levelDb: kept.levelDb, measuredAt: kept.measuredAt };
}

/** 시험에서 칸을 비웁니다. 화면 흐름에서는 부르지 않습니다 — 새로고침 · 탭 닫기로만 사라집니다 */
export function forgetBaseVoice() {
  kept = null;
}
