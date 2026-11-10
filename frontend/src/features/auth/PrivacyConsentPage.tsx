import { Link } from 'react-router';
import { Button } from '@/shared/ui/Button';
import { PitchCoachWordmark } from '@/shared/ui/PitchCoachWordmark';
import { recordPrivacyConsent } from '@/shared/lib/privacyConsent';
import { useLogout } from './useSession';

/** 안내문을 고치면 `PRIVACY_CONSENT_VERSION`(privacyConsent.ts)을 올립니다 */
const DATA_USES = [
  ['얼굴·시선', '기기 내에서만 분석하며, 영상과 생체정보는 서버로 전송하거나 저장하지 않습니다.'],
  ['음성', 'Deepgram STT로 텍스트 변환하며, 서비스 서버에 녹음 파일은 저장하지 않습니다.'],
  [
    '분석 정보',
    '발표 분석 결과, 변환된 텍스트, 오류 로그 등은 기능 검증 및 개선을 위해 2026년 12월 31일까지 보관합니다.',
  ],
] as const;

/**
 * 로그인한 사용자가 서비스를 쓰기 전에 한 번 보는 동의 화면입니다. `RequireSession` 이 동의 전에는 화면 대신 이것을 그립니다.
 * 동의하면 원래 가려던 화면이 그대로 열립니다(주소를 옮기지 않음).
 * 로그인 화면과 같은 머리 · 테두리 · 그림자를 씁니다.
 */
export function PrivacyConsentPage({ userId }: { userId: string }) {
  const signOut = useLogout();

  return (
    <div className="flex min-h-dvh flex-col bg-panel text-ink">
      <header className="border-b border-line-strong">
        <div className="mx-auto flex max-w-page items-center px-6 py-4 lg:px-10">
          <PitchCoachWordmark />
        </div>
      </header>

      <main className="flex flex-1 items-center justify-center px-6 py-12">
        <section
          aria-labelledby="privacy-consent-title"
          className="w-full max-w-xl border-2 border-ink bg-panel px-4 py-10 shadow-[4px_4px_0_var(--color-ink)] [overflow-wrap:anywhere] sm:px-10"
        >
          <h1 id="privacy-consent-title" className="text-2xl font-extrabold tracking-tight">
            개인정보 처리 및 데이터 활용
          </h1>

          <dl className="mt-6 flex flex-col gap-4 text-sm leading-6">
            {DATA_USES.map(([label, text]) => (
              <div key={label} className="border-l-2 border-ink pl-3">
                <dt className="font-bold">{label}</dt>
                <dd>{text}</dd>
              </div>
            ))}
          </dl>

          <p className="mt-6 border border-line-strong bg-cream px-4 py-3 text-sm leading-6">
            현재 사용자 테스트 단계로, 오류 확인과 기능 개선을 위한 최소한의 분석 정보 수집이
            필수입니다. 관련 내용을 안내하고 동의를 하신 후 서비스를 이용할 수 있습니다.
          </p>

          <p className="mt-4 text-sm">
            <Link
              to="/privacy"
              target="_blank"
              rel="noreferrer"
              className="font-bold underline underline-offset-4"
            >
              개인정보 처리방침 ↗
            </Link>
          </p>

          <div className="mt-8 flex flex-wrap items-center gap-3">
            <Button onClick={() => recordPrivacyConsent(userId)}>동의하고 시작하기</Button>
            <Button variant="quiet" disabled={signOut.isPending} onClick={() => signOut.mutate()}>
              {signOut.isPending ? '로그아웃 중…' : '동의하지 않고 로그아웃'}
            </Button>
          </div>
          {signOut.isError && (
            <p role="alert" className="mt-3 text-sm">
              로그아웃에 실패했습니다. 다시 시도해 주세요.
            </p>
          )}
        </section>
      </main>
    </div>
  );
}
