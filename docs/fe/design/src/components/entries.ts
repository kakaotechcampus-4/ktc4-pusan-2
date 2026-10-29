/**
 * 목록의 "컴포넌트" · "대화 상자" 칸. 견본은 ComponentDocs.tsx가 src/ui로 그린다.
 * 쓰는 규칙은 제품 코드 · 제품 shared/ui/README.md를 보고 적었다.
 */
import type { GuideComponent, GuideState } from '../model';

const view = (id: string, label: string, description: string): GuideState => ({
  id,
  label,
  description,
});

const components = '컴포넌트';
const dialogs = '대화 상자';

export const componentEntries: GuideComponent[] = [
  {
    kind: 'component',
    id: 'button',
    title: '버튼',
    group: components,
    status: '제품에 있음',
    summary: '동작은 Button, 화면 이동은 ButtonLink. 세 종류 × 두 크기.',
    states: [
      view('variants', '종류 · 크기', 'primary · secondary · quiet와 md · sm, 그리고 ButtonLink.'),
      view('disabled', '막힘 · 진행 중', '누를 수 없을 때와 처리 중 문구.'),
    ],
    rules: [
      '한 덩어리에 primary는 하나만 둔다. 나머지는 secondary, 덜 중요한 것은 quiet.',
      '화면을 옮기면 ButtonLink(라우터 링크), 그 자리에서 무언가 하면 Button이다.',
      'Button은 기본으로 type="button"이다. 폼을 제출할 때만 type="submit"을 준다.',
      '처리 중에는 막고 문구를 "…하는 중…"으로 바꾼다. 같은 요청을 두 번 보내지 않는다.',
      'sm(36px)은 표 · 카드 안처럼 좁은 곳에서만 쓴다. 기본은 md(44px).',
    ],
    tokens: ['coral-deep', 'ink', 'panel', 'cream'],
    sources: ['frontend/src/shared/ui/Button.tsx'],
    file: 'src/ui/Button.tsx',
    props: [
      ['variant', "'primary' | 'secondary' | 'quiet'", '기본 primary'],
      ['size', "'sm' | 'md'", '기본 md'],
      ['disabled', 'boolean', '회색 바탕 · 그림자 없음'],
      ['to (ButtonLink)', 'string', '라우터 경로'],
    ],
    code: `import { Button, ButtonLink } from '@/shared/ui/Button';

<Button onClick={start}>Take 3 시작하기</Button>
<Button variant="secondary" onClick={retry}>다시 불러오기</Button>
<ButtonLink to="/takes" variant="quiet" size="sm">Take 기록</ButtonLink>`,
  },
  {
    kind: 'component',
    id: 'badge',
    title: '배지',
    group: components,
    status: '제품에 있음',
    summary:
      '짧은 상태 표시. 색은 tone 하나로 정하고, Take 상태는 TakeStatusBadge로 크기를 맞춘다.',
    states: [
      view('tones', 'tone', 'bad · good · strong · plain · muted.'),
      view('take-status', 'Take 상태', '홈 · Take 기록이 같은 기준으로 쓰는 상태 여섯 가지.'),
    ],
    rules: [
      '색만으로 의미를 전하지 않는다. 상태 문구를 꼭 넣는다.',
      '색 표는 Badge 한 곳에만 둔다. 화면에서 배지 색을 따로 칠하지 않는다.',
      '측정하지 못한 값은 muted와 "—"로 둔다. 0으로 채우지 않는다.',
      'Take 상태는 점수 유무가 아니라 status로 정한다(TakeStatusBadge).',
    ],
    tokens: ['coral-deep', 'coral-wash', 'ink', 'cream', 'line-strong'],
    sources: [
      'frontend/src/shared/ui/Badge.tsx',
      'frontend/src/shared/ui/TakeStatusBadge.tsx',
      'frontend/src/shared/lib/takeStatus.ts',
    ],
    file: 'src/ui/Badge.tsx · src/ui/TakeStatusBadge.tsx · src/lib/takeStatus.ts',
    props: [
      ['tone', "'bad' | 'good' | 'strong' | 'plain' | 'muted'", '색'],
      ['className', 'string', '크기 · 정렬만. 기본 w-fit'],
      ['status (TakeStatusBadge)', '{ label, tone }', 'takeStatusView의 결과'],
    ],
    code: `import { Badge } from '@/shared/ui/Badge';
import { TakeStatusBadge } from '@/shared/ui/TakeStatusBadge';

<Badge tone="strong">이번 미션</Badge>
<TakeStatusBadge status={takeStatusView(take.id, take.status, take.end_reason)} />`,
  },
  {
    kind: 'component',
    id: 'card',
    title: '카드 · 지표 칸',
    group: components,
    status: '제품에 있음',
    summary: '제목과 내용을 묶는 Card, 숫자 지표 하나를 크게 보이는 Stat.',
    states: [
      view('card', '카드', '제목 · 오른쪽 동작이 있는 카드와 제목 없는 카드.'),
      view('stat', '지표 칸', '숫자 · 비교 문구 · 측정 안 함.'),
    ],
    rules: [
      '카드 안 제목은 text-lg 굵게 하나. 카드 안에 카드를 또 넣지 않는다.',
      '지표 숫자는 tabular · 고정 폭 글꼴로 맞춘다. 재지 않은 값은 "—"와 이유를 쓴다.',
    ],
    tokens: ['ink', 'panel', 'line', 'stone'],
    sources: ['frontend/src/shared/ui/Panel.tsx'],
    file: 'src/ui/Panel.tsx',
    props: [
      ['title', 'string', '카드 제목(없으면 머리 줄도 없음)'],
      ['action', 'ReactNode', '제목 오른쪽(배지 · 작은 버튼)'],
      ['label · value (Stat)', 'string', '지표 이름 · 값'],
      ['children (Stat)', 'ReactNode', '비교 · 설명 한 줄'],
    ],
    code: `import { Card, Stat } from '@/shared/ui/Panel';

<Card title="이번 Take 미션" action={<Badge tone="strong">Take 2에서 정함</Badge>}>
  <p className="text-sm">문장 끝에서 청중을 바라보기</p>
</Card>
<Stat label="청중 응시" value="68%">지난 Take보다 +6%p</Stat>`,
  },
  {
    kind: 'component',
    id: 'mascot',
    title: '마스코트',
    group: components,
    status: '제품에 있음',
    summary: '안내 · 빈 화면의 한마디. 치치는 연습생, 피코는 교수다.',
    states: [
      view('chichi', '치치', '반말 · 함께 연습하는 말투(404 · 무대 안내).'),
      view('pico', '피코', '존댓말 · 설명하는 말투(분석 방식 설명).'),
    ],
    rules: [
      '한마디는 한두 문장. 해야 할 일은 마스코트 말이 아니라 버튼 · 안내 줄에 둔다.',
      '그림은 픽셀 그대로(image-rendering: pixelated) 80px로 둔다.',
    ],
    tokens: ['ink', 'panel'],
    sources: ['frontend/src/shared/ui/Mascot.tsx'],
    file: 'src/ui/Mascot.tsx',
    props: [['who', "'chichi' | 'pico'", '누가 말하나']],
    code: `import { Mascot } from '@/shared/ui/Mascot';

<Mascot who="chichi">어라, 여기는 아무것도 없어. 길을 잃었나 봐!</Mascot>`,
  },
  {
    kind: 'component',
    id: 'wordmark',
    title: '워드마크',
    group: components,
    status: '제품에 있음',
    summary: '글꼴 없이 그리는 5×7 픽셀 PITCH COACH. 색은 글자색(currentColor)을 따른다.',
    states: [view('default', '기본', '밝은 바탕과 어두운 바탕.')],
    rules: [
      '로고를 누르면 홈으로 간다. 링크에 aria-label("피치코치 홈")을 단다.',
      '색을 따로 칠하지 않고 감싼 요소의 글자색을 쓴다.',
    ],
    tokens: ['ink', 'ink-stage'],
    sources: ['frontend/src/shared/ui/PitchCoachWordmark.tsx'],
    file: 'src/ui/PitchCoachWordmark.tsx',
    props: [],
    code: `import { PitchCoachWordmark } from '@/shared/ui/PitchCoachWordmark';

<Link to="/" aria-label="피치코치 홈"><PitchCoachWordmark /></Link>`,
  },
  {
    kind: 'component',
    id: 'app-header',
    title: '상단바',
    group: components,
    status: '제품에 있음',
    summary:
      '홈 · Take 기록 · 마이페이지 · 재시도 · 비교 · 리포트 · 피치 생성이 같이 쓰는 머리. 사용방법 창을 연다.',
    states: [
      view('home', '내 피치', '홈에 있을 때.'),
      view('takes', 'Take 기록', 'Take 기록에 있을 때.'),
      view('me', '마이페이지', '마이페이지에 있을 때.'),
    ],
    rules: [
      '지금 화면의 메뉴는 코랄 밑줄 · 굵게와 aria-current="page"로 표시한다.',
      '바탕은 기본 지면(bg-panel)으로 고정한다. 아래 화면이 밝은 지면이어도 바꾸지 않는다.',
      '시작 전 세팅 · 리허설 · 분석 대기 · 대본 확인은 집중 화면이라 두지 않는다. 피치 생성에는 #738부터 둔다. 시작 전 세팅은 단순화할 때(#706) 같이 정한다.',
      '높이(약 85px)는 styles.css의 --spacing-app-header와 맞춘다. 피치 생성 화면이 그 값으로 남은 높이를 계산한다.',
    ],
    tokens: ['panel', 'ink', 'coral', 'line-strong'],
    sources: ['frontend/src/shared/ui/AppHeader.tsx', 'frontend/src/app/routes.tsx'],
    file: 'src/ui/AppHeader.tsx',
    props: [['current (설계서)', "'home' | 'takes' | 'me' | 없음", '제품은 지금 경로로 정한다']],
    code: `import { AppHeader } from '@/shared/ui/AppHeader';

// 라우트 묶음에서 한 번. 메뉴의 현재 표시는 경로로 정한다
<AppHeader />`,
    productNote: '로그아웃 · 세션은 없다. 메뉴는 미리보기 안에서 누르면 설계서 화면으로 간다.',
  },
  {
    kind: 'component',
    id: 'inputs',
    title: '입력 · 선택',
    group: components,
    status: '제품에 있음',
    summary: '선택 상자 · 여러 줄 입력 · 체크 상자 · 선택 카드 묶음(radiogroup).',
    states: [
      view('default', '기본', '입력할 수 있는 상태.'),
      view('disabled', '막힘', '보내는 중 · 시작한 뒤처럼 바꿀 수 없는 상태.'),
    ],
    rules: [
      '입력 칸은 굵은 선(border-2 border-ink)과 흰 바탕, 높이 44px 이상이다.',
      '모든 입력에 보이는 라벨을 단다. 자리 표시 글로 라벨을 대신하지 않는다.',
      '글자 수 제한이 있으면 아래에 "n / 최대"를 보인다.',
      '선택 카드는 색과 함께 모양(◉ · ○)도 바꾼다. 화살표 · Home · End로 옮긴다.',
    ],
    tokens: ['ink', 'cream', 'line-strong', 'coral-wash'],
    sources: [
      'frontend/src/shared/ui/SuggestionCategorySelect.tsx',
      'frontend/src/features/auth/SuggestionForm.tsx',
      'frontend/src/features/rehearsal/prepare/ScriptModeChoice.tsx',
    ],
    file: 'src/ui/SuggestionCategorySelect.tsx · src/ui/ScriptModeChoice.tsx',
    props: [
      ['value · onChange', '선택 값', '제어 컴포넌트'],
      ['disabled', 'boolean', '회색 바탕 · 선은 line-strong'],
    ],
    code: `<label htmlFor={id} className="text-sm font-bold">의견 (필수)</label>
<textarea
  id={id}
  rows={5}
  className="w-full min-w-0 resize-y border-2 border-ink bg-white p-3 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
/>
<ScriptModeChoice value={mode} onChange={setMode} disabled={starting} />`,
  },
  {
    kind: 'component',
    id: 'notices',
    title: '안내 줄 · 불러오기',
    group: components,
    status: '제품에 있음',
    summary: '불러오는 중 · 실패 칸, 결과 안내(status) · 오류(alert), 빈 자리.',
    states: [
      view('loading', '불러오는 중', '목록 · 리포트를 가져오는 동안.'),
      view('error', '불러오기 실패', '다시 불러오기 버튼과 함께.'),
      view('messages', '안내 · 오류 · 빈 자리', '조작 결과와 오류 한 줄, 아직 없는 것.'),
    ],
    rules: [
      '조작 결과는 role="status", 실패는 role="alert"로 둔다. 화면 낭독기가 읽는다.',
      '실패 문구는 무엇이 안 됐는지 + 할 일(다시 시도 · 다른 곳에서)을 쓴다.',
      '빈 자리는 점선 테두리로 두고, 왜 비었는지 쓴다.',
    ],
    tokens: ['ink', 'panel', 'coral-deep', 'line-strong'],
    sources: ['frontend/src/features/report/QueryState.tsx'],
    file: 'src/screens/shared.tsx (QueryNotice)',
    props: [
      ['stateId', "'loading' | 'error'", '설계서 샘플용'],
      ['onRetry', '() => void', '다시 불러오기'],
    ],
    code: `<p role="status" className="text-sm">의견을 보냈어요. 감사합니다.</p>
<p role="alert" className="text-sm text-coral-deep">동의 설정을 바꾸지 못했어요. 다시 시도해 주세요.</p>`,
  },
  {
    kind: 'component',
    id: 'confirm-dialog',
    title: '확인 창',
    group: dialogs,
    status: '제품에 있음',
    summary: '되돌리기 어려운 동작 전에 한 번 더 묻는다(다른 곳의 연습 끝내기).',
    states: [
      view('open', '열림', '취소 · 확인 두 버튼. 처음 초점은 취소.'),
      view('busy', '처리 중', '끝내는 동안 버튼과 Esc를 막는다.'),
    ],
    rules: [
      '브라우저 기본 <dialog>를 showModal()로 연다. 뒤 화면은 막히고 Esc로 닫힌다.',
      '되돌리기 어려운 쪽이 확인 버튼이면 처음 초점은 취소에 둔다(autoFocus).',
      '처리 중에는 버튼과 Esc를 막고, 끝나면 결과를 알린 뒤 닫는다.',
      '제목 · 본문을 aria-labelledby · aria-describedby로 잇는다.',
      '지금 제품의 다른 확인(브라우저 기록 지우기 · 계정 삭제)은 브라우저 confirm()이다. 새로 만들면 이 창을 쓴다.',
    ],
    knownIssues: [
      '취소하면 부모가 창을 지우고, 창은 useEffect 정리에서 닫힌다. 그때는 이미 문서에서 빠져 있어 초점이 연 버튼으로 돌아오지 않는다(제품도 같은 코드). 정리를 useLayoutEffect로 옮기면 DOM에서 빠지기 전에 닫혀 초점이 돌아온다 — FE에서 고치기로 했다(#766 리뷰). 고쳐지면 설계서 복사본도 맞춘다.',
    ],
    tokens: ['panel', 'ink'],
    sources: ['frontend/src/features/rehearsal/prepare/ReplaceTakeDialog.tsx'],
    file: 'src/ui/dialogs/ReplaceTakeDialog.tsx',
    props: [
      ['busy', 'boolean', '처리 중'],
      ['onConfirm · onCancel', '() => void', '닫는 것은 부모가 정한다'],
    ],
    code: `{replacing && (
  <ReplaceTakeDialog
    take={replacing.take}
    busy={replacing.busy}
    onConfirm={confirmReplace}
    onCancel={cancelReplace}
  />
)}`,
  },
  {
    kind: 'component',
    id: 'help-dialog',
    title: '도움말 창',
    group: dialogs,
    status: '제품에 있음',
    summary: '사용방법 세 단계. 가운데 창으로 열리고, 640px보다 좁은 창에서만 화면 전체를 채운다.',
    states: [view('open', '열림', '닫기 버튼에 처음 초점.')],
    rules: [
      '긴 내용은 창 안에서 스크롤하고, 제목 줄은 위에 붙여 둔다(sticky).',
      '열려 있는 동안 뒤 페이지 스크롤을 막는다.',
      '닫으면 연 버튼으로 초점이 돌아온다.',
    ],
    tokens: ['panel', 'ink', 'coral-deep', 'cream'],
    sources: ['frontend/src/shared/ui/HomeHelpDialog.tsx'],
    file: 'src/ui/dialogs/HomeHelpDialog.tsx',
    props: [['onDismiss', '() => void', '닫힌 뒤']],
    code: `{helpOpen && <HomeHelpDialog onDismiss={() => setHelpOpen(false)} />}`,
  },
  {
    kind: 'component',
    id: 'review-dialog',
    title: '리뷰 창',
    group: dialogs,
    status: '제품에 있음',
    summary: '별점 · 의견을 받는 입력 창. 제출 줄은 스크롤 밖 아래에 둔다.',
    states: [
      view('open', '열림', '별을 고르기 전이라 제출이 막혀 있다.'),
      view('rated', '별점 고름', '별 4개를 고른 상태.'),
    ],
    rules: [
      '입력이 길어지면 창 안만 스크롤하고, 제출 · 나중에 줄은 늘 보이게 둔다.',
      '필수 입력(별점)이 없으면 제출을 막는다.',
      '닫기 · 나중에 · Esc는 모두 같은 동작(계속하기)이다.',
    ],
    tokens: ['panel', 'ink', 'coral-deep', 'stone', 'line'],
    sources: ['frontend/src/features/pitch/ServiceReviewDialog.tsx'],
    file: 'src/ui/dialogs/ServiceReviewDialog.tsx',
    props: [
      ['onContinue', '() => void', '닫기 · 나중에 · 제출 뒤'],
      ['initialRating (설계서)', 'number', '샘플용 처음 별점'],
    ],
    code: `// 제품: 홈에서 리뷰를 받을 차례면 연다(HomeServiceReview)
<ServiceReviewDialog onContinue={() => setClosed(true)} />`,
    productNote: '서버에 보내지 않는다. 제출해도 오류 · 중복 처리 없이 바로 닫힌다.',
  },
  {
    kind: 'component',
    id: 'slide-preview',
    title: '미리보기 창',
    group: dialogs,
    status: '제품에 있음',
    summary: '슬라이드를 크게 보는 창. 피치 생성 화면의 "슬라이드 미리보기" 상태와 같다.',
    states: [view('open', '열림', '피치 생성 화면에서 연 슬라이드 미리보기.')],
    rules: ['보기만 하는 창이라 닫기 하나와 이전 · 다음만 둔다.', '닫으면 보던 단계로 돌아간다.'],
    tokens: ['panel', 'ink'],
    sources: ['frontend/src/features/pitch/create/steps/SlidePreviewDialog.tsx'],
    file: 'src/screens/CreateScreen.tsx',
    props: [],
    code: `// 제품: 썸네일을 누르면 연다. 쪽 이동은 부모가 쥔다
<SlidePreviewDialog doc={doc} page={page} onPage={setPage} onClose={close} />`,
    productNote: '설계서는 피치 생성 샘플 안의 창을 그대로 보인다. 제품은 썸네일을 눌러 연다.',
  },
];
