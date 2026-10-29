import { useState } from 'react';
import { Card } from '@/ui/Panel';
import { Button } from '@/ui/Button';
import { DraftPage, Mascot } from '../ui';

/**
 * 글 위주 화면 시안 — 분석 방식 설명(P14) · 미지원 브라우저(P18) · 404. **시안 전용입니다.**
 */

/* ── P14 분석 방식 설명 ─────────────────────────────────────────── */

const ANALYSES = [
  {
    title: '시선',
    where: '이 브라우저 안에서만',
    body: '카메라 영상은 서버로 보내지 않아요. 브라우저가 1초마다 청중 · 화면 · 판정 불가 중 어디를 봤는지만 기록해요.',
  },
  {
    title: '말하기 속도 · 군더더기',
    where: '서버 (음성 인식)',
    body: '마이크 소리를 실시간으로 글자로 바꿔 분당 글자 수와 "음 · 어" 같은 말버릇을 세요. 녹음 파일은 남기지 않아요.',
  },
  {
    title: '소리 크기 · 침묵',
    where: '브라우저에서 재고 숫자만 보내요',
    body: '장치 점검에서 잰 평소 목소리와 비교해 작아지거나 길게 멈춘 구간을 찾아요.',
  },
  {
    title: '내용 전달',
    where: '서버 (AI)',
    body: '발표가 끝나면 대본의 핵심 내용과 숫자를 장마다 제대로 말했는지 확인해요.',
  },
  {
    title: '코치 총평 · 다음 미션',
    where: '서버 (AI)',
    body: '먼저 고칠 것, 지난 Take보다 나아진 것, 다음 Take에서 해 볼 것을 정해요. 숫자는 측정값 그대로 쓰고 지어내지 않아요.',
  },
];

export function AboutDraft() {
  return (
    <DraftPage
      title="분석 방식 설명"
      eyebrow="피치코치는 무엇을 어떻게 보나요?"
      questions={[
        '온보딩(사용방법)과 내용이 겹쳐요. 이 화면을 따로 둘지 정해야 해요.',
        '"판정 불가"가 왜 생기는지(조명 · 얼굴이 화면 밖) 더 자세히 안내할지 정해야 해요.',
      ]}
    >
      <Card>
        <Mascot who="pico">
          점수를 매기려는 게 아니에요. 지난번보다 무엇이 나아졌는지 보려는 거예요.
        </Mascot>
      </Card>
      <div className="grid gap-4 md:grid-cols-2">
        {ANALYSES.map((a) => (
          <Card key={a.title} title={a.title}>
            <p className="font-mono text-xs tracking-wide text-coral-deep">{a.where}</p>
            <p className="text-sm leading-relaxed">{a.body}</p>
          </Card>
        ))}
      </div>
    </DraftPage>
  );
}

/* ── P18 미지원 브라우저 ────────────────────────────────────────── */

type Reason = 'BROWSER' | 'MOBILE' | 'ISOLATION';

const REASONS: { id: Reason; label: string; title: string; body: string }[] = [
  {
    id: 'BROWSER',
    label: '브라우저 기능 없음',
    title: '이 브라우저에서는 시선 분석을 할 수 없어요',
    body: '시선 분석에 필요한 기능(WebAssembly · Worker · createImageBitmap)이 없어요. 최신 Chrome이나 Edge로 열어 주세요.',
  },
  {
    id: 'MOBILE',
    label: '휴대폰',
    title: '발표 연습은 PC에서 해 주세요',
    body: '슬라이드 · 대본 · 카메라를 한 화면에 띄워야 해서 화면이 넓어야 해요. 피치 확인과 리포트는 휴대폰에서도 볼 수 있어요.',
  },
  {
    id: 'ISOLATION',
    label: '보안 설정',
    title: '브라우저 보안 설정 때문에 연습을 시작할 수 없어요',
    body: '확장 프로그램이나 회사 보안 설정이 막고 있을 수 있어요. 시크릿 창으로 열거나 다른 브라우저로 시도해 주세요.',
  },
];

export function UnsupportedDraft() {
  const [reason, setReason] = useState<Reason>('BROWSER');
  const r = REASONS.find((x) => x.id === reason)!;

  return (
    <DraftPage
      title="미지원 브라우저"
      variants={REASONS}
      variant={reason}
      onVariant={setReason}
      questions={[
        '어느 시점에 이 화면으로 보낼지 정해야 해요 (첫 진입 · 장치 점검 직전).',
        '시선 없이 "소리만으로 계속하기"를 여기서도 열어 줄지 정해야 해요.',
      ]}
    >
      <Card>
        <h2 className="text-2xl font-bold">{r.title}</h2>
        <p className="leading-relaxed text-stone">{r.body}</p>
        <ul className="list-disc space-y-1 pl-5 text-sm">
          <li>Chrome 최신 버전 (PC)</li>
          <li>Edge 최신 버전 (PC)</li>
        </ul>
        <div className="flex flex-wrap gap-3">
          <Button>링크 복사</Button>
          <Button variant="secondary">내 피치로</Button>
        </div>
      </Card>
    </DraftPage>
  );
}

/* ── 404 ────────────────────────────────────────────────────────── */

export function NotFoundDraft() {
  return (
    <DraftPage
      title="찾을 수 없음"
      questions={['지운 피치 · 남의 Take 주소로 들어왔을 때도 이 화면을 쓸지 정해야 해요.']}
    >
      <Card>
        <p className="font-mono text-5xl font-bold">404</p>
        <Mascot who="chichi">어라, 여기는 아무것도 없어. 길을 잃었나 봐!</Mascot>
        <div>
          <Button>내 피치로</Button>
        </div>
      </Card>
    </DraftPage>
  );
}
