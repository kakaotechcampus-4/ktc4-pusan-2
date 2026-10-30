/**
 * 화면 이름 · 상태 · 동작 규칙 · 제품과 다른 점을 한 곳에서 정의한다.
 * DesignGuide가 목록과 설명을, ScreenPreview가 화면 · 상태 ID를 쓴다.
 * 제품과 다른 점은 여기 productNote가 원본이다(설명 패널에 보인다). 미리보기를 고치면 상태 · 규칙 · productNote를 함께 맞춘다.
 */
// 확장자를 적는다 — npm run drift가 이 파일을 Node로 바로 읽는다(scripts/product-drift.mjs)
import { componentEntries } from './components/entries.ts';
import { foundationEntries } from './foundations/entries.ts';
import type { GuideEntry, GuideScreen, GuideState, ProductLink } from './model';

/** 제품 개발 서버(`frontend/`에서 `npm run dev`). 설계서와 따로 띄운다 */
export const PRODUCT_ORIGIN = 'http://localhost:3000';

/**
 * 설명을 마지막으로 대조한 제품 커밋. `npm run drift`가 이 뒤로 바뀐 sources를 화면별로 보여 준다.
 * 바뀐 화면의 설명을 맞춘 뒤 비교한 커밋으로 올린다(README "제품이 바뀌었을 때").
 * `pending`은 아직 merge 전인 FE PR을 그 head 기준으로 미리 맞춘 것이다. merge되면 drift가 그 파일을
 * 바뀜으로 보이니, 최종 코드와 다시 대조하고 여기서 지운다
 */
export const PRODUCT_CHECKED: {
  commit: string;
  date: string;
  pending: { pr: number; head: string; what: string }[];
} = {
  commit: '43b95161106639e48db50b904683fddc5c9e38d4',
  date: '2026-10-30',
  pending: [],
};

const state = (id: string, label: string, description: string): GuideState => ({
  id,
  label,
  description,
});
const base = state('default', '기본', '대표 화면을 표시한다.');
const next = (label: string, screenId: string, stateId?: string) => ({
  label,
  screenId,
  stateId,
});
const link = (label: string, path: string, note?: string): ProductLink => ({ label, path, note });

// 제품 목 데이터(`frontend/src/mocks/`)의 id. 피치 p1 · Take t3이 기본이다
const PITCH_EDIT = '/pitch/10000000-0000-4000-8000-000000000001/edit';
const COMPARISON = '/pitch/10000000-0000-4000-8000-000000000001/comparison';
const REHEARSAL = link('리허설 · 실시간 코칭', '/takes/t3/rehearsal');
const PROCESSING = link(
  '분석 대기',
  '/takes/t4/processing',
  '몇 초마다 단계가 넘어가고 리포트로 간다',
);
const RETRY = link(
  '업로드 실패 · 재시도',
  '/takes/t3/retry',
  '이 브라우저에 기록이 없으면 안내만 뜬다. 콘솔에 mockTakeEnd.fail() 뒤 발표를 끝내면 열린다',
);

const group = '제품 화면 · 설계서 샘플';

const productScreens: GuideScreen[] = [
  {
    kind: 'screen',
    id: 'home',
    title: '홈 · 내 피치',
    group,
    route: '/',
    status: '제품에 있음',
    summary:
      '피치 목록과 연습 통계를 보고 새 피치 만들기 · 피치 수정 · Take 기록 · Take 비교로 간다. 데이터는 예시다.',
    states: [
      base,
      state(
        'prepare-entry-draft',
        '발표 준비 버튼 · 시안',
        '기존 목록형 홈을 유지하고 각 피치 행 맨 오른쪽에 발표 준비 버튼만 추가한 시안이다. 제품에는 아직 반영하지 않았다.',
      ),
      state('loading', '불러오는 중', '발표 노트를 가져오는 동안 안내한다.'),
      state('empty', '첫 피치 전', '통계 · TIP을 빼고 첫 피치 안내만 둔다.'),
      state('error', '오류', '목록을 가져오지 못했고 다시 불러오기를 안내한다.'),
      state(
        'consent',
        '테스트 참여 전',
        '사용자 테스트에 아직 동의하지 않아 위쪽에 참여 안내 카드가 있다. 동의하면 카드가 사라진다.',
      ),
      state(
        'pending',
        '남은 설문',
        '동의한 뒤 끝났는데 설문을 남기지 않은 리포트가 위쪽에 있다. 누르면 그 리포트로 간다.',
      ),
    ],
    rules: [
      '제품 홈에는 연습 시작 버튼이 없다. 연습은 피치 생성 · 수정의 마지막 단계에서 시작 전 세팅으로 간다.',
      '"발표 준비 버튼 · 시안"에서만 각 행 맨 오른쪽에 발표 준비 버튼을 둔다. 제목과 같은 피치 수정 경로로 연결해 설계서 안의 자료·대본 준비 샘플로 이동하며, Take를 만들거나 장치를 열지 않는다.',
      '시안의 Take 기록 · Take 비교 영역은 고정 너비를 써서 연습 횟수와 비교 링크 유무가 달라도 각 열을 위아래로 맞춘다.',
      '시안의 발표 준비 버튼은 기존 팔레트의 coral-deep(#C9421C) 단색 바탕과 밝은 글자를 사용한다. 클릭할 수 있는 경계를 드러내는 1px ink 외곽선만 추가하고 offset 그림자는 두지 않는다. 기존 작은 크기·열 정렬을 유지하며 버튼 안에는 화살표나 아이콘을 두지 않는다.',
      '피치 제목은 피치 수정, "Take 기록"은 그 피치로 거른 Take 기록을 연다.',
      'Take 비교 링크는 Take가 둘 이상인 피치에만 있다.',
      '누적 발표 시간은 길이를 아는 Take만 더하고, 하나도 모르면 —로 둔다.',
      '설문은 홈에서 받지 않는다(#776). 동의 전이면 참여 안내, 동의 뒤면 남은 설문 링크만 두고 설문은 리포트에서 한다. 자동으로 띄우는 창은 없다.',
      '남은 설문은 동의한 뒤 끝난 Take 만 오고, 최대 3개를 보이고 나머지는 "외 N개"로 둔다. 없으면 아무것도 두지 않는다.',
    ],
    components: [
      'HomePitchCard',
      '통계 3칸',
      'TIP 줄',
      '참여 안내 카드 · 참여 동의 창',
      '남은 설문 줄',
    ],
    tokens: ['greige', 'panel', 'ink', 'coral-deep', 'line-strong', 'coral-wash'],
    sources: [
      'frontend/src/features/pitch/HomePage.tsx',
      'frontend/src/features/pitch/HomeSurveyNotice.tsx',
      'frontend/src/features/pitch/HomePitchCard.tsx',
      'frontend/src/features/pitch/homeFormat.ts',
    ],
    productNote:
      '바닥글(이용약관 · 개인정보 처리방침)은 미리보기에서 뺐다. 참여 동의 창은 대화 상자의 "참여 동의 창"에서도 본다. 동의 · 남은 설문은 서버에 묻지 않는 예시다. "발표 준비 버튼 · 시안"의 버튼은 제품에 없는 제안이며, 기본 상태에는 표시하지 않는다.',
    productLinks: [link('홈 · 내 피치', '/')],
    next: [
      next('새 피치 만들기', 'pitch-create', 'empty'),
      next('피치 수정', 'pitch-create'),
      next('Take 기록', 'takes'),
      next('Take 비교', 'comparison'),
    ],
  },
  {
    kind: 'screen',
    id: 'pitch-create-a',
    title: '발표 준비 A안 · PM 검토',
    group,
    route: '/pitch/new',
    status: '시안',
    summary:
      'PM 승인 전 디자인 검토용. 발표 자료부터 연습 설정까지 한 페이지에서 준비하는 A안이다. 제품 구현은 포함하지 않는다.',
    states: [
      state('default', '시안 · 슬라이드 준비', '예시 슬라이드를 올린 뒤 대본을 연결한다.'),
      state('empty', '시안 · 빈 화면', '모든 섹션은 보이고 슬라이드부터 시작한다.'),
      state('error', '시안 · 업로드 실패', '예시 오류와 다시 시도 동작을 확인한다.'),
      state('mapping-error', '시안 · 매핑 실패', '대본은 보존하고 다시 매핑할 수 있다.'),
      state(
        'mapping',
        '시안 · 매핑 확인',
        '장별 예시 슬라이드와 대본을 큰 창에서 확인하고 수정한다.',
      ),
      state('ready', '시안 · 준비 완료', '필수 세 항목을 저장한 뒤 시작 전 점검으로 이동한다.'),
      state('many', '시안 · 버전 30개', '긴 버전 목록의 스크롤과 읽기 전용 열람을 확인한다.'),
    ],
    rules: [
      'PM 승인 전 독립 시안이다. 파일 업로드·저장·매핑은 메모리의 예시 데이터이며 API·카메라·마이크를 사용하지 않는다.',
      '슬라이드, 대본 연결, 발표정보, 평가기준(선택)을 한 페이지에 둔다. 날짜 항목은 없고 시간 편차는 0초/0초로 시작한다.',
      '섹션 배경은 발표 생성용 밝은 지면의 panel과 cream을 번갈아 쓴다. 슬라이드·발표정보는 panel, 대본 연결·평가기준은 cream이다. 경계는 1px line-strong이고 입력칸은 흰색을 유지한다.',
      '준비 순서는 본문으로 이동한다. 자료 버전 관리와 내부 폴더는 처음에 모두 접혀 있고, 펼쳐도 사이드바 위치는 유지한다.',
      '버전 창은 PDF 예시·장별 대본·평가기준을 읽기 전용으로 보여 준다. 현재 본문과 연결 조합은 바뀌지 않는다. 버전 트리에 발표정보·발표 자료 제목·새 버전 버튼은 없다.',
      '연결할 슬라이드 메뉴는 파일명 없이 버전과 장수만 표시한다. 예시 매핑이 완료되면 큰 확인·수정 창을 자동으로 연다. 이미 매핑한 대본은 연결 메뉴를 숨긴다.',
      '매핑 창은 목록·선택 장의 슬라이드·편집 대본을 함께 보여 주며, 편집값은 닫고 다시 열어도 유지한다. 수정하면 다시 매핑하고 저장해야 한다.',
      '고정 하단 버튼은 필수 준비가 끝나야 시작 전 점검으로 간다. 이 시안은 Take를 생성하지 않는다.',
    ],
    components: ['시안 전용 폴더 트리 · 연결 메뉴 · 매핑 창'],
    tokens: ['greige', 'panel', 'ink', 'coral-deep'],
    sources: ['frontend/src/features/pitch/create/PitchCreatePage.tsx'],
    productNote:
      '현재 제품은 단계별 본문과 날짜 입력을 사용한다. 이 항목은 제품을 설명하는 샘플이 아닌 PM 검토 시안이다. 기존 제품 샘플은 피치 생성 · 수정에서 비교한다. #790의 제품 구현 PR을 대체하는 디자인 검토이며, 실제 제품 반영은 PM 승인 후 별도 작업이다. 슬라이드는 PDF를 읽지 않고 3장의 예시 자료를 그린다. 입력한 원문은 화면에 보관하고, 매핑과 평가기준 정리 결과는 고정 fixture로 표현한다. API 응답·장비·서버 저장·실제 파일 선택은 검증 대상이 아니다.',
    next: [next('현재 제품 샘플과 비교', 'pitch-create'), next('시작 전 점검', 'prepare-mission')],
  },
  {
    kind: 'screen',
    id: 'pitch-create',
    title: '피치 생성 · 수정',
    group,
    route: '/pitch/new',
    status: '제품에 있음',
    summary: '슬라이드 · 대본 · 발표정보 · 평가기준을 차례로 준비하고 시작 전 세팅으로 간다.',
    states: [
      state('default', '슬라이드 올린 뒤', '올린 슬라이드를 확인하는 첫 단계다.'),
      state('empty', '슬라이드 전', 'PDF를 올리기 전이라 다음 단계가 막힌다.'),
      state('error', '업로드 오류', '자료를 다시 올릴 수 있도록 안내한다.'),
      state('preview', '슬라이드 미리보기', '슬라이드를 크게 보는 창을 연다.'),
    ],
    rules: [
      '슬라이드 → 대본 → 발표정보 확인 → 평가기준(선택) 순서로 준비한다.',
      '"연습 시작 →"은 Take를 만들지 않고 시작 전 세팅으로 간다. 저장한 평가기준이 없으면 "건너뛰기"로 시작한다.',
      '발표정보를 저장하지 않았으면 연습 시작 대신 발표정보 단계로 돌아가 저장을 요청한다.',
      'PDF만, 40MB까지 올린다. 파일 이름으로 발표 제목을 채운다.',
    ],
    components: ['PaneHeading', 'PaneFooter', 'primaryButton · secondaryButton'],
    tokens: ['greige', 'cream', 'panel', 'ink', 'coral-deep'],
    sources: [
      'frontend/src/features/pitch/create/PitchCreatePage.tsx',
      'frontend/src/features/pitch/create/lib/draft.ts',
      'frontend/src/features/pitch/create/steps/SlidePane.tsx',
      'frontend/src/features/pitch/create/steps/CriteriaPane.tsx',
      'frontend/src/features/pitch/create/steps/SlidePreviewDialog.tsx',
    ],
    productNote:
      '제품은 왼쪽에 자료 버전 트리(VersionRail)와 치치, 본문 위에 발표 정보 줄(InfoBar · 저장 전이면 "저장 전" 표시) · 단계 표시줄(StepBar)을 둔다. 위에는 공용 상단바가 얹히고 경로 줄은 없다(#738). 미리보기는 단계 목록 하나로 줄였고 대본 매핑 확인 · 버전 고르기 · 평가기준 편집은 그리지 않는다. "대본 매핑"은 매핑 확인 대신 다음 단계로 넘어간다. 슬라이드 크게 보기는 제품에서 썸네일을 눌러 연다(미리보기는 버튼 하나). 단계별 제목 · 버튼 · 안내 문구와 슬라이드를 올리기 전 아래 줄이 없는 것은 제품과 같다.',
    productLinks: [link('새 피치', '/pitch/new'), link('피치 수정', PITCH_EDIT)],
    next: [next('연습 시작 → 시작 전 세팅', 'prepare-mission'), next('내 피치', 'home')],
  },
  {
    kind: 'screen',
    id: 'prepare-mission',
    title: '시작 전 세팅',
    group,
    route: '/pitch/:pitchId/device-check',
    status: '제품에 있음',
    summary:
      '카메라 · 마이크 · 시선 기준을 점검하고 대본 표시를 고른 뒤, 이 화면의 시작 버튼에서 Take를 만들어 리허설을 연다.',
    states: [
      state('default', '점검 완료', '카메라 · 마이크 · 시선 기준이 모두 돼 시작할 수 있다.'),
      state(
        'no-camera',
        '카메라 꺼짐',
        '카메라 영상이 없어 점검을 시작할 수 없고 시작 버튼이 막힌다.',
      ),
      state('mic-error', '마이크 입력 없음', '마이크 입력이 확인되지 않아 시작 버튼이 막힌다.'),
      state(
        'calibration',
        '시선 기준 전',
        '카메라 · 마이크는 됐지만 시선 기준(3점)을 아직 잡지 않아 시작 버튼이 막힌다.',
      ),
    ],
    rules: [
      '공용 상단바(AppHeader)는 두지 않고 자체 틀(ScreenFrame)을 쓴다. 상단바 높이만큼 시작 버튼이 첫 화면 밖으로 밀려서다. 상단바는 시작 전 세팅을 단순화할 때(#706) 같이 정한다.',
      'Take는 이 화면의 시작 버튼에서만 만든다. 들어오는 것만으로는 만들지 않는다.',
      '카메라 영상 · 마이크 입력 · 시선 기준 셋이 모두 되고, 이번 연습의 슬라이드 · 대본 조합이 정해져야(제품 `choice !== null`) 시작한다. 카메라가 없으면 시작할 수 없다.',
      '안내 줄은 지금 막고 있는 것 하나만 말한다. 순서는 시작 실패 → 시작을 누른 뒤 여는 중(다른 곳의 연습을 끝낼지 묻는 동안은 그 안내) → 준비 정보 문제(불러오기 실패 · 버전 없음 등) → 평소 목소리 읽는 중 → 장치 오류 → 시선 분석을 쓸 수 없음 → 카메라 → 마이크 → 시선 분석 준비 중 → 기준 잡기 실패 → 기준 미완료 → 기준이 흐릿함(막지 않고 권하기만) → 평소 목소리 권유(선택) → "점검이 끝났어요"다.',
      '평소 목소리 읽기는 선택이라 시작을 막지 않는다.',
      '다른 창 · 기기에서 발표 중인 Take가 있으면 시작을 누를 때 그 연습을 끝내고 시작할지 묻는다. 끝내지 못하면 시작하지 않는다. 그 창을 닫으면(취소 · Esc · 끝내기 실패) 초점을 시작 버튼으로 돌린다 — 창이 열린 동안 시작 버튼이 막혀 있어 브라우저가 돌려주지 못하기 때문이다. 시작을 누른 뒤에는 대본 표시 · 장치 선택을 잠근다.',
      '"실전 모드 · 대본 없이"를 고르면 버튼이 "실전 모드로 시작"으로 바뀌고 실전 무대로 간다.',
    ],
    components: [
      'ScreenFrame 틀',
      'CheckCard 점검 항목',
      'MissionCard',
      'ScriptModeChoice',
      'Card',
      'Button',
    ],
    tokens: ['greige', 'panel', 'ink', 'stage'],
    sources: [
      'frontend/src/features/rehearsal/prepare/DeviceCheckPage.tsx',
      'frontend/src/features/rehearsal/prepare/ScreenFrame.tsx',
      'frontend/src/features/rehearsal/prepare/CheckCard.tsx',
      'frontend/src/features/rehearsal/prepare/ScriptModeChoice.tsx',
      'frontend/src/features/rehearsal/prepare/ReplaceTakeDialog.tsx',
      'frontend/src/features/rehearsal/prepare/activeTake.ts',
      'frontend/src/features/rehearsal/prepare/prepareStore.ts',
    ],
    productNote:
      '카메라 미리보기 · 시선 기준 전체 화면 · 장치 선택 · 음량 막대 · 평소 목소리 읽기 패널은 그리지 않고 자리만 둔다. 시선 기준의 "누르면 시작"은 기준을 다 잡은 상태로 바로 넘어간다. 안내 줄은 카메라 · 마이크 · 시선 기준 세 갈래만 그린다(제품 순서는 동작 규칙). 다른 곳의 연습을 끝낼지 묻는 창은 그리지 않는다. 시작 조건은 제품과 같다.',
    productLinks: [link('시작 전 세팅', '/pitch/p1/device-check')],
    next: [
      next('Take 시작 → 리허설', 'stage'),
      next('실전 모드로 시작', 'stage', 'exam'),
      next('뒤로 · 피치 수정', 'pitch-create'),
    ],
  },
  {
    kind: 'screen',
    id: 'prepare-layout-review',
    title: '시작 전 세팅 · PM 검토 시안',
    group: 'PM 검토 · 독립 시안',
    route: '/pitch/:pitchId/device-check',
    status: '시안',
    summary:
      '닫힌 구현 PR #789를 대체하는 디자인 검토용 독립 시안. PM 승인 전이며 제품 반영 완료를 뜻하지 않는다. 장비·미션·대본 표시 배치만 검토한다.',
    states: [
      state(
        'default',
        '장비 연결 전',
        '시안: 필수 0/3. 사진처럼 이름 옆 상태와 오른쪽 선택창을 둔다.',
      ),
      state('ready', '점검 완료', '시안: 필수 3/3. 목소리를 재지 않아도 시작 버튼을 켠다.'),
      state('calibration', '시선 기준 전', '시안: 기준 잡기는 실제 보정 없이 완료 예시로 바뀐다.'),
      state('mic-error', '마이크 입력 없음', '시안: 입력 미확인 상태와 막힌 시작 버튼.'),
      state('gaze-unavailable', '시선 분석 사용 불가', '시안: 시선 분석 불가 설명과 비활성 버튼.'),
      state(
        'permission-error',
        '권한 오류',
        '시안: 권한 요청 없이 오류 안내와 간결한 미션 요약을 그린다.',
      ),
      state(
        'voice-reading',
        '평소 목소리 읽는 중',
        '시안: 실제 측정 없이 문단·예시 음량·다 읽었어요 버튼을 표시한다.',
      ),
      state(
        'voice-done',
        '평소 목소리 완료',
        '시안: 선택 단계 완료 안내와 미션 전문 접근을 유지한다.',
      ),
      state(
        'starting',
        '시작 중 설정 잠금',
        '시안: 서버 요청 없이 시작 중 비활성 상태를 고정해 본다.',
      ),
      state('first-take', '첫 Take', '시안: 아직 미션이 없다는 안내를 표시한다.'),
      state('empty', '미션 없음', '시안: 이전 리뷰에서 정한 미션이 없는 예시.'),
      state('one', '미션 1개', '시안: 빈 줄을 만들지 않고 미션 영역 높이를 유지한다.'),
      state('two', '미션 2개', '시안: 3개일 때와 같은 영역 높이.'),
      state('four', '미션 4개', '시안: 다음 페이지의 미션 1개도 같은 높이.'),
      state('many', '미션 20개', '시안: 3개씩 넘기며 마지막 페이지는 2개다.'),
      state('long', '미션 500자', '시안: 잘리는 설명에만 더 보기를 두고 전문을 읽는다.'),
      state(
        'long-devices',
        '긴 장치 이름',
        '시안: 같은 폭의 선택창과 전체 이름 도움말을 확인한다.',
      ),
      state(
        'audio-low',
        '오디오 입력 작음',
        '시안: 카메라 아래 미터의 앞쪽 일부만 채우는 작은 입력 예시.',
      ),
      state('audio-high', '오디오 입력 큼', '시안: 믹서형 초록·노랑·빨강까지 채우는 큰 입력 예시.'),
      state(
        'exam',
        '실전 모드',
        '시안: 대본과 실시간 코치가 없음을 알리고 실전 화면 샘플로 이동한다.',
      ),
    ],
    rules: [
      'PM 승인 전 디자인 검토 전용이다. 실제 frontend 제품 구현·배포·반영 완료로 취급하지 않는다.',
      '카메라 자리는 왼쪽 하나, 장비 준비·미션·대본 표시는 오른쪽에 둔다. 자체 머리/바닥을 쓰며 공용 상단바 추가는 이 시안에 포함하지 않는다.',
      '장비 행은 항목명 → 원형 상태 표시·설명 → 필요한 조작부다. 선택/상태/버튼을 공통 4열로 강제하지 않는다.',
      '카메라와 마이크 선택창은 박스 중앙부터 오른쪽 끝까지 같은 폭이다. 아래 버튼은 118px로 짧게 두고 오른쪽 끝을 맞춘다.',
      '필수 준비 수는 제목 옆, 카메라·마이크 켜기는 오른쪽 위에 둔다. 평소 목소리는 선택이라고 표시한다.',
      '카메라 바로 아래에 같은 폭의 낮은 오디오 입력 미터를 둔다. panel 면·얇은 line-strong 선·작은 픽셀 마이크 아이콘과 같은 높이의 세그먼트 한 줄만 사용한다. 제목·상태 글자·숫자·배지·조절 손잡이는 없다.',
      '오디오 미터의 믹서형 초록→노랑→빨강은 형광 느낌만 줄인 시안 전용 제안색이다. 시선 전용 gaze 색·전역 토큰을 재사용하거나 바꾸지 않는다. 입력 예시는 상태 선택으로 바뀌고 자동 애니메이션은 없다.',
      '미션은 3개씩, 일반 카드 높이는 노트북 156px·데스크톱 164px다. 빈 미션 행은 만들지 않는다. 실제 잘릴 때만 더 보기로 전문을 연다.',
      '대본 표시 3개는 장비 연결 전에도 선택할 수 있다. 시작 중 고정 예시에서는 장치와 대본 표시를 잠근다.',
      '모든 상태는 독립 fixture다. 카메라·마이크·음성 측정·시선 보정·API·Take 생성을 실행하지 않는다. 시작은 기존 설계서 무대 샘플로만 이동한다.',
    ],
    components: [
      'PrepareDraftFrame',
      'PrepareDraftAudioMeter',
      'PrepareDraftCheckCard',
      'PrepareDraftMissionCard',
      'PrepareDraftScriptMode',
    ],
    tokens: ['greige', 'panel', 'ink', 'line', 'coral-deep', 'stage-deep'],
    sources: [],
    knownIssues: [
      'PM 승인 이후 제품 구현 범위와 검증 계획을 별도로 정한다. 시안의 상태 전환은 실제 장비·서버 동작을 검증하지 않는다.',
    ],
    next: [
      next('점검 완료 시안', 'prepare-layout-review', 'ready'),
      next('기존 제품 샘플과 비교', 'prepare-mission'),
      next('리허설 샘플', 'stage'),
    ],
  },
  {
    kind: 'screen',
    id: 'stage',
    title: '리허설 무대',
    group,
    route: '/takes/:takeId/rehearsal',
    status: '시안',
    summary:
      'PM 승인 대기 디자인 시안. 슬라이드와 대본을 보며 연습한다. 카메라 · 마이크를 열거나 음성을 보내지 않는다.',
    states: [
      state(
        'default',
        '기본',
        '3번째 장. 마지막 장이 아니라 끝내기가 막혀 있고, 버튼 옆에 그 이유를 글자로 둔다.',
      ),
      state('last-slide', '마지막 장', '12번째 장. 끝내기를 누를 수 있다.'),
      state('exam', '실전 모드', '대본 칸 없이 실전처럼 연습한다. 코치가 말하지 않는다.'),
      state('keyword', '핵심 키워드', '대본 대신 키워드를 보며 연습한다.'),
      state('confirm-end', '종료 확인', '마지막 장에서 끝내기를 한 번 더 눌러야 끝난다.'),
      state('exam-confirm', '실전 모드 종료 확인', '실전 모드를 유지하며 종료를 확인한다.'),
      state('keyword-confirm', '키워드 모드 종료 확인', '키워드 모드를 유지하며 종료를 확인한다.'),
      state('disconnected', '연결 끊김', '말하기 분석 연결이 끊겨도 발표는 계속하도록 안내한다.'),
      state(
        'opening',
        '시작 전 · 장치 여는 중',
        '카메라 · 마이크를 여는 동안. 시계 · 끝내기는 멈춰 있다.',
      ),
      state('connecting', '시작 전 · 연결 중', '말하기 분석 서버에 붙는 동안.'),
      state(
        'connect-slow',
        '시작 전 · 연결 늦음',
        '연결이 늦어 다시 시도 · 장치 점검으로를 함께 보인다. 연결되면 바로 시작한다.',
      ),
      state(
        'no-mic',
        '시작 전 · 마이크 없음',
        '마이크 권한 · 장치 문제 문구와 함께 시작을 막는다.',
      ),
      state(
        'busy',
        '시작 전 · 다른 곳에서 연습 중',
        '다른 기기 · 창의 연습을 끝낸 뒤 다시 시도하게 한다.',
      ),
    ],
    rules: [
      '끝내기("발표 끝내기")는 마지막 장에서만 열린다. 마지막 장이 아니면 막히고 버튼 바로 옆에 "마지막 장에서 끝낼 수 있어요"를 글자로 둔다(버튼의 aria-describedby). 막힌 버튼은 초점을 받지 못하고 title 툴팁도 잘 보이지 않아서다. 장 수를 모르면(0) 막지 않는다. 중간 종료는 없다 — BE가 중간 종료를 구분하지 않아 일부만 한 발표도 점수가 남기 때문이다.',
      '슬라이드는 ← → · Space로 넘긴다.',
      '한 번 누르면 "정말 끝낼까요?"로 바뀌고 4초 안에 다시 눌러야 끝난다. 장이 바뀌면 확인이 풀린다 — 이전 장에 갔다가 4초 안에 돌아와도 처음부터 다시 묻는다. 화면을 덮는 창은 띄우지 않는다.',
      '시작 뒤 말하기 분석 연결이 끊겨도 시계와 발표는 계속된다. 시작 전에 다른 곳에서 발표 중이라 연결이 거절되면 연결 실패 대신 그 이유를 알린다.',
      '이 Take를 다른 창에서 하고 있으면 무대를 열지 않고 "다른 창에서 연습 중"을, 창이 닫혀 끝났으면 "연습 중단"을 안내한다. 다른 곳에서 새 연습을 시작해 끝났으면 새 Take를 권하지 않는다.',
      '밝은 무대에서 슬라이드는 16:9 틀에 원본 전체를 넣고 카메라는 4:3 중앙 크롭으로 채운다. 테두리는 셋 다 실선이라, 범례에서 지금 상태 글자를 먹색 칩으로 뒤집어 색 없이도 구분한다.',
      '코치 행은 메시지·대기·실전 모두 56px이며 피코와 크림색 지면을 유지한다. 대기 안내는 평가가 아니고, 실전 모드는 코치 OFF다. 대본 예약 행 180px와 모드별 상자 180/120/0px, 모드 간 슬라이드 좌표는 고정한다.',
      '종료 버튼은 상단 진행바 오른쪽, 상태·종료 제한 이유와 키 안내는 코치 바로 아래 중앙 한 줄에 둔다. 슬라이드 이동 버튼은 두지 않는다. 대본 예약 행은 높이 180px를 유지한 채 화면 맨 아래에 둔다.',
      '실전 모드는 대본 칸을 그리지 않는다.',
    ],
    components: ['stage.css 무대 배치', '대본 칸(ScriptPane)', '코치 줄(CoachStrip)'],
    tokens: ['stage', 'stage-panel', 'ink-stage', 'gaze-audience', 'script-highlight'],
    sources: [
      'frontend/src/features/rehearsal/stage/RehearsalPage.tsx',
      'frontend/src/features/rehearsal/stage/ScriptPane.tsx',
      'frontend/src/dev/stage/CoachStrip.tsx',
      'frontend/src/features/rehearsal/stage/stage.css',
      'frontend/src/features/rehearsal/stage/canEnd.ts',
      'frontend/src/features/rehearsal/stage/useEndConfirm.ts',
    ],
    productNote:
      '제품은 PM 승인 전의 어두운 무대·카메라 16:9 contain·코치 32px·판정 불가 점선을 유지한다. 이 시안의 흰 지면·카메라 4:3 크롭·56px 피코 코치·범례 칩·상단 종료·하단 대본 배치는 승인 후 별도 반영한다. 배치 이동만으로 시선 오분류 해결을 보장하지 않는다. 제품 적용 시 새 배치에서 재보정한 뒤 대본 위·가운데·아래를 읽어 판정을 검증해야 한다. 카메라 영상 · 음량 막대 · PDF 슬라이드 대신 자리표시를 둔다. 코치 상태 표시는 그리지 않는다. 시작 전 안내는 "시작 전 · …" 상태로, 무대를 열지 않는 안내는 "무대를 열지 않는 안내" 화면으로 본다. 종료 확인은 4초 뒤 풀리지 않고 유지된다(장을 넘기면 풀리는 것은 제품과 같다). 무대 배치는 설계서 자체 src/ui/stage.css를 쓴다. /dev/stage도 제품 UI와 분리된 디자인 시안으로 유지한다.',
    productLinks: [REHEARSAL, link('리허설 · 실전 모드', '/takes/t3/exam')],
    next: [next('끝내기 → 분석 대기', 'processing-loading'), next('전송 실패 → 재시도', 'retry')],
  },
  {
    kind: 'screen',
    id: 'processing-loading',
    title: '분석 대기',
    group,
    route: '/takes/:takeId/processing',
    status: '제품에 있음',
    summary: '노을 화면에서 분석 세 단계의 진행을 보여 주고, 끝나면 리포트로 넘어간다.',
    states: [
      state('default', '분석 중', '세 단계 중 지금 단계와 치치의 팁을 보여 준다.'),
      state('loading', '불러오는 중', '연습 기록을 불러오는 동안 안내한다.'),
      state(
        'confirm',
        '대본 확인 대기',
        '비슷하게 들린 말이 있어 대본 확인으로 가는 버튼을 띄운다.',
      ),
      state('failed', '분석 실패', '다시 연습하기와 홈으로를 안내한다.'),
      state('held', '리뷰 보류', '분석이 멈춰 나중에 홈에서 확인하도록 안내한다.'),
      state('error', '오류', '연습 기록을 불러오지 못했다.'),
    ],
    rules: [
      '분석이 끝나면 리포트로 자동 이동한다(주소를 바꿔 끼움).',
      '확인 대기면 "비슷하게 들린 말 확인하기"로 대본 확인을 연다. 답이 없으면 10분 안에 그대로 진행된다.',
      '분석 실패는 다시 연습하기(시작 전 세팅) · 홈으로, 리뷰 보류는 홈으로만 안내한다.',
      '화면을 떠나도 분석은 계속되고 홈에서 다시 볼 수 있다.',
    ],
    components: ['DuskScreen', 'StatusLine', 'ButtonLink'],
    tokens: ['dusk', 'dusk-line', 'dusk-ink', 'dusk-accent'],
    sources: [
      'frontend/src/features/report/ProcessingPage.tsx',
      'frontend/src/features/report/DuskScreen.tsx',
    ],
    productNote:
      '팁은 바뀌지 않고 첫 문장에 머물며, 제품의 "팁 자동 전환 일시정지" 버튼은 뺐다. 분석 완료 뒤 자동 이동은 아래 "이어지는 화면"의 "분석 완료 → 리포트"로 대신한다.',
    productLinks: [
      PROCESSING,
      link('분석 실패', '/takes/t-failed/processing'),
      link('리뷰 보류', '/takes/t-held/processing'),
      link('대본 확인 대기', '/takes/t-confirm/processing', '확인 화면으로 가는 버튼이 뜬다'),
    ],
    next: [
      next('분석 완료 → 리포트', 'report'),
      next('대본 확인', 'confirm'),
      next('홈으로', 'home'),
    ],
  },
  {
    kind: 'screen',
    id: 'confirm',
    title: '대본 확인',
    group,
    route: '/takes/:takeId/confirm',
    status: '제품에 있음',
    summary: '발표 뒤 인식이 헷갈린 말을 "대본대로 / 들린 대로"로 확인하고 분석 대기로 돌아간다.',
    states: [
      base,
      state('loading', '불러오는 중', '확인할 말을 불러오는 동안 안내한다.'),
      state('error', '오류', '확인할 말을 불러오지 못해 다시 불러오기와 분석 대기로를 안내한다.'),
      state('timeout', '시간 초과', '확인 시간이 끝나 답 없이 진행됨을 알린다.'),
      state('closed', '이미 넘어감', '확인 시간이 끝났거나 이미 답해 확인할 말이 없다.'),
    ],
    rules: [
      '녹음 다시 듣기는 없다. 음성을 저장하지 않으므로 그 말이 든 문장을 보여 준다.',
      '답은 한 번에 보내고, 보내거나 건너뛰면 분석 대기로 돌아간다.',
      '고르지 않은 말과 "잘 모르겠어요"는 보류로 남고 내용 점수는 잠정값을 유지한다.',
      '확인 대기가 시작된 지 10분이 지나면 답 없이 진행된다.',
    ],
    components: ['DuskScreen', 'StatusLine', 'Button'],
    tokens: ['dusk', 'dusk-ink', 'dusk-accent'],
    sources: [
      'frontend/src/features/report/ConfirmPage.tsx',
      'frontend/src/features/report/DuskScreen.tsx',
    ],
    productNote:
      '남은 시간은 줄지 않는 예시 값이다. 확인 항목은 하나만 그린다. 제품의 "답을 반영하는 중" · "확인을 받을 수 없어요" 상태는 그리지 않는다.',
    productLinks: [
      link(
        '대본 확인',
        '/takes/t-confirm/confirm',
        '답하거나 건너뛰면 분석 대기를 거쳐 리포트로 간다',
      ),
      link('대본 확인 · 시간 초과', '/takes/t-confirm-timeout/confirm', '8초 뒤 시간이 끝난다'),
      link('대본 확인 · 이미 넘어감', '/takes/t-confirm-done/confirm'),
    ],
    next: [next('분석 대기로', 'processing-loading')],
  },
  {
    kind: 'screen',
    id: 'retry',
    title: '업로드 실패 · 재시도',
    group,
    route: '/takes/:takeId/retry',
    status: '제품에 있음',
    summary: '종료 기록을 서버로 보내지 못했을 때 이 브라우저에 남은 기록을 다시 보낸다.',
    states: [
      state('default', '보내지 못함', '남은 기록을 보여 주고 다시 보내기를 권한다.'),
      state('checking', '기록 확인 중', '이 브라우저의 발표 기록을 찾는 동안 안내한다.'),
      state('sending', '다시 보내는 중', '다 보낼 때까지 창을 열어 두도록 안내한다.'),
      state('empty', '남은 기록 없음', '이 브라우저에 다시 보낼 기록이 없을 때 안내한다.'),
      state('gone', '서버에서 끝남', '서버가 이미 끝낸 Take라 새 Take 준비로 안내한다.'),
    ],
    rules: [
      '다시 보낼 기록은 이 브라우저(IndexedDB)의 사본을 쓴다. 없으면 안내만 한다.',
      '서버가 이미 끝낸 Take는 다시 보내지 않고 새 Take 준비로 보낸다.',
      '"나중에 할게요"는 홈으로 간다. 서버는 몇 분 뒤 그 Take를 끝낸다.',
    ],
    components: ['InfoPage 틀', 'Card', 'Stat', 'Button', 'Mascot'],
    tokens: ['greige', 'panel', 'ink', 'coral-deep'],
    sources: ['frontend/src/features/rehearsal/retry/RetryPage.tsx'],
    productNote:
      '남은 기록 수치(발표 길이 · 시선 기록 · 슬라이드 이동)는 예시 값이다. 기록 확인 중 · 다시 보내는 중 · 기록 없음 · 서버에서 이미 끝남 상태의 문구는 제품과 같다.',
    productLinks: [RETRY],
    next: [
      next('다시 보내기 → 분석 대기', 'processing-loading'),
      next('새 Take 준비', 'prepare-mission'),
      next('내 피치로', 'home'),
    ],
  },
  {
    kind: 'screen',
    id: 'report',
    title: 'Take 리포트',
    group,
    route: '/takes/:takeId',
    status: '제품에 있음',
    summary: '한 Take의 코칭 노트 · 지난 Take와의 변화 · 측정값 · 다음 미션을 보여 준다.',
    states: [
      base,
      state('loading', '불러오는 중', '연습 기록을 불러오는 동안 안내한다.'),
      state('error', '오류', '연습 기록을 불러오지 못했다.'),
      state('no-gaze', '시선 측정 제외', '재지 않은 시선은 0%가 아니라 —로 둔다.'),
      state('no-review', '코치 총평 실패', '받은 측정값은 그대로 두고 총평이 없음을 알린다.'),
      state(
        'survey-sent',
        '설문 보냄',
        '이 리포트의 설문을 냈다. 아래 줄에 버튼 대신 보냈다는 문장만 둔다.',
      ),
    ],
    rules: [
      '점수(score)는 보여 주지 않는다. 요약과 지난 Take와의 변화를 먼저 보여 준다.',
      '리뷰가 없거나 실패해도 측정값은 먼저 보여 준다.',
      '측정하지 않은 값은 —로 두어 측정된 0과 구별한다.',
      '"Take N 연습하기"는 시작 전 세팅으로 이동만 한다. 여기서 Take를 만들지 않는다.',
      '분석 실패 · 리뷰 보류 Take는 분석 대기 화면으로 보낸다.',
      '아래 줄에 "이번 분석 설문 남기기"를 둔다(#776). 분석이 끝난 리포트에만 두고, 설문 상태를 못 받으면 버튼을 두지 않아 리포트를 막지 않는다. 리포트마다 한 번 보낸다.',
    ],
    components: ['Card', 'Stat', 'Badge', 'ButtonLink', '설문 창'],
    tokens: ['greige', 'panel', 'ink', 'coral-deep'],
    sources: [
      'frontend/src/features/report/ReportPage.tsx',
      'frontend/src/features/report/ReportFrame.tsx',
      'frontend/src/features/report/TakeSurvey.tsx',
    ],
    productNote:
      '제품은 평가기준 · 고칠 것 · 잘한 점 · 미션 결과 · 영역별 상태 카드를 더 둔다. 미리보기는 코칭 노트 · 지난 Take와 비교 · 측정값 · 시선 비율 · 다음 미션만 그린다. 설문은 서버에 보내지 않고, 보내기를 누르면 "설문 보냄" 상태로 간다.',
    productLinks: [
      link('Take 리포트', '/takes/t3'),
      link('AI 문장 실패', '/takes/t-fallback'),
      link('리뷰 실패', '/takes/t-review-failed'),
      link('시선 측정 제외', '/takes/t2'),
    ],
    next: [next('Take 3 연습하기', 'prepare-mission'), next('내 피치로', 'home')],
  },
  {
    kind: 'screen',
    id: 'takes',
    title: 'Take 기록',
    group,
    route: '/takes',
    status: '제품에 있음',
    summary: '모든 피치의 연습 기록을 최신순으로 보고 리포트나 진행 화면으로 간다.',
    states: [
      base,
      state('loading', '불러오는 중', '연습 기록을 가져오는 동안 안내한다.'),
      state('empty', '기록 없음', '첫 Take를 권하고 내 피치로 안내한다.'),
      state('error', '오류', '연습 기록을 가져오지 못해 다시 불러오기를 안내한다.'),
    ],
    rules: [
      '피치 선택은 주소(?pitch=)에만 둔다. 홈의 "Take 기록" 링크가 그 피치로 걸러 연다.',
      '완료 Take는 리포트, 분석 중 Take는 진행 보기로 간다.',
      '측정하지 않은 응시는 —로 둔다.',
    ],
    components: ['InfoPage 틀', 'Card', 'TakeStatusBadge', 'Mascot'],
    tokens: ['greige', 'panel', 'ink'],
    sources: [
      'frontend/src/features/report/TakesPage.tsx',
      'frontend/src/shared/lib/takeStatus.ts',
    ],
    productNote: '상태 배지는 TakeStatusBadge와 같은 Badge로 그린다.',
    productLinks: [link('Take 기록', '/takes')],
    next: [next('리포트', 'report'), next('내 피치로', 'home')],
  },
  {
    kind: 'screen',
    id: 'comparison',
    title: 'Take 비교',
    group,
    route: '/pitch/:id/comparison',
    status: '제품에 있음',
    summary: '같은 피치의 완료된 Take를 지표별 흐름과 표로 비교한다.',
    states: [
      base,
      state('loading', '불러오는 중', '연습 기록을 가져오는 동안 안내한다.'),
      state('empty', '완료 Take 하나', '비교하려면 Take가 2개 이상 필요하다고 안내한다.'),
      state('error', '피치 없음', '지워졌거나 열 수 없는 피치를 안내한다.'),
    ],
    rules: [
      '완료된 Take만 비교한다. 둘 미만이면 한 번 더 연습하자고 안내한다.',
      '측정하지 않았거나 시선 엔진이 다른 Take는 —로 두고 비교하지 않는다.',
    ],
    components: ['InfoPage 틀', 'Card', 'Mascot'],
    tokens: ['greige', 'panel', 'ink', 'coral-deep'],
    sources: ['frontend/src/features/report/ComparisonPage.tsx'],
    productNote: '지표 탭은 청중 응시 하나만 그리고, 제품의 Take별 기록 표는 그리지 않는다.',
    productLinks: [
      link('Take 비교', COMPARISON),
      link('완료 Take 없음', '/pitch/p2/comparison'),
      link('찾을 수 없는 피치', '/pitch/00000000-0000-4000-8000-000000000000/comparison'),
    ],
    next: [next('Take 기록', 'takes'), next('내 피치로', 'home')],
  },
];

const infoGroup = '안내 · 계정 화면';

/** 글 위주 화면. 제품 `shared/ui/InfoPage` 틀을 쓴다 */
const infoScreens: GuideScreen[] = [
  {
    kind: 'screen',
    id: 'stage-blocked',
    title: '무대를 열지 않는 안내',
    group: infoGroup,
    route: '/takes/:takeId/rehearsal',
    status: '제품에 있음',
    summary: '리허설 주소로 왔지만 무대를 열지 않을 때 이유와 갈 곳을 알린다.',
    states: [
      state(
        'other-window',
        '다른 창에서 연습 중',
        '이 Take를 다른 창에서 하고 있다. 그 창으로 돌아가게 한다.',
      ),
      state('closed', '창이 닫혀 중단', '창을 닫아 끝난 Take다. 새 Take 준비를 권한다.'),
      state(
        'replaced',
        '다른 곳에서 새 연습',
        '다른 곳에서 새 Take를 시작해 끝났다. 그쪽이 이번 연습이라 새 Take를 권하지 않는다.',
      ),
      state(
        'not-started',
        '시작 안 됨',
        '서버 연결이 끝내 붙지 않아 시작하지 않은 Take로 정리됐다.',
      ),
    ],
    rules: [
      '무대를 열지 않은 이유 하나와 갈 곳만 둔다. 여기서는 아무것도 바꾸지 않는다.',
      '창을 닫아 끝났거나 시작하지 못했으면 "새 Take 준비"(시작 전 세팅)를, 다른 곳이 이번 연습이면 "내 피치로"만 둔다.',
    ],
    components: ['InfoPage 틀(로고 줄)', 'Card', 'Mascot', 'ButtonLink'],
    tokens: ['greige', 'panel', 'ink'],
    sources: ['frontend/src/features/rehearsal/stage/RehearsalPage.tsx'],
    productNote: '문구 · 버튼은 제품과 같다(LeftWindowNotice · NotStartedNotice).',
    productLinks: [REHEARSAL],
    next: [next('새 Take 준비', 'prepare-mission'), next('내 피치로', 'home')],
  },
  {
    kind: 'screen',
    id: 'about',
    title: '분석 방식 설명',
    group: infoGroup,
    route: '/about',
    status: '제품에 있음',
    summary: '무엇을 어디서(브라우저 · 서버) 분석하는지 알린다. 로그인 없이 열린다.',
    states: [base],
    rules: [
      '점수가 아니라 지난번보다 나아진 것을 본다는 말을 먼저 둔다.',
      '분석마다 어디서 하는지(이 브라우저 안 · 서버)를 적는다.',
    ],
    components: ['InfoPage 틀(로고 줄)', 'Card', 'Mascot'],
    tokens: ['greige', 'panel', 'ink', 'coral-deep'],
    sources: ['frontend/src/features/info/AboutPage.tsx', 'frontend/src/shared/ui/InfoPage.tsx'],
    productNote: '문구 · 배치는 제품과 같다.',
    productLinks: [link('분석 방식 설명', '/about')],
    next: [next('로고 · 내 피치', 'home')],
  },
  {
    kind: 'screen',
    id: 'unsupported',
    title: '미지원 브라우저',
    group: infoGroup,
    route: '/unsupported',
    status: '제품에 있음',
    summary: '이 브라우저 · 기기에서 연습할 수 없는 이유와 쓸 브라우저를 알린다.',
    states: [
      state('browser', '시선 분석 불가', '시선 분석에 필요한 브라우저 기능이 없다.'),
      state('mobile', '터치 기기', '터치만 되는 기기라 PC에서 연습하라고 알린다.'),
      state('supported', '연습 가능', '이 주소로 바로 들어왔지만 이 브라우저는 연습할 수 있다.'),
    ],
    rules: [
      '사유는 이 화면이 직접 고른다. 시선 분석 기능이 없으면 그게 먼저고, 그다음 터치 전용 기기(`pointer: coarse`)다.',
      '지금 제품에는 이 화면으로 자동으로 보내는 곳이 없다. 연습은 PC 정책이고, 이 화면이 그 안내다.',
      '"링크 복사"는 주소를 복사하고, 복사할 수 없으면 주소를 직접 옮기라고 알린다.',
    ],
    components: ['InfoPage 틀(로고 줄)', 'Card', 'Button'],
    tokens: ['greige', 'panel', 'ink', 'stone'],
    sources: [
      'frontend/src/features/info/UnsupportedPage.tsx',
      'frontend/src/features/info/unsupportedReason.ts',
    ],
    productNote: '"링크 복사"는 샘플에서 클립보드를 쓰지 않고 복사 결과 문구도 띄우지 않는다.',
    productLinks: [
      link('미지원 브라우저', '/unsupported', '이 브라우저에 맞는 사유 하나만 보인다'),
    ],
    next: [next('내 피치로', 'home')],
  },
  {
    kind: 'screen',
    id: 'not-found',
    title: '찾을 수 없음',
    group: infoGroup,
    route: '*',
    status: '제품에 있음',
    summary: '없는 주소로 들어오면 보이는 공통 화면이다.',
    states: [base],
    rules: ['목록에 없는 모든 주소가 이 화면이다. 홈으로 돌아가는 버튼 하나만 둔다.'],
    components: ['InfoPage 틀(로고 줄)', 'Card', 'Mascot', 'ButtonLink'],
    tokens: ['greige', 'panel', 'ink'],
    sources: ['frontend/src/features/info/NotFoundPage.tsx'],
    productNote: '문구 · 배치는 제품과 같다.',
    productLinks: [link('찾을 수 없음', '/없는-주소')],
    next: [next('내 피치로', 'home')],
  },
  {
    kind: 'screen',
    id: 'me',
    title: '마이페이지',
    group: infoGroup,
    route: '/me',
    status: '제품에 있음',
    summary:
      '계정 정보, 연습 기록 사용 동의, 이 브라우저 기록 지우기, 의견 보내기, 계정 삭제를 둔다.',
    states: [
      state('default', '동의 안 함', '연습 기록 사용 동의를 하지 않은 기본 상태다.'),
      state('consented', '동의함', '연습 기록을 남겨 다시 분석하는 데 동의했다.'),
    ],
    rules: [
      '상단바 아래에 놓여 로고 줄 없이 제목만 둔다. 로그아웃은 상단바, 연습 요약은 홈에 있다.',
      '동의를 바꾸면 다음에 시작하는 연습부터 적용한다.',
      '브라우저 기록 지우기 · 계정 삭제는 브라우저 확인 창으로 한 번 더 묻는다.',
    ],
    components: ['InfoPage 틀(logo=false)', 'Card', 'Button', 'SuggestionForm'],
    tokens: ['greige', 'panel', 'ink', 'stone', 'coral-deep'],
    sources: [
      'frontend/src/features/auth/MyPage.tsx',
      'frontend/src/features/auth/SuggestionForm.tsx',
    ],
    productNote:
      '의견 보내기 칸(SuggestionForm)은 자리만 둔다. 동의 · 지우기 · 삭제는 서버 · 브라우저에 아무것도 하지 않고, 확인 창과 결과 · 오류 문구("동의 설정을 바꾸지 못했어요" · "이 브라우저의 기록을 지웠어요" · "삭제하는 중…" 등)도 그리지 않는다.',
    productLinks: [link('마이페이지', '/me')],
    next: [next('내 피치로', 'home')],
  },
];

const onboardingGroup = '처음 화면 · 로그인';

/** 로그인 전에 보는 화면. 웰컴과 로그인은 같은 머리 · 테두리 · 그림자를 쓴다 */
const onboardingScreens: GuideScreen[] = [
  {
    kind: 'screen',
    id: 'welcome',
    title: '웰컴 · 서비스 소개',
    group: onboardingGroup,
    route: '/welcome',
    status: '제품에 있음',
    summary: '서비스를 소개하고 로그인으로 보낸다. 공개 전에는 카운트다운과 "오픈 예정"을 보인다.',
    states: [
      state('default', '공개 뒤', '"시작하기"가 로그인으로 간다.'),
      state(
        'before-launch',
        '공개 전',
        '공개 시각(2026-10-30 14:00 한국 시간)까지 카운트다운을 보이고 시작 버튼을 "오픈 예정"으로 막는다.',
      ),
    ],
    rules: [
      '머리의 메뉴는 같은 쪽 안 구역(서비스 소개 · 사용방법 · 연구실 소개 · FAQ · 이용안내)으로 스크롤하고, 보고 있는 구역을 표시한다.',
      '시작 버튼은 공개 전에는 막혀 "오픈 예정"이고, 공개 뒤에는 로그인(`/login?next=/`)으로 간다.',
      '랜딩만 큰 제목(font-black · extrabold)과 움직임(landing-float · landing-wave)을 쓴다. 동작 줄이기를 켜면 멈춘다.',
    ],
    components: [
      'PitchCoachWordmark',
      'LaunchStart · LaunchCountdown',
      'ComfortScene',
      'details(FAQ)',
    ],
    tokens: ['panel', 'ink', 'coral-deep', 'coral-wash', 'cream'],
    sources: [
      'frontend/src/features/onboarding/WelcomePage.tsx',
      'frontend/src/features/onboarding/LaunchCountdown.tsx',
      'frontend/src/shared/api/launch.ts',
    ],
    productNote:
      '움직이는 장면(ComfortScene) · 코칭 예시 · 연구실 소개 글 · 구성원 카드는 자리만 둔다. 카운트다운 숫자는 넘어가지 않는 예시 값이다. 머리 메뉴의 "보고 있는 구역" 표시와 바닥글은 그리지 않는다.',
    productLinks: [link('웰컴', '/welcome')],
    next: [next('시작하기 → 로그인', 'login')],
  },
  {
    kind: 'screen',
    id: 'login',
    title: '로그인',
    group: onboardingGroup,
    route: '/login',
    status: '제품에 있음',
    summary:
      'Google 계정으로 로그인한다. 세션 확인 · Google로 이동 · 취소 · 실패를 상태 줄에 알린다.',
    states: [
      state('default', '기본', '로그인 버튼과 약관 안내.'),
      state('checking', '세션 확인 중', '로그인 상태를 확인하는 동안 버튼을 막는다.'),
      state('check-failed', '세션 확인 실패', '연결을 확인하라고 알리고 "연결 다시 확인"을 둔다.'),
      state('redirecting', 'Google로 이동 중', '버튼을 막고 이동 중이라고 알린다.'),
      state('cancelled', '로그인 취소', 'Google에서 취소하고 돌아왔다(auth_error=access_denied).'),
      state('failed', '로그인 실패', '그 밖의 이유로 로그인을 끝내지 못했다.'),
    ],
    rules: [
      '이미 로그인했으면 목적지(next)로 바로 보낸다. 외부 주소는 목적지로 받지 않는다.',
      '상태 줄은 role="status"로 한 줄만 보인다.',
      '정책 문서가 검토용 초안이라 "동의한 것으로 본다"고 쓰지 않고 확인만 안내한다.',
      '문구가 합니다체다(다른 화면은 해요체) — 문구 규칙의 "섞인 곳".',
    ],
    components: ['PitchCoachWordmark', 'Google 로그인 버튼 그림'],
    tokens: ['panel', 'ink', 'cream', 'line-strong'],
    sources: ['frontend/src/features/auth/LoginPage.tsx'],
    productNote:
      '로그인 버튼은 Google로 가지 않고 홈 샘플로 간다. 개발 환경의 "화면 미리보기 환경" 안내는 그리지 않는다.',
    productLinks: [link('로그인', '/login')],
    next: [next('로그인 → 홈', 'home'), next('서비스 소개', 'welcome')],
  },
];

/** 목록 순서 그대로 왼쪽 목록에 보인다: 기초 → 컴포넌트 · 대화 상자 → 화면 */
export const entries: GuideEntry[] = [
  ...foundationEntries,
  ...componentEntries,
  ...productScreens,
  ...infoScreens,
  ...onboardingScreens,
];

export const screens: GuideScreen[] = [...productScreens, ...infoScreens, ...onboardingScreens];
