import { duration } from './homeFormat';
import { useState } from 'react';
import { Link } from 'react-router';
import type { HomePitch } from '@/types/home';

const linkButton =
  'whitespace-nowrap border border-line-strong px-3 py-2 text-xs font-bold transition-colors hover:border-coral-deep hover:text-coral-deep';

export function HomePitchCard({ pitch, index }: { pitch: HomePitch; index: number }) {
  const [failedImage, setFailedImage] = useState<string | null>(null);
  const takes = pitch.takes.length;
  const titleStyle = 'inline-flex max-w-full items-center gap-3 break-all text-lg font-bold';
  return (
    <article className="border-2 border-ink bg-white shadow-[4px_4px_0_var(--color-line-strong)]">
      <div className="flex items-center gap-3 px-4 py-3 sm:gap-5 sm:px-5">
        <div
          aria-hidden="true"
          className="hidden h-14 w-20 shrink-0 overflow-hidden border border-ink bg-cream sm:block"
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
            <div className="p-2">
              <span className="font-mono text-[9px] text-ink/80">
                PITCH / {String(index + 1).padStart(2, '0')}
              </span>
              <div className="mt-2 h-1.5 w-10 bg-coral-deep" />
              <div className="mt-1.5 h-1 w-14 bg-line-strong" />
            </div>
          )}
        </div>
        <div className="min-w-0 flex-1">
          <p className="mb-1 text-xs text-ink/80">
            {takes === 0 && <span className="mr-2 font-bold text-coral-deep">연습 전</span>}
            목표 {duration(pitch.pitch_time)}
          </p>
          {pitch.pitch_id ? (
            <Link
              to={`/pitch/${encodeURIComponent(pitch.pitch_id)}/edit`}
              className={`${titleStyle} hover:text-ink`}
            >
              {pitch.pitch_title}
              <span aria-hidden="true" className="text-sm">
                ↗
              </span>
            </Link>
          ) : (
            <h3 className={titleStyle}>{pitch.pitch_title}</h3>
          )}
        </div>
        <div className="flex flex-col items-end gap-2 sm:flex-row sm:items-center sm:gap-3">
          {/* Take 기록 화면은 피치 필터를 화면 안 상태로만 들고 있어 피치를 골라 보내지 못합니다 */}
          <Link
            to="/takes"
            aria-label={`${pitch.pitch_title} Take 기록 ${takes}개 보기`}
            className={linkButton}
          >
            Take 기록 <strong className="font-mono">{takes}</strong> →
          </Link>
          {/* 비교는 Take 가 둘 이상일 때만 뜻이 있습니다 */}
          {pitch.pitch_id && takes >= 2 && (
            <Link
              to={`/pitch/${encodeURIComponent(pitch.pitch_id)}/comparison`}
              className={linkButton}
            >
              Take 비교 →
            </Link>
          )}
        </div>
      </div>
    </article>
  );
}
