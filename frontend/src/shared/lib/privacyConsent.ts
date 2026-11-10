import { useSyncExternalStore } from 'react';

/**
 * 개인정보 처리 · 데이터 활용 동의 — 로그인한 사용자가 서비스를 쓰기 전에 한 번 받습니다.
 *
 * (BE 미구현) 동의 기록을 둘 BE 칸이 없어 사용자별로 이 브라우저(localStorage)에 둡니다.
 * 그래서 다른 기기 · 브라우저에서는 한 번 더 묻고, 서버에는 동의 기록이 남지 않습니다.
 * BE 칸이 생기면 이 파일의 읽기 · 쓰기만 바꿉니다.
 *
 * 안내문(`PrivacyConsentDialog`)을 고치면 `PRIVACY_CONSENT_VERSION` 을 올립니다 — 바뀐 안내문으로 다시 묻습니다.
 */
export const PRIVACY_CONSENT_VERSION = 1;

const KEY_PREFIX = 'pitchcoach.privacyConsent.';

export const privacyConsentKey = (userId: string) =>
  `${KEY_PREFIX}v${PRIVACY_CONSENT_VERSION}.${userId}`;

/**
 * 저장소를 못 쓰는 브라우저(막힌 저장소 등)에서도 동의한 탭에서는 계속 쓸 수 있게 탭 메모리에도 둡니다.
 * 새로고침하면 다시 묻습니다 — 동의 없이 지나가는 것보다 낫습니다.
 */
const consentedInTab = new Set<string>();
const listeners = new Set<() => void>();

export function hasPrivacyConsent(userId: string): boolean {
  if (consentedInTab.has(userId)) return true;
  try {
    return localStorage.getItem(privacyConsentKey(userId)) !== null;
  } catch {
    return false;
  }
}

export function recordPrivacyConsent(userId: string): void {
  consentedInTab.add(userId);
  try {
    localStorage.setItem(privacyConsentKey(userId), new Date().toISOString());
  } catch {
    // 못 적으면 이 탭에서만 기억합니다
  }
  for (const listener of listeners) listener();
}

/** 시험에서 탭 메모리를 비웁니다 */
export function forgetPrivacyConsentsInTab(): void {
  consentedInTab.clear();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  // 다른 탭에서 동의하면 이 탭도 바로 넘어갑니다
  const onStorage = (event: StorageEvent) => {
    if (event.key === null || event.key.startsWith(KEY_PREFIX)) listener();
  };
  window.addEventListener('storage', onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener('storage', onStorage);
  };
}

/** 로그인한 사용자가 동의했는지. 사용자를 아직 모르면 false 입니다 */
export function usePrivacyConsent(userId: string | null): boolean {
  return useSyncExternalStore(subscribe, () => userId !== null && hasPrivacyConsent(userId));
}
