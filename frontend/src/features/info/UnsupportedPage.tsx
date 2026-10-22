import { useState } from 'react';
import { Button, ButtonLink } from '@/shared/ui/Button';
import { Card } from '@/shared/ui/Panel';
import { checkGazeSupport } from '@/shared/lib/gazeSupport';
import { InfoPage } from './InfoPage';
import { unsupportedReason, type UnsupportedReason } from './unsupportedReason';

/**
 * P18 미지원 브라우저 — 시안 `/dev/design/unsupported`.
 *
 * 왜 왔는지는 이 화면이 직접 봅니다. 아직 이 화면으로 보내는 곳이 없고(언제 보낼지는 시안의
 * 남은 질문), 보내는 쪽마다 사유를 실어 주지 않아도 되게 하려는 것입니다.
 * 시안의 '보안 설정' 사유는 뺐습니다 — `crossOriginIsolated` 가 없을 때 막는 코드가 없습니다.
 */

const COPY: Record<UnsupportedReason, { title: string; body: string }> = {
  BROWSER: {
    title: '이 브라우저에서는 시선 분석을 할 수 없어요',
    body: '시선 분석에 필요한 기능(WebAssembly · Worker · createImageBitmap)이 없어요. 최신 Chrome이나 Edge로 열어 주세요.',
  },
  MOBILE: {
    title: '발표 연습은 PC에서 해 주세요',
    body: '슬라이드 · 대본 · 카메라를 한 화면에 띄워야 해서 화면이 넓어야 해요. 피치 확인과 리포트는 휴대폰에서도 볼 수 있어요.',
  },
  SUPPORTED: {
    title: '이 브라우저에서는 연습할 수 있어요',
    body: '시선 분석에 필요한 기능이 모두 있어요. 다른 기기에서 열 때는 아래 브라우저를 써 주세요.',
  },
};

function detect(): UnsupportedReason {
  const touchOnly = window.matchMedia('(pointer: coarse)').matches;
  return unsupportedReason(checkGazeSupport().ok, touchOnly);
}

export function UnsupportedPage() {
  const [reason] = useState(detect);
  const [copied, setCopied] = useState<'OK' | 'FAILED' | null>(null);
  const copy = COPY[reason];

  const copyLink = () => {
    // http 로 연 주소에는 clipboard 가 없어서 동기로 터집니다. then 안에서 불러 catch 로 받습니다
    Promise.resolve()
      .then(() => navigator.clipboard.writeText(window.location.origin))
      .then(() => setCopied('OK'))
      .catch(() => setCopied('FAILED'));
  };

  return (
    <InfoPage title="미지원 브라우저">
      <Card>
        <h2 className="text-2xl font-bold">{copy.title}</h2>
        <p className="leading-relaxed text-stone">{copy.body}</p>
        <ul className="list-disc space-y-1 pl-5 text-sm">
          <li>Chrome 최신 버전 (PC)</li>
          <li>Edge 최신 버전 (PC)</li>
        </ul>
        <div className="flex flex-wrap gap-3">
          <Button onClick={copyLink}>링크 복사</Button>
          <ButtonLink to="/" variant="secondary">
            내 피치로
          </ButtonLink>
        </div>
        <p role="status" className="text-sm text-stone">
          {copied === 'OK' && '복사했어요. PC의 Chrome이나 Edge 주소창에 붙여 넣어 주세요.'}
          {copied === 'FAILED' &&
            `복사하지 못했어요. 주소 ${window.location.origin} 를 직접 옮겨 주세요.`}
        </p>
      </Card>
    </InfoPage>
  );
}
