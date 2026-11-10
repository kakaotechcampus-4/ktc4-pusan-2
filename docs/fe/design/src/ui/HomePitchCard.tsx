// 제품 `frontend/src/features/pitch/HomePitchCard.tsx`에서 복사해 시작했다. 설계서가 관리한다.
import { useState } from 'react';
import { Link } from 'react-router';
import { ButtonLink } from './Button';
import { PitchMoreMenu } from './PitchMoreMenu';
import { formatSeconds } from '@/lib/clock';

/** 홈 한 줄에 필요한 값만. 제품 `types/home.ts`의 HomePitch에서 이 줄이 읽는 칸만 남겼다 */
export interface HomePitch {
  pitch_id: string;
  pitch_title: string;
  /** 목표 발표 시간(초) */
  pitch_time: number;
  thumbnail_url: string | null;
  /** 이 줄은 개수만 본다 */
  takes: readonly unknown[];
  /** BE가 정한 삭제 가능 여부(#1057). false면 연습이나 분석이 도는 중이라 막는다. 없으면 막지 않는다 */
  can_delete?: boolean;
  /** #1105 튜토리얼 샘플 여부. 제목 대신 이 필드로 판단한다. */
  is_demo?: boolean;
}

const rowLink = 'whitespace-nowrap text-xs font-bold hover:text-coral-deep';

/** 홈 발표 목록의 한 줄. 목록 상자의 `<ul>` 안에 놓인다 */
export function HomePitchCard({
  pitch,
  index,
  showPrepare = false,
  menuOpen = false,
  onDelete,
}: {
  pitch: HomePitch;
  index: number;
  showPrepare?: boolean;
  /** 설계서 상태 미리보기용. 더보기 메뉴를 열린 채로 그린다 */
  menuOpen?: boolean;
  /** 더보기(···) 메뉴의 삭제. 없으면 메뉴를 두지 않는다 */
  onDelete?: () => void;
}) {
  const [failedImage, setFailedImage] = useState<string | null>(null);
  const takes = pitch.takes.length;
  const titleStyle = 'inline-flex min-w-0 items-center gap-2 font-bold';
  const demo = pitch.is_demo === true;
  const editTo = `/pitch/${encodeURIComponent(pitch.pitch_id)}/edit`;
  return (
    <li className="flex items-center gap-4 px-4 py-2 sm:px-5">
      <div
        aria-hidden="true"
        className="hidden h-8 w-12 shrink-0 items-center justify-center overflow-hidden border border-line-strong bg-cream sm:flex"
      >
        {pitch.thumbnail_url && failedImage !== pitch.thumbnail_url ? (
          <img
            src={pitch.thumbnail_url}
            alt=""
            loading="lazy"
            className="h-full w-full object-cover"
            onError={() => setFailedImage(pitch.thumbnail_url)}
          />
        ) : (
          <span className="font-mono text-[10px] text-ink/80">
            {String(index + 1).padStart(2, '0')}
          </span>
        )}
      </div>
      <div className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-3 gap-y-1">
        {demo && (
          <span className={titleStyle}>
            <span className="truncate">{pitch.pitch_title}</span>
            <span className="shrink-0 border border-ink bg-cream px-1.5 text-xs">샘플</span>
          </span>
        )}
        {!demo && pitch.pitch_id && (
          <Link to={editTo} className={`${titleStyle} hover:text-coral-deep`}>
            <span className="truncate">{pitch.pitch_title}</span>
            <span aria-hidden="true" className="text-xs">
              ↗
            </span>
          </Link>
        )}
        {!demo && !pitch.pitch_id && <h3 className={titleStyle}>{pitch.pitch_title}</h3>}
        <span className="text-xs text-ink/80">
          {takes === 0 && <span className="mr-2 font-bold text-coral-deep">연습 전</span>}
          목표 {formatSeconds(pitch.pitch_time)}
        </span>
      </div>
      {/* 기록이 없는 피치는 걸러 볼 Take가 없어 링크 대신 글자만 둔다 */}
      <span className={showPrepare ? 'w-28 shrink-0' : 'contents'}>
        {takes > 0 && pitch.pitch_id ? (
          <Link
            to={`/takes?pitch=${encodeURIComponent(pitch.pitch_id)}`}
            aria-label={`${pitch.pitch_title} Take 기록 ${takes}개 보기`}
            className={rowLink}
          >
            Take 기록 <span className="font-mono">{takes}</span> →
          </Link>
        ) : (
          <span className="whitespace-nowrap text-xs text-ink/80">
            Take 기록 <span className="font-mono">{takes}</span>
          </span>
        )}
      </span>
      {showPrepare && pitch.pitch_id && (
        <ButtonLink
          to={demo ? '/pitch/tutorial' : editTo}
          aria-label={`${pitch.pitch_title} 발표 준비`}
          size="sm"
          className="shrink-0 border-transparent! shadow-none! outline-1 outline-ink"
        >
          발표 준비
        </ButtonLink>
      )}
      {/* 삭제는 되돌릴 수 없어 발표 준비 옆에 버튼으로 두지 않고 메뉴 안에 둔다(#807) */}
      {pitch.pitch_id && onDelete && (
        <PitchMoreMenu
          pitchTitle={pitch.pitch_title}
          blockedReason={
            pitch.can_delete === false ? '연습이나 분석이 끝난 뒤 지울 수 있어요.' : null
          }
          onDelete={onDelete}
          defaultOpen={menuOpen}
        />
      )}
    </li>
  );
}
