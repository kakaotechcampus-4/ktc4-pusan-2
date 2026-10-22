import { Link } from 'react-router';
import { Card } from '@/shared/ui/Panel';
import { Button } from '@/shared/ui/Button';
import { DraftPage } from '../ui';

/**
 * 마이페이지 시안 (P9) — 계정 · 이 브라우저의 기록 · 약관 · 계정 삭제.
 *
 * 로그아웃은 헤더에 있고, 연습 요약(피치 · Take 수 · 누적 시간)은 홈이 보여 줘서 여기 두지 않습니다.
 * 연습 설정도 두지 않습니다 — 시작 전 세팅이 지난 Take 의 대본 표시를 이어받습니다(defaultScriptMode).
 *
 * 시선 · 슬라이드 · 코치 기록은 WS metrics 가 붙기 전까지 브라우저(IndexedDB)에만 있습니다.
 * 그래서 "이 브라우저 기록 지우기"는 서버 기록과 따로 보여 줍니다.
 */

export function MyPageDraft() {
  return (
    <DraftPage
      title="마이페이지"
      eyebrow="데모 사용자"
      questions={[
        '계정 삭제는 BE에 DELETE /users/me 가 있어야 해요. S3 슬라이드 파일까지 지우는지, 바로 지울지 정해야 해요.',
        '계정 삭제를 누른 뒤 확인을 어떻게 받을지 정해야 해요 (확인 창 · 이메일 다시 입력).',
        '시선 · 코치 기록을 서버로 보내게 되면(WS metrics) 이 브라우저의 기록 문구를 다시 맞춰야 해요.',
      ]}
    >
      <Card title="계정">
        <dl className="grid gap-3 text-sm sm:grid-cols-[8rem_1fr]">
          <dt className="text-stone">이름</dt>
          <dd className="font-bold">데모 사용자</dd>
          <dt className="text-stone">이메일</dt>
          <dd className="font-bold">demo@pitchcoach.local</dd>
          <dt className="text-stone">로그인</dt>
          <dd className="font-bold">Google 계정</dd>
        </dl>
      </Card>

      <Card title="이 브라우저의 기록">
        <p className="text-sm leading-relaxed text-stone">
          리허설 중 시선 · 슬라이드 · 코치 기록은 이 브라우저에 저장돼요. 지금은 서버로 보내지
          않아서 이 브라우저에만 있어요. 카메라 영상과 마이크 소리는 저장하지 않아요.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="secondary">이 브라우저 기록 지우기</Button>
          <span className="text-xs text-stone">서버에 저장된 피치와 리포트는 지워지지 않아요.</span>
        </div>
      </Card>

      <Card title="약관 · 정책">
        <ul className="space-y-2 text-sm">
          <li>
            <Link to="/terms?from=home" className="underline underline-offset-4">
              이용약관
            </Link>
          </li>
          <li>
            <Link to="/privacy?from=home" className="underline underline-offset-4">
              개인정보 처리방침
            </Link>
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
            className="text-sm font-bold text-coral-deep underline underline-offset-4"
          >
            계정 삭제하기
          </button>
        </div>
      </Card>
    </DraftPage>
  );
}
