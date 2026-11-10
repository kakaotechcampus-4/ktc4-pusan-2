// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import {
  forgetPrivacyConsentsInTab,
  hasPrivacyConsent,
  PRIVACY_CONSENT_VERSION,
  privacyConsentKey,
  recordPrivacyConsent,
} from './privacyConsent';

beforeEach(() => {
  localStorage.clear();
  forgetPrivacyConsentsInTab();
});
afterEach(() => {
  vi.restoreAllMocks();
});

it('records consent per user in this browser', () => {
  expect(hasPrivacyConsent('u1')).toBe(false);

  recordPrivacyConsent('u1');

  expect(hasPrivacyConsent('u1')).toBe(true);
  expect(hasPrivacyConsent('u2')).toBe(false);
  expect(localStorage.getItem(privacyConsentKey('u1'))).not.toBeNull();
});

it('keeps the notice version in the key so a changed notice asks again', () => {
  expect(privacyConsentKey('u1')).toContain(`v${PRIVACY_CONSENT_VERSION}`);

  // 지난 버전 안내문에 동의한 기록은 지금 버전의 동의가 아닙니다
  localStorage.setItem(`pitchcoach.privacyConsent.v${PRIVACY_CONSENT_VERSION - 1}.u1`, 'x');
  expect(hasPrivacyConsent('u1')).toBe(false);
});

it('treats unreadable storage as not consented', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('blocked');
  });

  expect(hasPrivacyConsent('u1')).toBe(false);
});

it('remembers consent in this tab when storage cannot be written', () => {
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('blocked');
  });

  recordPrivacyConsent('u1');

  expect(hasPrivacyConsent('u1')).toBe(true);
});
