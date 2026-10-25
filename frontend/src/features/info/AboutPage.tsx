import { Card } from '@/shared/ui/Panel';
import { Mascot } from '@/shared/ui/Mascot';
import { InfoPage } from '@/shared/ui/InfoPage';

/** P14 분석 방식 설명 — 시안 `/dev/design/about` */

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

export function AboutPage() {
  return (
    <InfoPage title="분석 방식 설명">
      <p className="border-b border-line pb-4 font-bold">피치코치는 무엇을 어떻게 보나요?</p>
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
    </InfoPage>
  );
}
