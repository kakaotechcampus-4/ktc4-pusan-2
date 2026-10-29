import { ButtonLink } from '@/shared/ui/Button';
import { Card } from '@/shared/ui/Panel';
import { Mascot } from '@/shared/ui/Mascot';
import { InfoPage } from '@/shared/ui/InfoPage';

/** 404 찾을 수 없음 — 시안 `docs/fe/design` not-found */
export function NotFoundPage() {
  return (
    <InfoPage title="찾을 수 없음">
      <Card>
        <p className="font-mono text-5xl font-bold">404</p>
        <Mascot who="chichi">어라, 여기는 아무것도 없어. 길을 잃었나 봐!</Mascot>
        <div>
          <ButtonLink to="/">내 피치로</ButtonLink>
        </div>
      </Card>
    </InfoPage>
  );
}
