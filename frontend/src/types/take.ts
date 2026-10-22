import type { PracticeMode, ScriptMode } from './api';

/**
 * Take 생성 — `POST /api/pitches/{pitch_id}/takes/`. BE 그대로 snake_case 입니다 (`TakeInitRequestDTO`).
 *
 * ★ BE 는 이번 연습의 **슬라이드 + 대본 조합을 Take 에 같이 박아 둡니다.**
 *   대본 버전 자체는 어느 슬라이드에 맞춘 것인지 모르므로, 그 연결이 남는 곳은 여기뿐입니다.
 * ★ 평가기준 버전은 받지 않습니다. 어떤 기준으로 채점했는지 남기려면 BE 에 필드 추가가 필요합니다.
 * ★ 목표 시간은 보내지 않습니다. BE 가 발표 제한 시간으로 채웁니다 (#296).
 * ★ 대본 장 수와 발표자료 쪽수가 다르면 `409 SLIDE_COUNT_MISMATCH`, 나누기가 안 끝난 대본이면
 *   `409 SCRIPT_NOT_PARSED` 입니다.
 */
export interface TakeCreateRequest {
  mode: PracticeMode;
  script_mode: ScriptMode;
  presentation_version_id: string;
  script_version_id: string;
  /**
   * 멱등 키 — 장치 점검이 Take 보다 먼저 만드는 연습 세션 id(`startSession()`).
   * 응답을 잃고 같은 값으로 다시 보내면 BE 가 새로 만들지 않고 처음 Take 를 돌려줍니다.
   */
  client_session_id: string;
}

/**
 * 발표 전 점검 결과 — `POST /api/pitches/{pitch_id}/takes/{take_id}/calibration`.
 *
 * 앞의 넷은 BE `CalibrationDTO` 그대로이고, `result` 는 AI 시선 문서(`docs/ai/features/gaze-tracking.md`
 * 12-1)가 정한 `calibrations.result` 입니다 (BE `CalibrationResultDTO`).
 *
 * ★ 보정 모델(얼굴 측정값)은 보내지 않습니다. 요약 수치만 갑니다.
 * ★ `base_volume` 은 보내지 않습니다. 뜻이 다른 옛 칸(조용한 환경의 소음 기준)이고 AI 가 쓰지 않습니다
 *   (`docs/ai/features/volume.md` 12-1). 평소 목소리 크기는 `base_level_db` 입니다 (BE `CalibrationDTO.base_level_db`).
 */
export interface CalibrationRequest {
  face_detected: boolean;
  mic_detected: boolean;
  /** 보정이 OK 면 `loo_accuracy`, 아니면 0 (AI 문서 추천) */
  gaze_confidence: number;
  /**
   * 평소 목소리 크기 — 말한 구간의 Leq(dBFS, A 가중 · Fast, 음수). 발표 전 점검에서 정해진 문장을 읽게 해
   * 잽니다. 안 쟀거나 말한 시간이 5초 미만이면 null 이고, AI 가 Take 첫 발화로 잡습니다 (`volume.md` 5-2)
   */
  base_level_db: number | null;
  result: {
    engine_version: string;
    status: 'OK' | 'RETRY_REQUIRED';
    separability: number | null;
    loo_accuracy: number | null;
    warnings: string[];
  };
}

/** Take 생성 응답. `take_number` 는 BE 가 실제로 매긴 번호입니다 (#296) */
export interface TakeCreated {
  message: string;
  take_id: string;
  take_number: number;
}

/**
 * 발표 종료 — `PUT /api/pitches/{pitch_id}/takes/{take_id}` (`TakeUpdateRequestDTO`).
 * BE 는 **보낸 필드만** 덮어씁니다 (`exclude_unset`). 같은 값을 다시 보내도 결과가 같습니다.
 *
 * ★ 시선 · 슬라이드 기록은 여기로 보내지 않습니다. 계약(`docs/ai/architecture.md` 1-4)에서
 *   발표 중 WS `metrics` 로 1초마다 가는 값입니다 — 그 전송이 붙기 전까지는 서버로 가지 않습니다.
 * ★ `event_logs` 는 BE 와 담을 것을 정한 뒤 보냅니다 (FE 화면 이벤트 기록용, 같은 문서 1-1).
 *   보내지 않으면 BE 는 그 칸을 건드리지 않습니다.
 */
export interface TakeEndRequest {
  /** ISO 8601. 리허설 세션을 연 시각 */
  started_at: string;
  /** ISO 8601. 끝내기를 누른 순간 */
  ended_at: string;
}

/** 컨트롤러가 `{"message", "take_id"}` 만 돌려줍니다 */
export interface TakeUpdated {
  message: string;
  take_id: string;
}
