// 제품 `frontend/src/features/pitch/HomePitchCard.tsx`에서 복사해 시작했다. 설계서가 관리한다.
import { useState } from 'react';
import { Link } from 'react-router';
import { ButtonLink } from './Button';
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
}

const rowLink = 'whitespace-nowrap text-xs font-bold hover:text-coral-deep';

/** 홈 발표 목록의 한 줄. 목록 상자의 `<ul>` 안에 놓인다 */
export function HomePitchCard({
  pitch,
  index,
  showPrepare = false,
}: {
  pitch: HomePitch;
  index: number;
  showPrepare?: boolean;
}) {
  const [failedImage, setFailedImage] = useState<string | null>(null);
  const takes = pitch.takes.length;
  const titleStyle = 'inline-flex min-w-0 items-center gap-2 font-bold';
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
        {pitch.pitch_id ? (
          <Link
            to={`/pitch/${encodeURIComponent(pitch.pitch_id)}/edit`}
            className={`${titleStyle} hover:text-coral-deep`}
          >
            <span className="truncate">{pitch.pitch_title}</span>
            <span aria-hidden="true" className="text-xs">
              ↗
            </span>
          </Link>
        ) : (
          <h3 className={titleStyle}>{pitch.pitch_title}</h3>
        )}
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
      {/* 비교는 Take가 둘 이상일 때만 뜻이 있어, 없을 때도 자리를 비워 줄을 맞춘다 */}
      <span className={`w-20 text-right${showPrepare ? ' shrink-0' : ''}`}>
        {pitch.pitch_id && takes >= 2 && (
          <Link to={`/pitch/${encodeURIComponent(pitch.pitch_id)}/comparison`} className={rowLink}>
            Take 비교 →
          </Link>
        )}
      </span>
      {showPrepare && pitch.pitch_id && (
        <ButtonLink
          to={`/pitch/${encodeURIComponent(pitch.pitch_id)}/device-check`}
          aria-label={`${pitch.pitch_title} 발표 준비`}
          size="sm"
          className="shrink-0"
        >
          발표 준비
        </ButtonLink>
      )}
    </li>
  );
}
