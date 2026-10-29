import { useState } from 'react';
import { Button, ButtonLink } from '@/ui/Button';
import { Badge, type Tone } from '@/ui/Badge';
import { Card, Stat } from '@/ui/Panel';
import { Mascot } from '@/ui/Mascot';
import { DraftPage } from './ui';

export function ComponentsDraft() {
  const [clicks, setClicks] = useState(0);
  return (
    <DraftPage
      title="공용 컴포넌트"
      eyebrow="피치코치 UI · 첫 번째 시안"
      questions={[
        '버튼 크기와 테두리, 카드의 그림자 강도를 확인해 주세요.',
        '현재는 시안에만 적용했습니다. 디자인 확정 후 실제 화면에 순차 적용합니다.',
      ]}
    >
      <Card title="버튼" action={<Badge tone="plain">Button · ButtonLink</Badge>}>
        <p className="text-sm text-stone">클릭, 이동, 비활성 상태를 확인해 보세요.</p>
        <div className="flex flex-wrap items-center gap-4">
          <Button onClick={() => setClicks((value) => value + 1)}>주요 동작</Button>
          <Button variant="secondary" onClick={() => setClicks(0)}>
            횟수 초기화
          </Button>
          <Button variant="quiet" onClick={() => setClicks((value) => value + 1)}>
            조용한 동작
          </Button>
          <ButtonLink to="/report" variant="secondary">
            리포트 시안 보기 →
          </ButtonLink>
        </div>
        <p role="status" className="text-sm">
          주요 동작 클릭: {clicks}회
        </p>
        <div className="flex flex-wrap items-center gap-4">
          <Button size="sm" onClick={() => setClicks((value) => value + 1)}>
            작은 버튼
          </Button>
          <Button disabled>비활성</Button>
        </div>
      </Card>
      <div className="grid gap-6 md:grid-cols-2">
        <Card title="기본 패널">
          <p className="text-sm leading-relaxed">
            제목과 내용을 담는 공통 패널입니다. 각진 모서리와 진한 테두리, 오른쪽 아래로 떨어지는
            그림자를 사용합니다.
          </p>
        </Card>
        <Card
          title="동작이 있는 패널"
          action={
            <ButtonLink size="sm" variant="quiet" to="/">
              시안 목록 →
            </ButtonLink>
          }
        >
          <p className="text-sm leading-relaxed">
            제목 옆에 버튼이나 배지를 넣을 수 있습니다. 좁은 화면에서는 다음 줄로 이동합니다.
          </p>
        </Card>
      </div>
      <Card title="상태 배지">
        <div className="flex flex-wrap gap-3">
          {(
            [
              ['bad', '먼저 고칠 것'],
              ['good', '나아짐'],
              ['strong', '강점'],
              ['plain', '유지'],
              ['muted', '판단 불가'],
            ] satisfies [Tone, string][]
          ).map(([tone, label]) => (
            <Badge key={tone} tone={tone}>
              {label}
            </Badge>
          ))}
        </div>
        <p className="text-sm text-stone">
          색상과 함께 문구로 상태를 설명합니다. 리허설 시선 테두리의 3색은 사용하지 않습니다.
        </p>
      </Card>
      <section aria-label="숫자 지표 예시" className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="발표 시간" value="04:38">
          목표 05:00
        </Stat>
        <Stat label="청중 응시" value="74%">
          이번 Take
        </Stat>
        <Stat label="말하기 속도" value="352자/분">
          측정 평균
        </Stat>
        <Stat label="측정값 없음" value="—">
          측정하지 않은 값은 0으로 표시하지 않습니다.
        </Stat>
      </section>

      <Card title="마스코트" action={<Badge tone="plain">Mascot</Badge>}>
        <Mascot who="chichi">치치는 연습생이에요. 길 안내나 가벼운 말을 해요.</Mascot>
        <Mascot who="pico">피코는 교수예요. 분석 방식처럼 설명하는 자리에 나와요.</Mascot>
      </Card>
    </DraftPage>
  );
}
