import { duration } from './homeFormat';
import { useId, useState } from 'react';
import { Link } from 'react-router';
import type { HomePitch } from '@/types/home';

const modes: Record<string, string> = {
  FULL: '전체 대본',
  HIGHLIGHT: '전체 대본 + 하이라이트',
  KEYWORD: '핵심 키워드',
  OFF: '대본 없이',
};

export function HomePitchCard({ pitch, index }: { pitch: HomePitch; index: number }) {
  const [open, setOpen] = useState(index === 0);
  const [failedImage, setFailedImage] = useState<string | null>(null);
  const recordsId = useId();
  const takes = [...pitch.takes].sort((a, b) => b.take_version - a.take_version);
  const titleStyle = 'inline-flex items-center gap-3 text-lg font-bold sm:text-xl';
  return (
    <article className="border-2 border-ink bg-panel shadow-[4px_4px_0_var(--color-line-strong)]">
      <div className="flex flex-wrap items-center gap-5 p-5 sm:p-7">
        <div
          aria-hidden="true"
          className="relative hidden h-20 w-28 shrink-0 overflow-hidden border border-ink bg-cream sm:block"
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
            <div className="p-3">
              <span className="font-mono text-[9px] text-stone">
                PITCH / {String(index + 1).padStart(2, '0')}
              </span>
              <div className="mt-3 h-1.5 w-12 bg-coral" />
              <div className="mt-2 h-1 w-16 bg-line-strong" />
            </div>
          )}
        </div>
        <div className="min-w-0 flex-1">
          <p className="mb-2 text-xs text-stone">목표 {duration(pitch.pitch_time)}</p>
          {pitch.pitch_id ? (
            <Link
              to={`/pitch/${encodeURIComponent(pitch.pitch_id)}/edit`}
              className={`${titleStyle} hover:text-coral-deep`}
            >
              {pitch.pitch_title}
              <span aria-hidden="true" className="text-sm">
                ↗
              </span>
            </Link>
          ) : (
            <h3 className={titleStyle}>{pitch.pitch_title}</h3>
          )}
          <p className="mt-2 text-xs text-stone">
            {pitch.pitch_id ? '자료와 대본 확인하기' : '현재 자료 열기를 사용할 수 없어요.'}
          </p>
        </div>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={recordsId}
          onClick={() => setOpen(!open)}
          className="flex items-center gap-5 border border-line-strong px-4 py-3 text-sm hover:border-ink hover:bg-cream"
        >
          연습 기록 <span className="font-mono font-bold">{takes.length}</span>
          <span aria-hidden="true">{open ? '−' : '+'}</span>
        </button>
      </div>
      {open && (
        <div id={recordsId} className="border-t border-line-strong">
          {takes.length === 0 ? (
            <p className="p-6 text-sm text-stone">아직 연습 기록이 없어요.</p>
          ) : (
            <>
              <div className="flex justify-between bg-cream/50 px-6 py-3 font-mono text-[10px] tracking-widest text-stone">
                <span>REHEARSAL LOG</span>
                <span>최근 연습순</span>
              </div>
              <ul>
                {takes.map((take) => (
                  <li
                    key={take.take_id}
                    className="flex flex-wrap items-center gap-x-6 gap-y-3 border-t border-line px-6 py-4 text-sm"
                  >
                    <span className="w-20 font-mono font-bold">
                      TAKE {String(take.take_version).padStart(2, '0')}
                    </span>
                    <span
                      className="font-mono text-xs"
                      aria-label={`발표 시간 ${duration(take.take_elapsed)}, 목표 ${duration(take.take_time)}`}
                    >
                      {duration(take.take_elapsed)}{' '}
                      <span className="text-stone">/ {duration(take.take_time)}</span>
                    </span>
                    <span className="text-xs text-stone">
                      {modes[take.script_mode] ?? '기타 대본 모드'}
                    </span>
                    <span
                      className="ml-auto font-mono"
                      aria-label={take.score === null ? '점수 미제공' : `${take.score}점`}
                    >
                      {take.score === null ? '—' : `${take.score}점`}
                    </span>
                    <span
                      className="w-16 text-xs text-stone"
                      aria-label={
                        take.delta === null ? '점수 비교 없음' : `이전 대비 ${take.delta}점`
                      }
                    >
                      {take.delta === null
                        ? '비교 없음'
                        : take.delta === 0
                          ? '변화 없음'
                          : `${take.delta > 0 ? '↑ +' : '↓ '}${take.delta}`}
                    </span>
                    {take.take_id ? (
                      <Link
                        to={`/takes/${encodeURIComponent(take.take_id)}`}
                        className="font-bold hover:text-coral-deep"
                      >
                        리포트 ↗
                      </Link>
                    ) : (
                      <span className="text-xs text-stone">리포트 열기 미지원</span>
                    )}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </article>
  );
}
