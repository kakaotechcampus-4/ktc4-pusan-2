import type { PracticeMode, ScriptMode } from './api';

/**
 * Take 생성 — `POST /api/pitches/{pitch_id}/takes/`. BE 그대로 snake_case 입니다 (`TakeInitRequestDTO`).
 *
 * ★ BE 는 이번 연습의 **슬라이드 + 대본 조합을 Take 에 같이 박아 둡니다.**
 *   대본 버전 자체는 어느 슬라이드에 맞춘 것인지 모르므로, 그 연결이 남는 곳은 여기뿐입니다.
 * ★ 평가기준 버전은 받지 않습니다. 어떤 기준으로 채점했는지 남기려면 BE 에 필드 추가가 필요합니다.
 * ★ 멱등키가 없습니다. 같은 요청을 두 번 보내면 Take 가 두 개 생깁니다 — FE 가 시작 버튼을 한 번만 받습니다.
 */
export interface TakeCreateRequest {
  mode: PracticeMode;
  script_mode: ScriptMode;
  presentation_version_id: string;
  script_version_id: string;
  /** 목표 발표시간(초) — 피치의 time_limit_sec */
  goal_time_sec: number;
}

/** 컨트롤러가 `{"message", "take_id"}` 만 돌려줍니다. Take 번호 · 상태는 오지 않습니다 */
export interface TakeCreated {
  message: string;
  take_id: string;
}
