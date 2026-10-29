// 제품 글 위주 화면(`frontend/src/features/info/*` · `features/auth/MyPage.tsx`)의 설계서 샘플.
// 문구는 제품과 같다. 복사 · 저장 · 탈퇴처럼 브라우저나 서버를 쓰는 동작은 하지 않는다.
import { Button } from '@/ui/Button';
import { Card } from '@/ui/Panel';
import { Mascot } from '@/ui/Mascot';
import { Frame, type Navigate } from './shared';

type ScreenProps = { stateId: string; onNavigate: Navigate };

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

/** 분석 방식 설명(`/about`) */
export function AboutScreen({ onNavigate }: ScreenProps) {
  return (
    <Frame title="분석 방식 설명" onHome={() => onNavigate('home')}>
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
    </Frame>
  );
}

// 제품 UnsupportedPage의 COPY. 상태 ID가 사유(BROWSER · MOBILE · SUPPORTED)다
const UNSUPPORTED: Record<string, { title: string; body: string }> = {
  browser: {
    title: '이 브라우저에서는 시선 분석을 할 수 없어요',
    body: '시선 분석에 필요한 기능(WebAssembly · Worker · createImageBitmap)이 없어요. 최신 Chrome이나 Edge로 열어 주세요.',
  },
  mobile: {
    title: '발표 연습은 PC에서 해 주세요',
    body: '슬라이드 · 대본 · 카메라를 한 화면에 띄워야 해서 화면이 넓어야 해요. 피치 확인과 리포트는 휴대폰에서도 볼 수 있어요.',
  },
  supported: {
    title: '이 브라우저에서는 연습할 수 있어요',
    body: '시선 분석에 필요한 기능이 모두 있어요. 다른 기기에서 열 때는 아래 브라우저를 써 주세요.',
  },
};

/** 미지원 브라우저(`/unsupported`). 제품은 이 화면이 직접 사유를 고른다 */
export function UnsupportedScreen({ stateId, onNavigate }: ScreenProps) {
  const copy = UNSUPPORTED[stateId] ?? UNSUPPORTED.browser!;
  return (
    <Frame title="미지원 브라우저" onHome={() => onNavigate('home')}>
      <Card>
        <h2 className="text-2xl font-bold">{copy.title}</h2>
        <p className="leading-relaxed text-stone">{copy.body}</p>
        <ul className="list-disc space-y-1 pl-5 text-sm">
          <li>Chrome 최신 버전 (PC)</li>
          <li>Edge 최신 버전 (PC)</li>
        </ul>
        <div className="flex flex-wrap gap-3">
          {/* 샘플은 클립보드를 쓰지 않는다 */}
          <Button>링크 복사</Button>
          <Button variant="secondary" onClick={() => onNavigate('home')}>
            내 피치로
          </Button>
        </div>
      </Card>
    </Frame>
  );
}

/** 찾을 수 없음(제품의 나머지 모든 경로) */
export function NotFoundScreen({ onNavigate }: ScreenProps) {
  return (
    <Frame title="찾을 수 없음" onHome={() => onNavigate('home')}>
      <Card>
        <p className="font-mono text-5xl font-bold">404</p>
        <Mascot who="chichi">어라, 여기는 아무것도 없어. 길을 잃었나 봐!</Mascot>
        <div>
          <Button onClick={() => onNavigate('home')}>내 피치로</Button>
        </div>
      </Card>
    </Frame>
  );
}

const muted = 'text-sm leading-relaxed text-stone';

/** 마이페이지(`/me`). 상단바 아래에 놓여 로고 줄이 없다 */
export function MyScreen({ stateId }: ScreenProps) {
  const consented = stateId === 'consented';
  return (
    <Frame title="마이페이지">
      <Card title="계정">
        <dl className="grid gap-3 text-sm [overflow-wrap:anywhere] sm:grid-cols-[8rem_minmax(0,1fr)]">
          <dt className="text-stone">이름</dt>
          <dd className="font-bold">김피치</dd>
          <dt className="text-stone">이메일</dt>
          <dd className="font-bold">pitch@example.com</dd>
          <dt className="text-stone">로그인</dt>
          <dd className="font-bold">Google 계정</dd>
        </dl>
      </Card>

      <Card title="연습 기록 사용 동의">
        <p className={muted}>
          리허설 중 1초마다 잰 시선 · 목소리 크기 기록을 연습이 끝난 뒤에도 남겨, 분석 기준이
          바뀌었을 때 지난 연습을 다시 분석하는 데 써요. 동의하지 않아도 리포트는 똑같이 나오고, 이
          기록은 리포트를 만든 뒤 지워요(분석에 실패한 연습은 7일 뒤).
        </p>
        <label className="flex min-h-11 items-center gap-3 text-sm font-bold">
          <input
            type="checkbox"
            defaultChecked={consented}
            className="size-4 shrink-0 accent-ink"
          />
          연습 기록을 남겨 다시 분석하는 데 동의해요
        </label>
        <span className="text-xs text-stone">
          바꾼 설정은 다음에 시작하는 연습부터 적용돼요. 이미 끝난 연습은 그대로예요.
        </span>
      </Card>

      <Card title="이 브라우저의 기록">
        <p className={muted}>
          리허설 중 시선 · 슬라이드 · 코치 기록은 이 브라우저에도 저장돼요. 카메라 영상과 마이크
          소리는 저장하지 않아요.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="secondary">이 브라우저 기록 지우기</Button>
          <span className="text-xs text-stone">서버에 저장된 피치와 리포트는 지워지지 않아요.</span>
        </div>
      </Card>

      <Card title="의견 보내기">
        <p className="text-sm text-ink/80">
          좋았던 점이나 개선할 점을 알려주세요. 별점과 관계없이 언제든 보낼 수 있어요.
        </p>
        <p className="border-2 border-dashed border-line-strong p-4 text-sm text-stone">
          의견 종류 · 의견 입력 칸 · 보내기 버튼 자리
        </p>
      </Card>

      <Card title="약관 · 정책">
        <ul className="space-y-2 text-sm">
          <li className="flex min-h-11 items-center underline underline-offset-4">이용약관</li>
          <li className="flex min-h-11 items-center underline underline-offset-4">
            개인정보 처리방침
          </li>
        </ul>
      </Card>

      <Card title="계정 삭제">
        <p className="text-sm text-stone">
          피치 · 연습 기록 · 리포트가 모두 지워지고 되돌릴 수 없어요.
        </p>
        <div>
          <button
            type="button"
            className="min-h-11 text-sm font-bold text-coral-deep underline underline-offset-4"
          >
            계정 삭제하기
          </button>
        </div>
      </Card>
    </Frame>
  );
}
