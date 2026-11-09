import { useEffect, useLayoutEffect, useRef } from 'react';
import type { ChatScreen } from '@/types/chat';
import { onSignedOut } from './sessionSignal';

/**
 * 화면 상태 (`docs/ai/features/chat-agent.md` 12-4 F2 "화면 상태 `Screen`") — 피치 만들기 · 수정 · 장치 점검처럼
 * 저장 전 입력이 화면 안에만 있는 화면이, 지금 상태를 대화창에 알리는 곳입니다.
 *
 * - 화면은 `useChatScreen(경로, 읽기)` 한 줄만 더합니다. 화면 동작 · 저장 흐름은 바꾸지 않습니다.
 * - 등록에는 경로 · 등록 번호를 붙이고, 화면이 사라지면 풉니다. 로그아웃 신호(이 탭 · 다른 탭)가 오거나, 신호 없이
 *   로그인 사용자가 바뀌면(대화 저장소 `setUser` 가 `clearChatScreen` 을 부릅니다) 등록을 비웁니다 —
 *   앞 사람 때 등록한 화면 상태를 새 사람의 질문에 싣지 않게(12-4 L3-09 "세션 세대"를 이것으로 지킵니다).
 * - `shared/` 에 둡니다 — 화면(피치 만들기 · 장치 점검)과 대화(`features/chat`)가 함께 쓰는데 feature 끼리는
 *   서로 가져다 쓰지 않습니다(FRONTEND_CONVENTION.md 5).
 * - 보내는 순간(`takeScreenSnapshot`)에만 읽고, 그때 `snapshot_id` 를 발급합니다 — SCREEN 근거가 이 값을 가리킵니다.
 * - 명시한 칸만 싣고 칸마다 모양 · null 을 검사합니다. 검사에 걸리거나 읽다가 실패하면 그 메시지를 보내지 않게 알립니다
 *   (12-4 L3-10 "상태를 만들 수 없으면 전송 차단"). 화면이 `null` 을 돌려주면 알릴 상태가 없다는 뜻이라 화면 상태 없이 보냅니다.
 * - 평가 기준 원문이 2,000자(코드 포인트)를 넘으면 잘라 보내지 않고, 그 메시지를 보내지 않게 알립니다.
 */

/** `snapshot_id` 를 뺀 화면 상태 — 화면이 만드는 값 */
export type ScreenBody = ChatScreen extends infer S
  ? S extends ChatScreen
    ? Omit<S, 'snapshot_id'>
    : never
  : never;

/** 평가 기준 원문 상한 — 서버와 같은 기준(Python `len`, 코드 포인트 수)으로 셉니다 */
export const MAX_SCREEN_CRITERIA_CHARS = 2000;

export type ScreenSnapshot =
  /** 실을 화면 상태 — 등록된 화면이 없거나 · 다른 경로 · 로그아웃 뒤 · 화면이 null 을 주면 null(화면 상태 없이 보냄) */
  | { ok: true; screen: ChatScreen | null }
  /**
   * 보내지 않습니다
   * - `CRITERIA_TOO_LONG`: 평가 기준 원문이 넘칩니다 (12-4 "평가 기준 원문이 2,000자를 넘으면 … 그 메시지는 보내지 않는다")
   * - `SCREEN_UNAVAILABLE`: 읽다가 실패했거나 계약 모양이 아닙니다 (12-4 L3-10 "상태를 만들 수 없으면 전송 차단")
   */
  | { ok: false; reason: 'CRITERIA_TOO_LONG' | 'SCREEN_UNAVAILABLE' };

interface Registration {
  id: number;
  route: string;
  read: () => ScreenBody | null;
}

let seq = 0;
let current: Registration | null = null;
/** 로그아웃 신호를 듣고 있나 — 처음 등록할 때부터 듣습니다(모듈을 읽기만 해서는 듣지 않게) */
let listening = false;
const listen = () => {
  if (listening) return;
  listening = true;
  onSignedOut(clearChatScreen);
};

/**
 * 등록을 비웁니다 — 로그아웃 신호와, 신호 없이 사용자가 바뀔 때(대화 저장소 `setUser`) 부릅니다.
 * `shared/` 는 `features/chat` 을 가져다 쓰지 않아서, 사용자가 바뀐 것을 아는 저장소 쪽에서 부르게 했습니다 (12-4 L3-09)
 */
export function clearChatScreen(): void {
  current = null;
}

/** 화면을 등록합니다 — 돌려준 함수로 풉니다. 나중에 등록한 화면이 앞 화면을 대신합니다 */
export function registerChatScreen(route: string, read: () => ScreenBody | null): () => void {
  const id = ++seq;
  listen();
  current = { id, route, read };
  return () => {
    if (current?.id === id) current = null;
  };
}

/**
 * 화면이 지금 상태를 대화창에 알립니다. `read` 는 보내는 순간에 불리고, 늘 마지막으로 그린 때의 함수를 씁니다.
 * `route` 는 `app/routes.tsx` 의 경로 패턴(`ChatPage.route` 와 같은 값)입니다.
 */
export function useChatScreen(route: string, read: () => ScreenBody | null): void {
  const latest = useRef(read);
  useLayoutEffect(() => {
    latest.current = read;
  });
  useEffect(() => {
    return registerChatScreen(route, () => latest.current());
  }, [route]);
}

/** 보내는 순간의 화면 상태 — 지금 경로의 화면이 등록돼 있을 때만 읽습니다 */
export function takeScreenSnapshot(route: string): ScreenSnapshot {
  const reg = current;
  if (!reg || reg.route !== route) return { ok: true, screen: null };
  let body: ScreenBody | null;
  try {
    body = reg.read();
  } catch (error) {
    // 상태를 만들 수 없으면 보내지 않습니다 — 화면을 모른 채 답하지 않게 (12-4 L3-10)
    console.error('화면 상태를 읽지 못했어요', error);
    return { ok: false, reason: 'SCREEN_UNAVAILABLE' };
  }
  if (body === null) return { ok: true, screen: null };
  if (!isScreenBody(body)) {
    // 화면 쪽 실수라 개발 중에 알아채게 남깁니다 — 내용은 사용자 입력이라 남기지 않고 어느 화면인지만
    console.warn('화면 상태가 계약 모양이 아니라 보내지 않았어요', { route: reg.route });
    return { ok: false, reason: 'SCREEN_UNAVAILABLE' };
  }
  if (
    body.name !== 'device_check' &&
    body.draft.criteria_text !== null &&
    [...body.draft.criteria_text].length > MAX_SCREEN_CRITERIA_CHARS
  )
    return { ok: false, reason: 'CRITERIA_TOO_LONG' };
  return { ok: true, screen: { ...body, snapshot_id: newSnapshotId() } as ChatScreen };
}

/** 시험용 — 등록을 비웁니다 */
export function resetChatScreen(): void {
  clearChatScreen();
}

function newSnapshotId(): string {
  const bytes = new Uint8Array(8);
  globalThis.crypto.getRandomValues(bytes);
  return `ss-${Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')}`;
}

// ── 검사 — 명시한 칸만, 칸마다 모양 · null ─────────────────────────────

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);
const hasOnly = (v: Obj, keys: readonly string[]) =>
  Object.keys(v).every((k) => keys.includes(k)) && keys.every((k) => k in v);
const strOrNull = (v: unknown) => v === null || typeof v === 'string';
const countOrNull = (v: unknown) => v === null || (Number.isInteger(v) && (v as number) >= 0);
const oneOf =
  <T extends string>(list: readonly T[]) =>
  (v: unknown): v is T =>
    typeof v === 'string' && (list as readonly string[]).includes(v);

const isStep = oneOf(['slides', 'script', 'info', 'criteria'] as const);
const isChangedKey = oneOf(['title', 'time_limit_sec', 'criteria_text'] as const);
const isCamera = oneOf(['connected', 'disconnected'] as const);
const isMic = oneOf(['detected', 'checking', 'disconnected'] as const);
const isGaze = oneOf(['LOADING', 'UNAVAILABLE', 'IDLE', 'RUNNING', 'DONE', 'FAILED'] as const);

const SAVED_KEYS = ['title', 'time_limit_sec', 'criteria_text', 'script_chars'] as const;
const DRAFT_KEYS = [
  'title',
  'slide_count',
  'script_chars',
  'time_limit_sec',
  'criteria_text',
] as const;

/** 화면이 만든 값이 계약(12-4 표 · AI `PitchScreen` · `DeviceScreen`) 모양인지 */
export function isScreenBody(v: unknown): v is ScreenBody {
  if (!isObj(v)) return false;
  if (v.name === 'device_check')
    return (
      hasOnly(v, ['name', 'camera', 'mic', 'gaze_setup', 'gaze_reason']) &&
      isCamera(v.camera) &&
      isMic(v.mic) &&
      isGaze(v.gaze_setup) &&
      strOrNull(v.gaze_reason)
    );
  if (v.name !== 'pitch_create' && v.name !== 'pitch_edit') return false;
  if (
    !hasOnly(v, ['name', 'step', 'version_label', 'version_id', 'saved', 'draft', 'changed']) ||
    !isStep(v.step) ||
    !strOrNull(v.version_label) ||
    !strOrNull(v.version_id)
  )
    return false;
  const saved = v.saved;
  if (
    saved !== null &&
    !(
      isObj(saved) &&
      hasOnly(saved, SAVED_KEYS) &&
      strOrNull(saved.title) &&
      countOrNull(saved.time_limit_sec) &&
      strOrNull(saved.criteria_text) &&
      countOrNull(saved.script_chars)
    )
  )
    return false;
  const draft = v.draft;
  if (
    !isObj(draft) ||
    !hasOnly(draft, DRAFT_KEYS) ||
    !strOrNull(draft.title) ||
    !countOrNull(draft.slide_count) ||
    !countOrNull(draft.script_chars) ||
    !countOrNull(draft.time_limit_sec) ||
    !strOrNull(draft.criteria_text)
  )
    return false;
  return (
    Array.isArray(v.changed) &&
    v.changed.every(isChangedKey) &&
    new Set(v.changed).size === v.changed.length
  );
}
