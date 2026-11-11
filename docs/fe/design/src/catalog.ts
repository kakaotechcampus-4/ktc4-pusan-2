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
  commit: '3aca4eb31201e98d71d4ad55d28b37e2d5279a59',
  date: '2026-11-11',
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
// /pitches, /pitch/:id/prepare, /pitch/:id/comparison은 호환 라우트다.
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

/** 화면 그룹은 표시용이다. 기존 화면 ID와 공유 링크는 바꾸지 않는다. */
export const GUIDE_GROUP_ORDER: readonly string[] = [
  '기초',
  '컴포넌트',
  '대화 상자',
  '서비스 시작',
  '피치 관리 · 생성',
  '연습 준비 · 진행',
  '분석 · 기록 · 리포트',
  '안내 · 계정',
  '검토 시안',
];

const screenGroup = {
  pitch: '피치 관리 · 생성',
  rehearsal: '연습 준비 · 진행',
  results: '분석 · 기록 · 리포트',
  review: '검토 시안',
} as const;

const productScreens: GuideScreen[] = [
  {
    kind: 'screen',
    id: 'home',
    title: '홈 · 내 피치',
    group: screenGroup.pitch,
    route: '/',
    status: '제품에 있음',
    // #944 사용방법 안내와 #1105 튜토리얼의 머지 결과를 반영한다.
    summary:
      '피치 목록과 연습 통계를 보고 발표 준비 · 새 피치 만들기 · 피치 수정 · Take 기록으로 간다. 비교는 Take 기록 안에서 연다. 줄마다 더보기(···) 메뉴에서 피치를 지운다. 데이터는 예시다.',
    states: [
      base,
      state(
        'prepare-entry-draft',
        '발표 준비 버튼 · 기존 디자인',
        '공유 주소 호환용 상태로, 승인된 코랄 강조 발표 준비 버튼을 기본 홈과 동일하게 보여 준다.',
      ),
      state(
        'sample',
        '튜토리얼 샘플 완료',
        '샘플 피치는 샘플 배지를 붙이고 편집 대신 튜토리얼 화면으로 이동한다. 완료한 경우 PRESS START 강조는 숨긴다.',
      ),
      state('loading', '불러오는 중', '발표 노트를 가져오는 동안 안내한다.'),
      state(
        'empty',
        '첫 피치 전',
        '통계는 빼고 첫 피치 안내만 둔다. 푸터의 TIP과 사용방법 버튼은 유지한다.',
      ),
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
      state(
        'delete-menu',
        '더보기 메뉴 · 시안',
        '시안(#807). 첫 줄의 ··· 메뉴를 연 상태. 항목은 "삭제" 하나이고 초점이 그 항목에 있다.',
      ),
      state(
        'delete-blocked',
        '삭제 막힘 · 시안',
        '시안(#807). BE가 지울 수 없다고(`can_delete: false`) 한 피치(셋째 줄)의 메뉴. 삭제를 aria-disabled로 막고 이유를 항목 아래에 적는다.',
      ),
      state(
        'delete-confirm',
        '삭제 확인 · 시안',
        '시안(#807). 피치 제목, 되돌릴 수 없음, 함께 지워지는 Take 기록 수 · 리포트 · 자료와, 지워지지 않고 남는 설문 답을 알린다. 처음 초점은 취소.',
      ),
      state(
        'delete-pending',
        '삭제 중 · 시안',
        '시안(#807). 지우는 동안 취소 · 삭제를 aria-disabled로 막고 Esc도 막는다. 버튼 글이 "삭제하는 중…"으로 바뀐다.',
      ),
      state(
        'delete-error',
        '삭제 실패 · 시안',
        '시안(#807). 창을 닫지 않고 실패를 알린다. 줄은 목록에 그대로 있고, 삭제를 다시 누를 수 있다.',
      ),
    ],
    rules: [
      '일반 피치의 발표 준비는 같은 피치 수정 경로로 연결한다. 튜토리얼 샘플(`is_demo`)은 제목에 샘플 배지를 붙이고 편집 링크를 두지 않으며 발표 준비 버튼은 `/pitch/tutorial`로 이동한다(#1105).',
      'Take 기록 영역은 고정 너비를 써서 연습 횟수가 달라도 각 열을 위아래로 맞춘다.',
      '발표 준비 버튼은 #801에서 승인한 coral-deep 강조형으로 유지한다. #1117의 secondary 변경은 미병합 종료되었으며 적용하지 않는다. 작은 크기, 행 정렬 및 피치 수정 경로는 유지한다.',
      '피치 제목은 피치 수정, "Take 기록"은 그 피치로 거른 Take 기록을 연다.',
      '"튜토리얼 해 보기"는 머지된 #1105의 튜토리얼 화면으로 이어진다. 설계서의 홈은 아직 샘플을 끝내지 않은 계정의 PRESS START 별 스티커를 시연한다. 실제 제품은 리포트를 받지 않았고 샘플 Take를 끝까지 완료하지 않았다면 초대한다. 단순히 샘플 피치 존재 여부만으로 판단하지 않는다.',
      '누적 발표 시간은 길이를 아는 Take만 더하고, 하나도 모르면 —로 둔다.',
      '설문은 홈에서 받지 않는다(#776). 동의 전이면 참여 안내, 동의 뒤면 남은 설문 링크만 두고 설문은 리포트에서 한다. 자동으로 띄우는 창은 없다.',
      '참여에 동의하면 카드가 사라지고 완료 안내로 초점이 옮겨 간다.',
      '남은 설문은 동의한 뒤 끝난 Take 만 오고, 최대 3개를 보이고 나머지는 "외 N개"로 둔다. 없으면 아무것도 두지 않는다.',
      '피치 삭제(#807)는 발표 준비 오른쪽 더보기(···) 메뉴 안에 둔다. 되돌릴 수 없는 동작이라 발표 준비와 같은 버튼 줄에 두지 않는다. ···는 36px 정사각형이라 열 정렬을 바꾸지 않는다.',
      '···는 메뉴 버튼이다(aria-haspopup="menu" · aria-expanded). Enter · Space · ↓로 열면 첫 항목, ↑면 마지막 항목에 초점이 가고 ↑ ↓ Home End로 옮긴다. Esc는 닫고 ···로 초점을 돌려준다. Tab · 바깥 누르기 · 스크롤은 그냥 닫는다.',
      '메뉴는 화면 좌표로 띄운다. 페이지 스크롤 중에도 마지막 줄에서 잘리지 않도록 아래 자리가 모자라면 위로 연다.',
      '연습이나 분석이 도는 중이면 삭제를 aria-disabled로 막고 "연습이나 분석이 끝난 뒤 지울 수 있어요."를 항목 아래에 적는다(aria-describedby). 홈 목록의 BE `can_delete`로 판단하고(#1057), BE도 DELETE를 409 `PITCH_BUSY`로 막는다. 분석이 보류되거나 멈춘 Take만 남은 피치는 BE가 허용해 지울 수 있다. 값이 없으면 막지 않고 BE의 409에 맡긴다.',
      '확인 창에서 삭제를 누르면 DELETE를 바로 보낸다. 창을 연 사이 연습이 시작돼 409 `PITCH_BUSY`를 받으면 지우지 않고 창 안에 알리며("피치를 삭제하지 못했어요."를 붙이지 않는다), 홈 목록을 다시 받아 메뉴를 막는다.',
      '확인 창은 제목 · 되돌릴 수 없음 · 함께 지워지는 것(Take 기록 N개와 리포트, 발표 자료 · 대본 · 평가 기준)을 적는다. 버튼은 취소 · 삭제, 처음 초점은 취소다.',
      '지우는 동안 두 버튼을 aria-disabled로 막고 Esc도 막는다. 두 번 눌러도 요청은 한 번이다. 실패하면 창과 줄을 그대로 두고 알린다(role="alert").',
      '취소하면 ···로 초점을 돌려준다. 지우면 그 줄을 빼고, 같은 자리에 온 다음 줄(마지막 줄이었으면 윗줄)의 제목, 줄이 없으면 "내 피치" 제목으로 초점을 옮기고 화면 낭독기에 지웠다고 알린다.',
      '홈 푸터는 TIP · 사용방법 살펴보기 · 브랜드 슬로건 · 이용약관 · 개인정보 처리방침을 배치한다. 좁은 화면에서는 TIP 문장 전체를 버튼 아래에 줄바꿈해 표시하고, 넓은 화면에서는 한 행에 배치한다. 영문 슬로건은 2xl 이상에서만 표시한다. 실제 채팅 런처가 있을 때만 좁은 화면의 푸터 오른쪽 여백을 확보한다. TIP에는 채팅 에이전트에서 지난 Take 기록이나 발표 방법을 물어보는 안내를 포함한다. TIP은 8초 간격으로 바뀌며 일시정지·재개할 수 있고, 자동 전환 문구에는 aria-live를 걸지 않는다. 움직임 감소 설정은 처음뿐 아니라 실행 중 변경도 반영한다.',
    ],
    components: [
      'HomePitchCard',
      '더보기 메뉴(PitchMoreMenu)',
      '피치 삭제 창',
      '통계 3칸',
      '푸터(TIP 자동 전환 · 사용방법 · 이용약관 · 개인정보 처리방침)',
      '참여 안내 카드 · 참여 동의 창',
      '남은 설문 줄',
    ],
    tokens: ['greige', 'panel', 'ink', 'coral-deep', 'line-strong', 'coral-wash'],
    sources: [
      'frontend/src/features/pitch/home/HomePage.tsx',
      'frontend/src/features/pitch/home/HomeSurveyNotice.tsx',
      'frontend/src/features/pitch/home/HomePitchCard.tsx',
      'frontend/src/features/pitch/home/PitchMoreMenu.tsx',
      'frontend/src/features/pitch/home/DeletePitchDialog.tsx',
      'frontend/src/features/pitch/home/homeFormat.ts',
      'frontend/src/shared/ui/HomeHelpDialog.tsx',
      'frontend/src/shared/api/pitch.ts',
    ],
    productNote:
      '통합 푸터에는 11개의 예시 TIP(8초 간격 순환·일시정지), 사용방법 링크와 정책 링크를 표시한다. TIP은 피치가 없어도 표시한다. 정책 링크는 설계서의 terms·privacy 화면으로 이동한다(?from=home의 돌아가기 분기는 별도 재현하지 않는다). 통계값은 고정 예시이고 피치 목록은 내부 스크롤 대신 페이지 스크롤을 쓴다. 참여 동의 창은 대화 상자의 "참여 동의 창"에서도 본다. 동의 · 남은 설문은 서버에 묻지 않는 예시다. 기존 "prepare-entry-draft" 상태 ID는 공유 링크 호환을 위해 유지하며, 승인된 코랄 강조 버튼을 기본 상태와 동일하게 표시한다. 삭제(#807)는 서버에 묻지 않는다 — 기본 상태에서 지우면 이 미리보기에서만 줄이 빠지고, 누르는 순간 다시 묻는 것과 지운 뒤 초점 이동은 그리지 않는다. 제품 목에서는 콘솔에 mockPitchDelete.fail()을 입력하면 삭제가 실패한다.',
    productLinks: [link('홈 · 내 피치', '/')],
    next: [
      next('새 피치 만들기', 'pitch-create', 'empty'),
      next('피치 수정', 'pitch-create'),
      next('Take 기록', 'takes'),
    ],
  },
  {
    kind: 'screen',
    id: 'semini',
    title: '새미나이 · 채팅 패널',
    group: '서비스 시작',
    route: '/',
    status: '제품에 있음',
    summary:
      '실제 제품에 적용된 오른쪽 하단 런처와 우측 400px 사이드 패널의 대화 범위, 질문 예시와 메시지 입력을 시연한다.',
    states: [
      base,
      state('history', '대화 목록', '입력창 없이 이전 대화 목록 자리를 표시한다.'),
      state('memory', '기억 설정', '입력창 없이 기억 설정 자리를 표시한다.'),
      state('closed', '채팅 닫힘', '오른쪽 하단 런처만 표시한다.'),
      state('pitch-scope', '현재 피치 범위', '현재 피치를 대화 범위로 지정한 모양이다.'),
      state('memory-off', '기억 없이 대화', '기억 없이 대화하기 옵션을 켠 모양이다.'),
    ],
    rules: [
      '런처는 오른쪽 하단에 고정한다. 패널이 열리면 런처를 숨기고, 닫힌 뒤 런처에 초점을 복원한다. 패널은 오른쪽 400px이며 1440px 이상에서는 본문을 밀고, 761~1439px 및 모바일에서는 본문 위에 겹친다. 장치 점검·리허설·실전과 개인정보 처리 미동의 상태에서는 런처를 표시하지 않는다(#1171, #1197, #1204).',
      '제품에서 질문 예시를 누르면 해당 질문을 바로 전송한다. 설계서의 정적 미리보기는 API를 호출하지 않고 입력창에만 채운다.',
      '대화 범위와 기억 없이 대화하기는 독립적으로 동작한다. 새 대화에서 선택 직후에는 스위치로 상태를 알리고, 제품은 전송·대화 생성이 시작되면 스위치 대신 기억 없이 배지를 표시한다.',
      '정적 미리보기의 기억 없이 대화 상태는 메시지 전송 전의 스위치 선택만 재현한다. 목록·기억 설정·패널 닫기 전환 시 선택을 유지하지만 실제 대화 생성·배지는 시연하지 않는다.',
      '대화 목록과 기억 설정에서는 입력 폼을 숨기고 작성 중인 초안을 보존한다.',
      '실제 API·메시지 기록·기억 설정은 호출하지 않는다.',
    ],
    components: ['새미나이 런처', 'ChatPanelHeader', '질문 예시', '메시지 입력 composer'],
    tokens: ['panel', 'ink', 'coral-deep', 'cream'],
    sources: [
      'frontend/src/features/chat/ui/ChatAgentHost.tsx',
      'frontend/src/features/chat/ui/ChatPanelHeader.tsx',
      'frontend/src/features/chat/ui/ChatEmptyState.tsx',
      'frontend/src/features/chat/ui/ChatAskHelp.tsx',
    ],
    productNote:
      '채팅 패널의 독립 UI 샘플로, 실제 채팅 API·오류 경계·라우트별 런처 오프셋·본문의 완전한 반응형 재배치는 재현하지 않는다. 오른쪽 400px 패널과 열림 중 런처 숨김은 시연한다. 제품의 질문 예시는 클릭 즉시 전송되지만 설계서에서는 입력창에만 채운다. 화면 상태 선택은 프리셋이고 패널 내부 조작은 로컬 상태로만 시연한다.',
    productLinks: [link('홈 · 채팅 레이어', '/')],
    next: [next('홈', 'home')],
  },
  {
    kind: 'screen',
    id: 'tutorial',
    title: '튜토리얼 · 샘플 발표',
    group: '서비스 시작',
    route: '/pitch/tutorial',
    status: '제품에 있음',
    // 머지된 #1105의 샘플 준비·장치 점검·리포트 사용 제한을 재현한다.
    summary:
      '머지된 #1105의 BE 전용 튜토리얼 API로 준비하는 샘플 발표와 장치 점검 진입, 리포트 사용 후 제한을 시연한다.',
    states: [
      base,
      state('pending', '샘플 준비 중', '튜토리얼 시작 후 진행 문구와 비활성화된 버튼을 보여 준다.'),
      state(
        'used',
        '리포트 이미 사용함 · 샘플 기록 있음',
        '이미 받은 샘플 리포트를 알리고 새 피치 생성 및 샘플 Take 기록 링크를 보여 준다.',
      ),
      state(
        'used-no-pitch',
        '리포트 이미 사용함 · 샘플 삭제됨',
        '이미 받은 샘플 리포트를 알리고 새 피치 생성만 안내한다.',
      ),
      state('error', '샘플 준비 실패', '제품의 기본 오류 안내를 읽을 수 있게 보여 준다.'),
    ],
    rules: [
      '샘플은 BE `POST /pitches/tutorials/first-take`가 5장의 슬라이드·대본을 자동 준비한다. `is_demo`로 식별하며 리포트는 계정당 한 번이다.',
      '설계서에서는 서버 요청 없이 화면 문구와 상태만 시연한다. 제품은 응답의 report_received 또는 pitch_id가 null일 때 사용 완료를 판정하고, 샘플 피치 ID가 있으면 해당 Take 기록 링크도 보여 준다.',
      'BE는 샘플 중복 생성을 막고 기존 샘플을 재사용한다. 리포트는 계정당 한 번 받을 수 있으며, 리포트 전 중단한 연습은 다시 시도할 수 있다. 실제 발표 시작 시에는 409 TUTORIAL_REPORT_USED로도 차단한다.',
    ],
    components: ['5장 미션 카드', '진행·오류 상태', '장치 점검 진입'],
    tokens: ['greige', 'panel', 'ink', 'coral-deep'],
    sources: ['frontend/src/features/pitch/tutorial/FirstTakePage.tsx'],
    productNote:
      '실제 API를 호출하지 않고 시작 버튼으로 장치 점검 미리보기로 이동한다. 제품 #1105는 BE 전용 API를 사용하며 `report_received`와 `pitch_id`에 따라 완료·차단 및 샘플 기록 연결이 달라진다. 설계서 기록 버튼은 샘플 피치 ID별 URL 필터를 모사하지 않는다.',
    productLinks: [link('튜토리얼', '/pitch/tutorial')],
    next: [next('시작 전 세팅', 'prepare-mission'), next('홈', 'home')],
  },
  {
    kind: 'screen',
    id: 'pitch-create-a',
    title: '피치 생성 · A안 검토 시안',
    group: screenGroup.review,
    route: '/pitch/new',
    status: '시안',
    summary:
      '제품에 채택된 한 페이지형 피치 생성 A안(#802)의 검토 기록이다. 지금은 "피치 생성 · 수정"(pitch-create)과 같은 샘플을 그리고, 공유 링크 호환을 위해 ID와 상태 주소만 남긴다.',
    states: [
      state('default', '시안 · 슬라이드 준비', '예시 슬라이드를 올린 뒤 대본을 연결한다.'),
      state('empty', '시안 · 빈 화면', '모든 섹션은 보이고 슬라이드부터 시작한다.'),
      state(
        'upload-review',
        '시안 · PDF 확인',
        '업로드 후 자동으로 열린 창에서 PDF를 보고 확인 완료를 누른다.',
      ),
      state(
        'final-review',
        '시안 · 최종 확인',
        '확인한 자료·대본·발표정보·평가기준을 모아 보고 시작 전 점검으로 이동한다.',
      ),
      state('error', '시안 · 업로드 실패', '예시 오류와 다시 시도 동작을 확인한다.'),
      state('mapping-error', '시안 · 매핑 실패', '대본은 보존하고 다시 매핑할 수 있다.'),
      state(
        'mapping',
        '시안 · 매핑 확인',
        '장별 예시 슬라이드와 대본을 큰 창에서 확인하고 수정한다.',
      ),
      state('ready', '시안 · 준비 완료', '필수 세 항목을 저장한 뒤 시작 전 점검으로 이동한다.'),
      state(
        'focus-outline',
        '시안 · 현재 단계 강조',
        '현재 피치 생성 제품 샘플과 같은 섹션 배경·강조 테두리를 표시한다(옛 공유 주소 호환용).',
      ),
      state('many', '시안 · 버전 30개', '긴 버전 목록의 스크롤과 읽기 전용 열람을 확인한다.'),
    ],
    rules: [
      '규칙 · 제품과 다른 점은 "피치 생성 · 수정"(pitch-create)에 적는다. 두 곳에 적지 않는다.',
    ],
    components: [],
    tokens: [],
    sources: [],
    next: [next('현재 제품 샘플', 'pitch-create')],
  },
  {
    kind: 'screen',
    id: 'pitch-create',
    title: '피치 생성 · 수정',
    group: screenGroup.pitch,
    route: '/pitch/new',
    status: '제품에 있음',
    summary:
      '슬라이드 · 대본 연결 · 발표정보 · 평가기준(선택)을 한 페이지에서 준비한다. 왼쪽 준비 순서와 자료 버전 목록, 고정 하단 확인 버튼을 쓴다. 생성과 수정은 같은 구성을 공유한다.',
    states: [
      state('default', '슬라이드 준비', '예시 슬라이드를 올린 뒤 대본을 연결한다.'),
      state('empty', '새 피치 · 자료 없음', '슬라이드 업로드 전에도 네 영역이 모두 보인다.'),
      state('error', '슬라이드 업로드 실패', 'PDF 업로드 오류를 표시하고 재선택할 수 있다.'),
      state(
        'preview',
        '슬라이드 확인 · 옛 주소',
        '옛 preview 상태 주소. 슬라이드 확인(upload-review)과 같은 창을 연다.',
      ),
      state(
        'upload-review',
        '슬라이드 확인',
        '업로드 후 자동으로 열린 창에서 PDF를 보고 확인 완료를 누른다.',
      ),
      state(
        'mapping',
        '대본 매핑 확인',
        '장별 예시 슬라이드와 대본을 큰 창에서 확인하고 수정한다.',
      ),
      state('mapping-error', '대본 매핑 실패', '대본은 보존하고 다시 매핑할 수 있다.'),
      state(
        'final-review',
        '최종 확인',
        '확인한 자료·대본·발표정보·평가기준을 모아 보고 시작 전 세팅으로 이동한다.',
      ),
      state('ready', '준비 완료', '필수 세 항목을 저장한 뒤 시작 전 세팅으로 이동한다.'),
      state('focus-outline', '현재 단계 강조', '선택하거나 입력 중인 한 영역만 테두리로 표시한다.'),
      state('many', '버전 30개', '긴 버전 목록의 스크롤과 읽기 전용 열람을 확인한다.'),
    ],
    rules: [
      '슬라이드, 대본 연결, 발표정보, 평가기준(선택)을 한 페이지에 세로로 두고 준비 순서 버튼으로 해당 영역에 이동한다. 단계마다 화면을 갈아끼우지 않는다. 날짜 항목은 없고 시간 편차는 0초/0초로 시작한다.',
      '슬라이드·발표정보 섹션은 panel 55%와 cream을 섞은 배경, 대본 연결·평가기준 섹션은 cream 배경을 쓴다. 섹션 사이 구분선은 없고 입력칸은 흰색을 유지한다.',
      '기본·빈 화면·오류·확인 등 모든 상태에서 준비 순서 선택·본문 클릭·키보드 입력에 따라 작업 중인 한 섹션의 1px coral-deep 테두리를 이동한다. focus-outline 상태는 기존 공유 주소 호환용이다.',
      '자료 버전 관리와 내부 폴더는 처음에 모두 접혀 있고, 펼쳐도 사이드바 위치는 유지한다.',
      '버전 창은 PDF 예시·장별 대본·평가기준을 읽기 전용으로 보여 준다. 현재 본문과 연결 조합은 바뀌지 않는다. 버전 트리에 발표정보·발표 자료 제목·새 버전 버튼은 없다.',
      '연결할 슬라이드 메뉴는 파일명 없이 버전과 장수만 표시한다. 매핑이 끝나면 큰 확인·수정 창을 자동으로 연다. 이미 매핑한 대본은 연결 메뉴를 숨긴다.',
      '수정 대본 매핑 중에는 버튼에 초점을 유지한다. 완료 후 그 버튼이 사라졌다면 저장 버튼이나 저장할 수 없는 이유로 초점을 옮긴다. 첫 장과 마지막 장에서도 이동 버튼에 초점을 유지한다.',
      '매핑 창은 목록·선택 장의 슬라이드·편집 대본을 함께 보여 주며, 편집값은 닫고 다시 열어도 유지한다. 수정하면 다시 매핑하고 저장해야 한다.',
      'PDF 업로드 후 확인 창을 자동으로 연다. 닫기만 하면 확인 필요로 남고, 확인 완료를 눌러야 필수 준비 완료에 포함한다. 버전 보기 창에는 저장 동작이 없다.',
      '평가기준은 선택 항목이다. 입력하면 정리 결과를 확인하고 저장해야 하고, 다시 수정하면 재확인이 필요하다. 비워 두면 사용하지 않는다.',
      '평가기준 안내 칩 8개는 선택 입력이 아니라 예시 툴팁이다. 호버·포커스·클릭으로 목록 아래에 하나만 열리고 Esc로 포커스를 유지하며 닫힌다(#1102). 실제 정리·저장은 서버에서 하고 샘플은 고정 결과만 보여 준다.',
      '고정 하단 버튼은 필수 세 항목과 사용한 평가기준을 확인·저장한 뒤 최종 확인 창을 연다. 최종 확인 뒤 /pitch/:pitchId/device-check로 간다. 이 화면은 Take를 만들지 않는다 — Take는 장치 점검의 시작 버튼에서 만든다.',
    ],
    components: [
      'VersionRail · 준비 순서',
      'SlidePane · ScriptPane · InfoPane · CriteriaPane',
      '버전 보기 · 슬라이드 확인 · 매핑 확인 · 최종 확인 창',
      '고정 하단 준비 버튼',
    ],
    tokens: ['greige', 'cream', 'panel', 'ink', 'coral-deep', 'line-strong'],
    sources: [
      'frontend/src/features/pitch/create/PitchCreatePage.tsx',
      'frontend/src/features/pitch/create/VersionRail.tsx',
      'frontend/src/features/pitch/create/steps/SlidePane.tsx',
      'frontend/src/features/pitch/create/steps/ScriptPane.tsx',
      'frontend/src/features/pitch/create/steps/InfoPane.tsx',
      'frontend/src/features/pitch/create/steps/CriteriaPane.tsx',
      'frontend/src/features/pitch/create/FinalReviewDialog.tsx',
      'frontend/src/features/pitch/create/VersionPreviewDialog.tsx',
      'frontend/src/features/pitch/create/lib/draft.ts',
    ],
    productNote:
      '제품과 같은 한 페이지 구조(#802 · 현재 단계 강조와 최종 확인 창까지)를 그린다. 제품과 다른 점: 목표 발표 시간은 제품이 1~60분(편차 ±10초 버튼, 1초 단위)이고 샘플은 30분까지(30초 단위)다. 제품의 슬라이드 제목줄에는 N장·확인 상태 배지·새 버전 버튼이 있고, 샘플은 파일 요약 아래에 확인 상태 배지만 그린다. 대본의 구분 수 · 글자 수 · 예상 시간 안내와 장수 불일치 안내, 업로드 실패 사유별 문구가 있지만 샘플은 그리지 않는다. 제품의 읽기용 버전 창은 실제 PDF를 그린다. PDF 확인 상태는 FE 메모리에만 둔다. 이번 화면에서 새로 업로드한 미확인 버전만 확인 필요로 보고, 서버에서 불러온 기존 버전은 확인 완료로 본다. 확인 전 새로고침해도 수정 화면에서 다시 불러오면 확인 완료가 되는 한계가 있다. PDF는 업로드 때 이미 서버에 저장되므로 확인 완료 버튼은 추가 저장 API를 부르지 않는다. 슬라이드는 PDF를 읽지 않고 3장의 예시 자료를 그린다. 입력한 원문은 화면에 보관하고, 매핑과 평가기준 정리 결과는 고정 fixture로 표현한다. 피치 수정의 자료 불러오기 실패 · 없는 피치 상태는 그리지 않는다.',
    productLinks: [link('새 피치', '/pitch/new'), link('피치 수정', PITCH_EDIT)],
    next: [next('최종 확인 → 시작 전 세팅', 'prepare-mission'), next('내 피치로', 'home')],
  },
  {
    kind: 'screen',
    id: 'prepare-mission',
    title: '시작 전 세팅',
    group: screenGroup.rehearsal,
    route: '/pitch/:pitchId/device-check',
    status: '제품에 있음',
    summary:
      '카메라 · 마이크 · 시선 기준 · 기준 목소리를 점검하고 대본 표시를 고른 뒤, 이 화면의 시작 버튼에서 Take를 만들어 리허설을 연다.',
    states: [
      state(
        'default',
        '점검 완료',
        '카메라 · 마이크 · 시선 기준 · 기준 목소리가 모두 돼 시작할 수 있다.',
      ),
      state(
        'voice-required',
        '기준 목소리 전',
        '나머지 점검이 끝나도 기준 목소리를 재지 않으면 시작할 수 없다(#1184).',
      ),
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
      '위에 공용 상단바(AppHeader)가 얹히고, 그 아래 본문은 자체 틀(ScreenFrame)을 쓴다(#694). 상단바 높이(약 85px)를 빼고도 폭 1024px 이상 · 높이 606px 이상에서 페이지 스크롤 없이 시작 버튼까지 보이도록, 높이 749px 이하에서는 여백을 줄이고(compact) 659px 이하에서는 점검 행 · 미션 칸 · 카메라 열 폭(640px)까지 줄인다(low). 내용은 가운데 정렬 그대로다(왼쪽 위로 옮길지는 #706에서 정한다).',
      'Take는 이 화면의 시작 버튼에서만 만든다. 들어오는 것만으로는 만들지 않는다.',
      '카메라 영상 · 마이크 입력 · 시선 기준 · 기준 목소리 넷이 모두 되고, 이번 연습의 슬라이드 · 대본 조합이 정해져야(제품 `choice !== null`) 시작한다. 카메라가 없으면 시작할 수 없다. 테스트 기간에는 Take 시작 전에 연습 기록 사용 동의를 켜야 하며 실패하면 시작하지 않는다(#1208).',
      '안내 줄은 지금 막고 있는 것 하나만 말한다. 순서는 시작 실패 → 시작을 누른 뒤 여는 중(다른 곳의 연습을 끝낼지 묻는 동안은 그 안내) → 준비 정보 문제(불러오기 실패 · 버전 없음 등) → 평소 목소리 읽는 중 → 장치 오류 → 시선 분석을 쓸 수 없음 → 카메라 → 마이크 → 시선 분석 준비 중 → 기준 잡기 실패 → 기준 미완료 → 기준이 흐릿함(막지 않고 권하기만) → 기준 목소리 측정 필요 → "점검이 끝났어요"다.',
      '안내 줄 아래에는 "말이 없는 상태가 2분 넘게 이어지면 연습이 자동으로 끝날 수 있어요. 발표 목표 시간과는 상관없어요."를 늘 둔다(#925). 서버는 최근 2분 무발화와 최근 5분 중 80% 이상 무발화를 모두 볼 때 끝내므로 "끝날 수 있어요"로 쓴다. 바뀌지 않는 말이라 안내 줄의 알림 영역(role="status") 밖이다. 2분은 서버가 미리 알려 주지 않아 제품 `shared/lib/autoEnd.ts`에 두고 BE `take_silence_end_sec`과 맞춘다.',
      '기준 목소리는 연습 시작 조건이다(#1184). 이번에 재거나, 같은 탭·같은 마이크·자동 음량 조절 꺼짐·30분 이내에 측정된 값이면 그대로 쓸 수 있다. 읽기에 두 번 실패하면 안내된 대체 기준으로 시작할 수 있다. 재사용은 시작할 때 한 번 더 확인한다(#1179).',
      '미션 전문 창에서 첫 미션과 마지막 미션에 도달하면 해당 탐색 버튼의 동작을 막고 초점은 유지한다.',
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
      'frontend/src/features/rehearsal/prepare/MissionCard.tsx',
      'frontend/src/features/rehearsal/prepare/activeTake.ts',
      'frontend/src/features/rehearsal/prepare/prepareStore.ts',
    ],
    productNote:
      '카메라 미리보기 · 시선 기준 전체 화면 · 장치 선택 · 음량 막대 · 기준 목소리 읽기·재사용·대체 시작 패널은 그리지 않고 자리만 둔다. 시선 기준의 "누르면 시작"은 기준을 다 잡은 상태로 바로 넘어간다. 안내 줄은 카메라 · 마이크 · 시선 기준 · 기준 목소리 네 갈래를 그린다(제품 순서는 동작 규칙). 다른 곳의 연습을 끝낼지 묻는 창은 그리지 않는다. 시작 조건은 제품과 같다.',
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
    title: '시작 전 세팅 · 배치 검토 시안',
    group: screenGroup.review,
    route: '/pitch/:pitchId/device-check',
    status: '시안',
    summary:
      '닫힌 구현 PR #789를 대체하는 디자인 검토용 독립 시안. PM 승인 전이며 제품 반영 완료를 뜻하지 않는다. 장비·미션·대본 표시 배치만 검토한다.',
    states: [
      state(
        'default',
        '장비 연결 전',
        '시안: 필수 0/4. 사진처럼 이름 옆 상태와 오른쪽 선택창을 둔다.',
      ),
      state('ready', '점검 완료', '시안: 필수 4/4. 기준 목소리를 마쳐야 시작 버튼을 켠다.'),
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
        '기준 목소리 읽는 중',
        '시안: 실제 측정 없이 문단·예시 음량·다 읽었어요 버튼을 표시한다.',
      ),
      state(
        'voice-done',
        '평소 목소리 완료',
        '시안: 필수 단계 완료 안내와 미션 전문 접근을 유지한다.',
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
      '필수 준비 수는 제목 옆, 카메라·마이크 켜기는 오른쪽 위에 둔다. 기준 목소리를 필수 항목으로 표시한다.',
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
    title: '리허설 · 실전',
    group: screenGroup.rehearsal,
    route: '/takes/:takeId/rehearsal',
    status: '제품에 있음',
    // 병합된 #1118의 RUNNING 조건·배치·반응형 규칙을 따른다.
    summary:
      '리허설과 실전 모드의 슬라이드·대본·코치·연결 상태를 독립적으로 재현한 화면이다. 실제 카메라·마이크·음성 전송은 하지 않는다.',
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
      '실제 발표 RUNNING 상태에서만 STAGE LIVE 배지를 표시하며 진입 연출은 180ms 한 번만, 움직임 감소 환경에서는 애니메이션 없이 표시한다(#1118). 슬라이드는 ← → · Space, 슬라이드 영역 좌클릭(다음 장)·우클릭(이전 장)으로 넘긴다(#1192).',
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
      'frontend/src/features/rehearsal/stage/SlideClickArea.tsx',
      'frontend/src/features/rehearsal/stage/ScriptPane.tsx',
      'frontend/src/features/rehearsal/stage/CoachStrip.tsx',
      'frontend/src/features/rehearsal/stage/stage.css',
      'frontend/src/features/rehearsal/stage/canEnd.ts',
      'frontend/src/features/rehearsal/stage/useEndConfirm.ts',
    ],
    productNote:
      '제품에도 흰 지면·카메라 4:3 크롭·56px 피코 코치·범례 칩·상단 종료·하단 대본 배치를 반영했다. 제품의 보정 키는 stage:bottom-script-v2로 이전 배치의 기준을 제외한다. 배치 이동만으로 시선 오분류 해결을 보장하지 않는다. 실기기에서 새 배치로 재보정한 뒤 대본 위·가운데·아래를 읽어 판정을 검증해야 한다. 카메라 영상 · 음량 막대 · PDF 슬라이드 대신 자리표시를 둔다. 코치 상태 표시는 그리지 않는다. 시작 전 안내는 "시작 전 · …" 상태로, 무대를 열지 않는 안내는 "리허설 · 접근 안내" 화면으로 본다. 종료 확인은 4초 뒤 풀리지 않고 유지된다(장을 넘기면 풀리는 것은 제품과 같다). 무대 배치는 설계서 자체 src/ui/stage.css를 쓴다. /dev/stage도 제품 UI와 분리된 디자인 시안으로 유지한다.',
    productLinks: [REHEARSAL, link('리허설 · 실전 모드', '/takes/t3/exam')],
    next: [next('끝내기 → 분석 대기', 'processing-loading'), next('전송 실패 → 재시도', 'retry')],
  },
  {
    kind: 'screen',
    id: 'processing-loading',
    title: '분석 대기',
    group: screenGroup.results,
    route: '/takes/:takeId/processing',
    status: '제품에 있음',
    // 병합된 #1118의 TAKE RECORDED 표시 조건을 따른다.
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
      state(
        'auto-end',
        '자동 종료 뒤',
        '서버가 말 없음으로 Take를 끝내고 넘어와, 분석 진행 위에 그 이유를 알린다.',
      ),
      state('error', '오류', '연습 기록을 불러오지 못했다.'),
    ],
    rules: [
      'TAKE RECORDED · 발표 기록 접수는 서버 Take 상태가 ANALYZING일 때만 표시한다(#1118). 확인 대기(AWAITING_CONFIRMATION)·불러오는 중·실패·보류 상태에서는 표시하지 않는다. 설계서의 기본/자동 종료 뒤 예시는 ANALYZING, 대본 확인 대기 예시는 AWAITING_CONFIRMATION을 나타낸다.',
      '분석이 끝나면 리포트로 자동 이동한다(주소를 바꿔 끼움).',
      '서버가 Take를 자동으로 끝냈으면(말 없음 · 최대 1시간) 무대가 이유와 서버 문구를 라우터 state로 넘기고, 이 화면 맨 위에 role="alert" 안내("자동 종료" 이름표 + 서버 문구 + 덧붙임)를 띄운다(#925). 리포트로 넘어갈 때도 같이 넘겨 리포트 위에 한 번 더 보인다. 끝내기 버튼으로 끝냈으면 띄우지 않는다.',
      '확인 대기면 "비슷하게 들린 말 확인하기"로 대본 확인을 연다. 답이 없으면 10분 안에 그대로 진행된다.',
      '분석 실패는 다시 연습하기(시작 전 세팅) · 홈으로, 리뷰 보류는 홈으로만 안내한다.',
      '화면을 떠나도 분석은 계속되고 홈에서 다시 볼 수 있다.',
    ],
    components: ['DuskScreen', 'StatusLine', 'ButtonLink'],
    tokens: ['dusk', 'dusk-line', 'dusk-ink', 'dusk-accent'],
    sources: [
      'frontend/src/features/report/analysis/ProcessingPage.tsx',
      'frontend/src/features/report/analysis/DuskScreen.tsx',
      'frontend/src/features/report/AutoEndAlert.tsx',
      'frontend/src/shared/lib/autoEnd.ts',
    ],
    productNote:
      '팁은 바뀌지 않고 첫 문장에 머물며, 제품의 "팁 자동 전환 일시정지" 버튼은 뺐다. 분석 완료 뒤 자동 이동은 아래 "이어지는 화면"의 "분석 완료 → 리포트"로 대신한다. 자동 종료 안내는 말 없음 문구만 그리고(최대 시간도 같은 상자에 서버 문구만 다르다), 리포트 위에 한 번 더 뜨는 안내는 그리지 않는다. 제품 목 서버로는 라우터 state를 넣을 수 없어 "제품에서 열기"로 이 상태를 볼 수 없다.',
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
    group: screenGroup.results,
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
      'frontend/src/features/report/analysis/ConfirmPage.tsx',
      'frontend/src/features/report/analysis/DuskScreen.tsx',
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
    group: screenGroup.results,
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
    group: screenGroup.results,
    route: '/takes/:takeId',
    status: '제품에 있음',
    summary:
      '한 Take의 코칭 노트 · 지난 Take와의 변화 · 측정값 · 내용 전달 · 다음 미션을 보여 준다.',
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
      state(
        'content-open',
        '내용 전달 · 장 펼침',
        '장별 목록의 3장을 펼쳤다. 대본 문장 판정 · 내가 말한 문장의 대본 밖 구간 · 핵심 수치를 보인다.',
      ),
      state(
        'content-estimated',
        '내용 전달 · 간이 장만',
        '채점된 장 없이 간이 장만 있다. 전달률 숫자 대신 간이 단계를 모아 보인다.',
      ),
      state(
        'content-none',
        '내용 전달 · 미제공',
        '대본 전달도 상세가 아직 없다(BE 배포 전 응답). 숫자 없이 안내만 둔다.',
      ),
      state('content-analyzing', '내용 전달 · 분석 중', '대본 전달도를 분석하고 있다.'),
    ],
    rules: [
      '지난 Take 비교 본문은 지표별 개선/악화 슬라이드 요약을 보여 주고, 개선된 슬라이드 · 발표 전체 비교 박스와 배지는 #1176의 코랄 강조를 유지한다. 상세 비교표는 별도 모달에서 제공한다(#1120). 점수(score)는 보여 주지 않는다.',
      '지난 Take 비교에서 발표 전체 전용 지표는 이전 · 이번 값과 개선 판정을 한 박스에 넣는다. 개선된 슬라이드 또는 개선 판정의 발표 전체 박스만 코랄로 강조한다. 악화·변화 없음·판단 불가는 중립색과 문구로 구분한다.',
      '리뷰가 없거나 실패해도 측정값은 먼저 보여 준다.',
      '측정하지 않은 값은 —로 두어 측정된 0과 구별한다.',
      '"Take N 연습하기"는 시작 전 세팅으로 이동만 한다. 여기서 Take를 만들지 않는다.',
      '분석 실패 · 리뷰 보류 Take는 분석 대기 화면으로 보낸다.',
      '아래 줄에 "이번 분석 설문 남기기"를 둔다(#776). 분석이 끝난 리포트에만 두고, 설문 상태를 못 받으면 버튼을 두지 않아 리포트를 막지 않는다. 리포트마다 한 번 보낸다. 보내면 완료 안내로 초점이 옮겨 간다.',
      '내용 전달 카드는 측정값 다음에 둔다(#1154). 대표 숫자는 핵심 내용 전달률이고, 다르게 말한 몫은 따로 보인다. 리뷰 비교의 기존 점수는 "핵심 내용 점수(기존 방식)"로 이름을 나눈다.',
      '내용 전달의 비율은 null을 0%로 바꾸지 않고 —와 이유를 보인다. 간이 · 추정 값은 배지로 구분하고, 막대는 글자 범례와 %를 함께 둔다.',
      '지난 Take 대비는 평가 기능 버전이 같고 양쪽 전달률이 숫자일 때만 보인다.',
      '장별 목록은 "장별 보기 (N장)"로 접어 두어 장이 많아도 카드가 길어지지 않게 한다. 열어서 줄을 누르면 그 장을 펼치고, 그때만 장별 상세를 부른다. 결과가 바뀌었다는 응답(409)이면 리포트를 다시 읽고 새 판으로 다시 부른다.',
    ],
    components: ['Card', 'Stat', 'Badge', 'ButtonLink', '설문 창'],
    tokens: ['greige', 'panel', 'ink', 'coral-deep', 'coral-wash'],
    sources: [
      'frontend/src/features/report/report/ReportPage.tsx',
      'frontend/src/features/report/report/ReportFrame.tsx',
      'frontend/src/features/report/report/TakeSurvey.tsx',
      'frontend/src/features/report/report/ContentCard.tsx',
      'frontend/src/features/report/report/SlideCoverage.tsx',
      'frontend/src/features/report/report/SpeechMixBlock.tsx',
      'frontend/src/features/report/report/contentFormat.ts',
    ],
    productNote:
      '제품은 평가기준 · 고칠 것 · 잘한 점 · 미션 결과 · 영역별 상태 카드를 더 둔다. 미리보기는 코칭 노트 · 지난 Take와 비교 · 측정값 · 내용 전달 · 시선 비율 · 다음 미션만 그린다. 설문은 서버에 보내지 않고, 보내기를 누르면 "설문 보냄" 상태로 간다. 내용 전달은 #1152 예시 숫자로 간추려 그린다 — 제품의 4칸 · 말의 구성 막대 대신 글자 범례만 두고, 수치 칩 · 0일 때 설명 · 측정 불가 · 실패 상태와 장 펼침의 핵심 내용 표 · 간이 장 펼침은 그리지 않는다.',
    productLinks: [
      link('Take 리포트', '/takes/t3'),
      link('AI 문장 실패', '/takes/t-fallback'),
      link('리뷰 실패', '/takes/t-review-failed'),
      link('시선 측정 제외', '/takes/t2'),
      link('내용 전달 · 0 과 확인 대기', '/takes/t-content-zero'),
      link('내용 전달 · 간이 장만', '/takes/t-content-estimated'),
      link('내용 전달 · 미제공', '/takes/t-content-none'),
    ],
    next: [next('Take 3 연습하기', 'prepare-mission'), next('내 피치로', 'home')],
  },
  {
    kind: 'screen',
    id: 'takes',
    title: 'Take 기록 · 지표 비교',
    group: screenGroup.results,
    route: '/takes',
    status: '제품에 있음',
    summary: '하나의 Take 기록 화면에서 전체 목록과 같은 피치의 지표 비교를 전환한다.',
    states: [
      base,
      state('compare', '지표 비교 · 2개', '완료된 Take 두 개의 지표 흐름과 표를 표시한다.'),
      state('compare-empty', '지표 비교 · 1개', '비교에는 완료된 Take 두 개가 필요하다고 알린다.'),
      state('compare-zero', '지표 비교 · 0개', '완료된 Take가 아직 없을 때를 보여 준다.'),
      state('loading', '불러오는 중', '연습 기록을 가져오는 동안 안내한다.'),
      state('empty', '기록 없음', '첫 Take를 권하고 내 피치로 안내한다.'),
      state('error', '오류', '연습 기록을 가져오지 못해 다시 불러오기를 안내한다.'),
    ],
    rules: [
      '기록 목록 / 지표 비교 전환을 사용한다. 뷰는 ?view=compare, 피치는 ?pitch=로 주소에 남긴다.',
      '피치 선택은 #1143의 커스텀 select-only combobox다. 열린 목록에는 2px 먹색 테두리·오프셋 그림자, 선택 체크·강조색, 긴 제목 줄바꿈이 있고 좁은 높이에서는 위로 열린다. ↑ ↓ Home End로 이동, Enter·Space로 선택, Esc·Tab으로 변경 없이 닫는다. 선택 시 ?pitch=가 바뀌며 스크롤 위치는 유지된다.',
      '목록은 모든 피치를 선택할 수 있으나 비교는 한 피치만 고른다. 다른 피치의 Take를 섞지 않는다.',
      '비교에는 완료된 Take만 사용한다. 둘 미만이면 한 번 더 연습하자고 안내한다. 1개라면 해당 리포트로 이동할 수 있다.',
      '지표 비교 그래프는 Take가 적을 때 최대 너비를 제한하고 막대는 3rem을 넘지 않게 한다. 전체 지표 표는 아래에 전체 너비로 유지한다(#1119). 비교 표의 각 완료 Take에는 개별 리포트 보기 링크를 둔다. 주소는 /takes/:takeId를 사용한다.',
      '측정하지 않았거나 시선 엔진이 다른 Take는 —로 표시하고 비교하지 않는다. 말하기 속도는 높고 낮음으로 판정하지 않는다.',
      '기존 /pitch/:id/comparison 주소는 /takes?pitch=:id&view=compare로 리다이렉트한다.',
    ],
    components: [
      'InfoPage 틀',
      'Card',
      'TakeStatusBadge',
      'Mascot',
      'PitchSelect',
      '지표 탭',
      '지표 흐름 · 표',
    ],
    tokens: ['greige', 'panel', 'ink', 'coral-deep'],
    sources: [
      'frontend/src/features/report/history/TakesPage.tsx',
      'frontend/src/features/report/history/PitchSelect.tsx',
      'frontend/src/features/report/history/TakeComparison.tsx',
      'frontend/src/features/report/history/TakeComparisonRedirect.tsx',
      'frontend/src/features/report/history/takeStatus.ts',
    ],
    productNote:
      '지표 비교의 두 Take 값은 예시다. 제품은 여러 Take를 가로 스크롤 차트와 표로 보여 준다. 샘플 지표 버튼은 제품과 같은 이름·방향을 사용하며, 표의 각 리포트 보기에서 해당 Take 리포트로 이동한다.',
    productLinks: [
      link('기록 목록', '/takes'),
      link('한 피치의 지표 비교', '/takes?pitch=10000000-0000-4000-8000-000000000001&view=compare'),
      link('이전 비교 주소(리다이렉트)', COMPARISON),
    ],
    next: [next('리포트', 'report'), next('내 피치로', 'home')],
  },
  {
    kind: 'screen',
    id: 'comparison',
    title: 'Take 비교 · 이전 주소',
    group: screenGroup.results,
    route: '/pitch/:id/comparison',
    status: '제품에 있음',
    summary: '옛 주소에서 /takes?pitch=:id&view=compare로 이동하는 호환 경로다.',
    states: [base, state('empty', '완료 Take 하나', '통합 화면에서 비교할 Take가 부족한 상태다.')],
    rules: [
      '새 비교 화면은 따로 만들지 않는다. 동일한 Take 기록 화면의 지표 비교 모드를 사용한다.',
      '기존 주소는 replace 방식으로 이동하여 뒤로 가기로 옛 주소를 다시 열지 않는다.',
    ],
    components: ['Take 기록 · 지표 비교'],
    tokens: ['greige', 'panel', 'ink', 'coral-deep'],
    sources: ['frontend/src/features/report/history/TakeComparisonRedirect.tsx'],
    productNote: '호환 주소의 화면은 통합 Take 기록 화면과 같은 샘플을 사용한다.',
    productLinks: [link('이전 비교 주소(리다이렉트)', COMPARISON)],
    next: [next('통합 Take 기록', 'takes', 'compare'), next('내 피치로', 'home')],
  },
];

const infoGroup = '안내 · 계정';

/** 안내 화면과 공개 정책 문서. 정책 문서는 제품 PolicyPage를 독립 샘플로 옮긴다. */
const infoScreens: GuideScreen[] = [
  {
    kind: 'screen',
    id: 'terms',
    title: '이용약관 · 검토용 초안',
    group: infoGroup,
    route: '/terms',
    status: '제품에 있음',
    summary:
      '제품에 공개 경로가 있는 시행 전 이용약관 초안. 서비스 목적과 자료 권리, 장치 권한, 삭제 및 미확정 운영 정책을 안내한다.',
    states: [base],
    rules: [
      '실제 제품도 시행 중인 약관이 아니라 검토용 초안으로 표시한다. 운영 주체·시행일·문의처는 아직 확정되지 않았다.',
      '홈에서 열면 홈으로 돌아가고, 다른 곳에서 열면 서비스 소개의 이용안내 구역으로 돌아간다. 이용약관과 개인정보 처리방침 사이를 전환할 수 있다.',
    ],
    components: ['정책 문서 머리 · 검토용 초안 안내', '7개 정책 조항 · 문서 전환 링크'],
    tokens: ['panel', 'ink', 'coral-wash', 'line'],
    sources: ['frontend/src/features/onboarding/PolicyPage.tsx'],
    productNote:
      '7개 조항의 텍스트를 제품 코드에서 가져온 독립 샘플이다. 현재 경로의 from=home에 따른 돌아가기 차이는 미리보기에서 고정된 이동으로 표현한다. 외부 작성지침 링크는 포함하지 않는다.',
    productLinks: [link('이용약관', '/terms'), link('홈에서 연 이용약관', '/terms?from=home')],
    next: [
      next('개인정보 처리방침', 'privacy'),
      next('서비스 소개', 'welcome'),
      next('내 피치로', 'home'),
    ],
  },
  {
    kind: 'screen',
    id: 'privacy',
    title: '개인정보 처리방침 · 검토용 초안',
    group: infoGroup,
    route: '/privacy',
    status: '제품에 있음',
    summary:
      '서버에 전송하지 않는 카메라 영상과 서버로 전송하는 음성을 구분하고, 데이터 처리·보관 및 삭제를 설명하는 시행 전 초안.',
    states: [base],
    rules: [
      '시행 전 검토용 초안임을 반드시 표시한다. 미확정 외부 처리 사업자·국외 이전·보관기간을 확정된 정책으로 표현하지 않는다.',
      '홈에서 열면 홈으로 돌아가고, 다른 곳에서 열면 서비스 소개의 이용안내 구역으로 돌아간다. 두 정책 문서 사이를 전환할 수 있다.',
    ],
    components: ['정책 문서 머리 · 검토용 초안 안내', '7개 정책 조항 · 문서 전환 링크'],
    tokens: ['panel', 'ink', 'coral-wash', 'line'],
    sources: ['frontend/src/features/onboarding/PolicyPage.tsx'],
    productNote:
      '제품의 공개 정책 7개 조항을 독립적으로 재현한다. 서버·계정과 무관하며 from=home 쿼리에 따른 돌아가기 구분과 외부 작성지침 링크는 재현하지 않는다.',
    productLinks: [
      link('개인정보 처리방침', '/privacy'),
      link('홈에서 연 처리방침', '/privacy?from=home'),
    ],
    next: [next('이용약관', 'terms'), next('서비스 소개', 'welcome'), next('내 피치로', 'home')],
  },

  {
    kind: 'screen',
    id: 'stage-blocked',
    title: '리허설 · 접근 안내',
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
    title: '미지원 환경 안내',
    group: infoGroup,
    route: '/unsupported',
    status: '제품에 있음',
    summary: '이 브라우저 · 기기에서 연습할 수 없는 이유와 쓸 브라우저를 알린다.',
    states: [
      state('browser', '시선 분석 불가', '시선 분석에 필요한 브라우저 기능이 없다.'),
      state(
        'mobile',
        '터치 기기',
        '휴대폰 · 태블릿에서 연습 화면(튜토리얼 진입 · 장치 점검 · 리허설 · 실전)을 열면 이 안내로 온다. 노트북 · 데스크톱에서 연습하라고 알린다.',
      ),
      state('supported', '연습 가능', '이 주소로 바로 들어왔지만 이 브라우저는 연습할 수 있다.'),
    ],
    rules: [
      '사유는 이 화면이 직접 고른다. 시선 분석 기능이 없으면 그게 먼저고, 그다음 터치 전용 기기다(주 입력이 `pointer: coarse` 이고 마우스 · 트랙패드가 없을 때).',
      '터치 전용 기기가 연습 화면(튜토리얼 진입 · 장치 점검 · 리허설 · 실전)을 열면 `DesktopGate` 가 로그인보다 먼저 이 화면으로 보낸다. 홈 · 피치 · 리포트 · 마이페이지는 모바일에서도 열린다.',
      '"링크 복사"는 주소를 복사하고, 복사할 수 없으면 주소를 직접 옮기라고 알린다.',
    ],
    components: ['InfoPage 틀(로고 줄)', 'Card', 'Button'],
    tokens: ['greige', 'panel', 'ink', 'stone'],
    sources: [
      'frontend/src/features/info/UnsupportedPage.tsx',
      'frontend/src/features/info/unsupportedReason.ts',
      'frontend/src/shared/lib/browserCheck.ts',
      'frontend/src/app/DesktopGate.tsx',
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
      state('default', '기본', '테스트 기간이라 연습 기록 사용 동의가 켜진 채로 잠겨 있다.'),
    ],
    rules: [
      '상단바 아래에 놓여 로고 줄 없이 제목만 둔다. 로그아웃은 상단바, 연습 요약은 홈에 있다.',
      '테스트 기간에는 연습 기록 사용 동의를 사용자가 바꿀 수 없다(#1208). 화면은 켜진 채로 잠가 두고, 계정에 저장된 값이 꺼져 있다면 다음 연습 시작 시 동의 API를 먼저 호출해 켠 뒤 Take를 만든다. 동의 변경에 실패하면 연습을 시작하지 않고 실패를 안내한다.',
      '브라우저 기록 지우기 · 계정 삭제는 브라우저 확인 창으로 한 번 더 묻는다.',
    ],
    components: ['InfoPage 틀(logo=false)', 'Card', 'Button', 'SuggestionForm'],
    tokens: ['greige', 'panel', 'ink', 'stone', 'coral-deep'],
    sources: [
      'frontend/src/features/auth/MyPage.tsx',
      'frontend/src/features/auth/SuggestionForm.tsx',
    ],
    productNote:
      '의견 보내기 칸(SuggestionForm)은 자리만 둔다. 연습 기록 사용 동의는 실제 제품처럼 비활성으로 그린다. 지우기 · 삭제는 서버 · 브라우저에 아무것도 하지 않고, 확인 창과 결과 · 오류 문구("이 브라우저의 기록을 지웠어요" · "삭제하는 중…" 등)는 그리지 않는다.',
    productLinks: [link('마이페이지', '/me')],
    next: [next('내 피치로', 'home')],
  },
];

const onboardingGroup = '서비스 시작';

/** 로그인 전에 보는 화면. 웰컴과 로그인은 같은 머리 · 테두리 · 그림자를 쓴다 */
const onboardingScreens: GuideScreen[] = [
  {
    kind: 'screen',
    id: 'welcome',
    title: '서비스 소개 · 시작하기',
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
      'frontend/src/features/onboarding/launch.ts',
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
      'PC의 Google Chrome이 아니면 세션 확인이 끝난 뒤 안내 창을 띄운다. 막지 않고, 닫으면 Google 로그인 버튼으로 초점이 간다. 휴대폰 · 태블릿은 연습 화면이 열리지 않는다고 알린다. 닫은 기록은 남기지 않는다.',
      '문구가 합니다체다(다른 화면은 해요체) — 문구 규칙의 "섞인 곳". Chrome 안내 창은 다른 창처럼 해요체다.',
    ],
    components: ['PitchCoachWordmark', 'Google 로그인 버튼 그림', 'ChromeNoticeDialog'],
    tokens: ['panel', 'ink', 'cream', 'line-strong'],
    sources: [
      'frontend/src/features/auth/LoginPage.tsx',
      'frontend/src/features/auth/ChromeNoticeDialog.tsx',
      'frontend/src/shared/lib/browserCheck.ts',
    ],
    productNote:
      '로그인 버튼은 Google로 가지 않고 개인정보 처리 동의 샘플로 간다. 개발 환경의 "화면 미리보기 환경" 안내와 Chrome 안내 창은 그리지 않는다.',
    productLinks: [link('로그인', '/login')],
    next: [
      next('로그인 → 개인정보 처리 동의(처음 한 번)', 'privacy-consent'),
      next('이미 동의함 → 홈', 'home'),
      next('서비스 소개', 'welcome'),
    ],
  },
  {
    kind: 'screen',
    id: 'privacy-consent',
    title: '개인정보 처리 동의',
    group: onboardingGroup,
    route: '로그인이 필요한 모든 주소',
    status: '제품에 있음',
    summary:
      '로그인한 뒤 동의 전이면 가려던 화면 위에 동의 창(모달)을 띄운다. 동의해야만 닫히고, 같은 주소에서 그 화면을 쓴다.',
    states: [
      state(
        'default',
        '기본',
        '데이터 활용 안내와 "동의하고 시작하기" · "동의하지 않고 로그아웃".',
      ),
      state('signing-out', '로그아웃 중', '로그아웃 버튼을 막고 "로그아웃 중…"으로 바꾼다.'),
      state('sign-out-failed', '로그아웃 실패', 'role="alert"로 다시 시도하라고 알린다.'),
    ],
    rules: [
      '로그인 확인(RequireSession) 다음, 화면보다 앞에 걸린다. 공개 화면(/welcome · /login · /about · /privacy · /terms 등)에는 걸리지 않는다.',
      '주소를 옮기지 않는다. 딥링크 · next 로 들어와도 그 화면 위에 창이 뜬다.',
      '창은 Esc · 닫기 버튼으로 닫히지 않는다. 뒤 화면은 모달이라 누를 수 없다.',
      '카메라 · 마이크를 켜는 연습 화면(장치 점검 · 리허설 · 실전)은 동의 전에는 뒤에 그리지 않는다(holdUntilConsent).',
      '동의 전에는 새미나이 런처를 띄우지 않는다.',
      '동의는 사용자별 · 안내문 버전별로 이 브라우저(localStorage)에 남는다(BE 미구현). 다른 계정 · 다른 브라우저 · 버전을 올린 뒤에는 다시 묻는다.',
      '창을 열면 초점은 창 제목에, 동의하면 뒤 화면의 첫 제목(h1)으로 옮긴다. 로그아웃 중에는 aria-disabled 로 막아 초점이 창 밖으로 빠지지 않게 한다.',
      '개인정보 처리방침 링크는 새 탭으로 연다.',
    ],
    components: ['PrivacyConsentDialog', 'Button(primary · quiet)', 'dl(안내 항목)'],
    tokens: ['panel', 'ink', 'cream', 'line-strong'],
    sources: [
      'frontend/src/features/auth/PrivacyConsentDialog.tsx',
      'frontend/src/features/auth/session.tsx',
      'frontend/src/shared/lib/privacyConsent.ts',
    ],
    productNote:
      '뒤 화면은 홈 샘플로 고정한다(제품은 가려던 화면). "동의하고 시작하기"는 홈 샘플로, "동의하지 않고 로그아웃"은 서비스 소개로 간다(제품은 로그아웃하면 로그인 확인이 보낸다). 개인정보 처리방침 링크는 새 탭 대신 설계서의 정책 화면으로 간다.',
    productLinks: [link('동의 화면(동의 전 로그인 사용자)', '/')],
    next: [
      next('동의하고 시작하기 → 가려던 화면(홈)', 'home'),
      next('동의하지 않고 로그아웃', 'welcome'),
      next('개인정보 처리방침', 'privacy'),
    ],
  },
];

/** 항목 배열 순서. 왼쪽 목록과 전체 보기는 GUIDE_GROUP_ORDER로 그룹을 정렬한다. */
export const entries: GuideEntry[] = [
  ...foundationEntries,
  ...componentEntries,
  ...productScreens,
  ...infoScreens,
  ...onboardingScreens,
];

export const screens: GuideScreen[] = [...productScreens, ...infoScreens, ...onboardingScreens];
