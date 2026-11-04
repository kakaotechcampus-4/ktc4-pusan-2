import { duration } from './homeFormat';
import { useState } from 'react';
import { Link } from 'react-router';
import type { HomePitch } from '@/types/home';

const rowLink = 'whitespace-nowrap text-xs font-bold hover:text-coral-deep';

/** 홈 발표 목록의 한 줄. 목록 상자(`HomePage`)의 `<ul>` 안에 놓입니다 */
export function HomePitchCard({ pitch, index }: { pitch: HomePitch; index: number }) {
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
          목표 {duration(pitch.pitch_time)}
        </span>
      </div>
      {/* 기록이 없는 피치는 걸러 볼 Take 가 없어 링크 대신 글자만 둡니다 */}
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
      {/* 비교는 Take 가 둘 이상일 때만 뜻이 있어, 없을 때도 자리를 비워 줄을 맞춥니다 */}
      <span className="w-20 text-right">
        {pitch.pitch_id && takes >= 2 && (
          <Link to={`/pitch/${encodeURIComponent(pitch.pitch_id)}/comparison`} className={rowLink}>
            Take 비교 →
          </Link>
        )}
      </span>
    </li>
  );
}
