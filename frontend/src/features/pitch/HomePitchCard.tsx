import { duration } from './homeFormat';
import { useState } from 'react';
import { Link } from 'react-router';
import { ButtonLink } from '@/shared/ui/Button';
import type { HomePitch } from '@/types/home';
import { PitchMoreMenu } from './PitchMoreMenu';

const rowLink = 'whitespace-nowrap text-xs font-bold hover:text-coral-deep';

/** 홈 발표 목록의 한 줄. 목록 상자(`HomePage`)의 `<ul>` 안에 놓입니다 */
export function HomePitchCard({
  pitch,
  index,
  onDelete,
}: {
  pitch: HomePitch;
  index: number;
  /** 더보기(···) 메뉴의 삭제. 없으면 메뉴를 두지 않습니다 */
  onDelete?: (pitch: HomePitch) => void;
}) {
  const [failedImage, setFailedImage] = useState<string | null>(null);
  const takes = pitch.takes.length;
  const titleStyle = 'inline-flex min-w-0 items-center gap-2 font-bold';
  // 튜토리얼 샘플 피치(#931)는 자료 · 대본을 고치는 화면으로 보내지 않습니다. 자료를 바꾸면 튜토리얼이
  // 아니게 되고, 시간을 바꾸면 BE 가 코칭 계획을 다시 만들려고 AI 를 부릅니다. 연습은 튜토리얼 화면에서
  // 시작합니다 — 리포트를 이미 받았는지 거기서 알려 줍니다
  const demo = pitch.is_demo;
  const editTo = `/pitch/${encodeURIComponent(pitch.pitch_id)}/edit`;
  return (
    <li data-pitch-id={pitch.pitch_id} className="flex items-center gap-4 px-4 py-2 sm:px-5">
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
          <span className="tabular-nums text-[10px] text-ink/80">
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
        {!demo &&
          (pitch.pitch_id ? (
            <Link to={editTo} className={`${titleStyle} hover:text-coral-deep`}>
              <span className="truncate">{pitch.pitch_title}</span>
              <span aria-hidden="true" className="text-xs">
                ↗
              </span>
            </Link>
          ) : (
            <h3 className={titleStyle}>{pitch.pitch_title}</h3>
          ))}
        <span className="text-xs text-ink/80">
          {takes === 0 && <span className="mr-2 font-bold text-coral-deep">연습 전</span>}
          목표 {duration(pitch.pitch_time)}
        </span>
      </div>
      {/* 기록이 없는 피치는 걸러 볼 Take 가 없어 링크 대신 글자만 둡니다 */}
      <span className="w-28 shrink-0">
        {takes > 0 && pitch.pitch_id ? (
          <Link
            to={`/takes?pitch=${encodeURIComponent(pitch.pitch_id)}`}
            aria-label={`${pitch.pitch_title} Take 기록 ${takes}개 보기`}
            className={rowLink}
          >
            Take 기록 <span className="tabular-nums">{takes}</span> →
          </Link>
        ) : (
          <span className="whitespace-nowrap text-xs text-ink/80">
            Take 기록 <span className="tabular-nums">{takes}</span>
          </span>
        )}
      </span>
      {pitch.pitch_id && (
        <ButtonLink
          to={demo ? '/pitch/tutorial' : editTo}
          aria-label={`${pitch.pitch_title} 발표 준비`}
          size="sm"
          className="shrink-0 border-transparent! shadow-none! outline-1 outline-ink"
        >
          발표 준비
        </ButtonLink>
      )}
      {/* 삭제는 되돌릴 수 없어 발표 준비 옆에 버튼으로 두지 않고 메뉴 안에 둡니다 (#807).
          지울 수 있는지는 BE 가 정합니다(can_delete, #1057). 값이 없으면(BE 가 아직 안 내림) 막지 않고 BE 의 409 에 맡깁니다 */}
      {pitch.pitch_id && onDelete && (
        <PitchMoreMenu
          pitchTitle={pitch.pitch_title}
          blockedReason={
            pitch.can_delete === false ? '연습이나 분석이 끝난 뒤 지울 수 있어요.' : null
          }
          onDelete={() => onDelete(pitch)}
        />
      )}
    </li>
  );
}
