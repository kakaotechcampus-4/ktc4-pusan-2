import type { ReactNode } from 'react';
import { COVERAGE_REVISION_CHANGED, useSlideCoverage, useTakeReport } from '@/shared/api/report';
import { ApiFailure } from '@/shared/api/tokenStore';
import { Badge } from '@/shared/ui/Badge';
import type {
  CoverageSentence,
  Fallback,
  OffscriptSpan,
  SlideCoverageDetail,
} from '@/types/report';
import { SpeechMixBlock } from './SpeechMixBlock';
import {
  bandText,
  coverageStatusLabel,
  expressionLabel,
  factStatusLabel,
  importanceLabel,
  mixLabel,
  mixMerged,
  reasonText,
  sentenceStatusLabel,
  slideNotes,
  splitMarks,
} from './contentFormat';

/**
 * 장별 펼침 (#1154 4절). 장을 펼칠 때만 상세를 부릅니다(`useSlideCoverage`).
 * 409 면 요청 훅이 리포트를 다시 읽고, 새 `revision` 으로 이 장을 다시 부릅니다.
 */
export function SlideCoverage({
  takeId,
  slideNumber,
  revision,
}: {
  takeId: string;
  slideNumber: number;
  revision: number;
}) {
  const query = useSlideCoverage(takeId, slideNumber, revision, true);
  if (query.isPending) return <Note>이 장의 결과를 불러오고 있어요.</Note>;
  if (query.isError)
    return <LoadError takeId={takeId} error={query.error} retry={() => query.refetch()} />;
  return <SlideBody detail={query.data} />;
}

function Note({ children }: { children: ReactNode }) {
  return <p className="text-sm text-ink/80">{children}</p>;
}

function Retry({ message, retry }: { message: string; retry: () => unknown }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Note>{message}</Note>
      <button
        type="button"
        onClick={() => {
          retry();
        }}
        className="min-h-11 text-sm font-bold underline underline-offset-4"
      >
        다시 불러오기
      </button>
    </div>
  );
}

function LoadError({
  takeId,
  error,
  retry,
}: {
  takeId: string;
  error: unknown;
  retry: () => unknown;
}) {
  if (error instanceof ApiFailure && error.code === COVERAGE_REVISION_CHANGED)
    return <RevisionChanged takeId={takeId} />;
  if (error instanceof ApiFailure && error.status === 404)
    return <Note>이 장은 자세한 결과가 없어요.</Note>;
  return <Retry message="이 장의 결과를 불러오지 못했어요." retry={retry} />;
}

/**
 * 409 — 요청 훅이 리포트를 무효화했습니다. 리포트가 새 판으로 오면 이 장은 새 키로 다시 불립니다.
 * 리포트를 다시 읽는 것마저 실패하면 기다려도 바뀌지 않으니, 리포트를 다시 부르는 버튼을 둡니다 (#1157 리뷰)
 */
function RevisionChanged({ takeId }: { takeId: string }) {
  const report = useTakeReport(takeId);
  if (report.isError)
    return (
      <Retry
        message="결과가 바뀌었는데 새 결과를 불러오지 못했어요."
        retry={() => report.refetch()}
      />
    );
  return <Note>확인 답이 반영돼 결과가 바뀌었어요. 새 결과를 불러오고 있어요.</Note>;
}

function SlideBody({ detail }: { detail: SlideCoverageDetail }) {
  if (detail.state !== 'SCORED') return <FailedSlide detail={detail} />;
  // 음성 인식이 끊겼거나 문장 단위로 평가한 장은 결과가 잠정입니다 — 이유를 맨 위에 둡니다 (#1157 리뷰)
  const notes = slideNotes(
    detail.detail?.reason_codes ?? [],
    detail.detail?.rubric_quality ?? null,
  );
  return (
    <div className="flex flex-col gap-5">
      {notes.length > 0 && (
        <div className="flex flex-col gap-1">
          <Badge tone="muted">잠정</Badge>
          {notes.map((note) => (
            <Note key={note}>{note}</Note>
          ))}
        </div>
      )}
      <Sentences sentences={detail.sentences} />
      <SpokenSentences detail={detail} />
      {detail.detail && (
        <SpeechMixBlock mix={detail.detail.speech_mix} title="이 장의 말의 구성" scope="slide" />
      )}
      <KeyPoints rows={detail.key_points} />
      <Facts rows={detail.critical_facts} />
    </div>
  );
}

/* ── 대본 문장 ─────────────────────────────────────────────────── */

function Sentences({ sentences }: { sentences: CoverageSentence[] }) {
  if (sentences.length === 0) return null;
  return (
    <section className="flex flex-col gap-2">
      <h4 className="text-sm font-bold">대본 문장</h4>
      <ol className="flex flex-col gap-2">
        {sentences.map((s) => {
          const expression = expressionLabel(s.expression_mode);
          return (
            <li key={s.sentence_index} className="flex flex-col gap-1 border-l-2 border-line pl-3">
              <p className="flex flex-wrap items-center gap-2 text-sm">
                <Badge tone={sentenceTone(s)}>{sentenceStatusLabel(s.role, s.status)}</Badge>
                {expression && <Badge tone="muted">{expression}</Badge>}
                <span className="leading-relaxed">{s.text}</span>
              </p>
              {s.reason && (
                <details className="text-xs text-ink/80">
                  <summary className="min-h-11 w-fit cursor-pointer content-center">
                    이유 보기
                  </summary>
                  <p className="leading-relaxed">{s.reason}</p>
                </details>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function sentenceTone(s: CoverageSentence) {
  if (s.role === 'skip' || s.status === null) return 'muted' as const;
  if (s.status === 'said') return 'strong' as const;
  if (s.status === 'contradicted') return 'bad' as const;
  return 'plain' as const;
}

/* ── 내가 말한 문장 ────────────────────────────────────────────── */

/**
 * 말한 문장 위의 안내 한 줄.
 * - 대본 밖 말을 나누지 못했으면(`state` 가 `ok` 가 아님) 강조 없이 문장만 보이고 그 이유를 붙입니다
 * - 나눴지만 세부 범주를 합쳐 보였으면(`OFFSCRIPT_DISABLED` · `NOT_VALIDATED`, #1152 최종 일치 확인)
 *   말의 구성과 같은 "세부 구분 없이 합쳐 보여요"를 붙입니다
 */
function spokenNote(offscript: SlideCoverageDetail['offscript']): string | null {
  if (!offscript) return null;
  if (offscript.state !== 'ok') return reasonText(offscript.reason_codes[0] ?? null);
  return mixMerged(offscript.reason_codes) ? reasonText('OFFSCRIPT_DISABLED') : null;
}

function SpokenSentences({ detail }: { detail: SlideCoverageDetail }) {
  if (detail.stt_sentences.length === 0) return null;
  const offscript = detail.offscript;
  const spans = offscript?.spans ?? [];
  const why = spokenNote(offscript);
  return (
    <section className="flex flex-col gap-2">
      <h4 className="text-sm font-bold">내가 말한 문장</h4>
      {why && <Note>{why}</Note>}
      <ol className="flex flex-col gap-1.5">
        {detail.stt_sentences.map((sentence, index) => (
          // 같은 문장이 두 번 나올 수 있어 순서를 키로 씁니다 — 목록은 바뀌지 않습니다
          <li key={index} className="text-sm leading-relaxed">
            <MarkedSentence
              sentence={sentence}
              spans={spans.filter((s) => s.stt_sentence_index === index)}
            />
          </li>
        ))}
      </ol>
    </section>
  );
}

/** 대본 밖 구간은 `<mark>` + 범주 이름 글자 — 색만으로 구분하지 않습니다 */
function MarkedSentence({ sentence, spans }: { sentence: string; spans: OffscriptSpan[] }) {
  return (
    <>
      {splitMarks(sentence, spans).map((piece, i) => {
        if (!piece.span) return <span key={i}>{piece.text}</span>;
        return (
          <mark key={i} className="bg-coral-wash px-0.5 text-ink">
            {piece.text}
            <span className="ml-1 text-xs font-bold text-coral-deep">
              [{mixLabel(piece.span.display_category)}]
            </span>
          </mark>
        );
      })}
    </>
  );
}

/* ── 핵심 내용 · 핵심 수치 ─────────────────────────────────────── */

function KeyPoints({ rows }: { rows: SlideCoverageDetail['key_points'] }) {
  if (rows.length === 0) return null;
  return (
    <section className="flex flex-col gap-2">
      <h4 className="text-sm font-bold">핵심 내용</h4>
      <table className="w-full text-left text-sm">
        <thead className="text-xs text-stone">
          <tr>
            <th scope="col" className="py-1 font-bold">
              내용
            </th>
            <th scope="col" className="w-14 py-1 font-bold">
              중요도
            </th>
            <th scope="col" className="w-24 py-1 font-bold">
              판정
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key_point_id} className="border-t border-line align-top">
              <td className="py-1.5 pr-2">{row.content}</td>
              <td className="py-1.5">{importanceLabel(row.importance)}</td>
              <td className="py-1.5 font-bold">{coverageStatusLabel(row.status)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function Facts({ rows }: { rows: SlideCoverageDetail['critical_facts'] }) {
  if (rows.length === 0) return null;
  return (
    <section className="flex flex-col gap-2">
      <h4 className="text-sm font-bold">핵심 수치</h4>
      <table className="w-full text-left text-sm">
        <thead className="text-xs text-stone">
          <tr>
            <th scope="col" className="py-1 font-bold">
              대본 값
            </th>
            <th scope="col" className="py-1 font-bold">
              들린 값
            </th>
            <th scope="col" className="w-20 py-1 font-bold">
              판정
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.fact_id} className="border-t border-line align-top">
              <td className="tabular-nums py-1.5 pr-2">{row.value}</td>
              <td className="tabular-nums py-1.5 pr-2">{row.stt_value ?? '—'}</td>
              <td className="py-1.5 font-bold">{factStatusLabel(row.status)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

/* ── 간이 · 실패 장 ────────────────────────────────────────────── */

function FailedSlide({ detail }: { detail: SlideCoverageDetail }) {
  const failure = detail.failure;
  const fallback = failure?.fallback ?? null;
  return (
    <div className="flex flex-col gap-2">
      <Note>{reasonText(failure?.error?.code ?? null, 'failedSlide')}</Note>
      {fallback && <FallbackValues fallback={fallback} />}
    </div>
  );
}

function FallbackValues({ fallback }: { fallback: Fallback }) {
  const band = bandText(fallback.delivery_band);
  const facts = fallback.facts;
  return (
    <div className="flex flex-col gap-1.5 text-sm">
      <p className="flex flex-wrap items-center gap-2">
        <Badge tone="muted">간이</Badge>
        {band ?? reasonText(fallback.reason_codes[0] ?? null)}
      </p>
      {facts && (
        <p>
          수치 맞음 {facts.matched} · 어림 {facts.approximate} · 다른 값 {facts.mismatched} · 빠뜨림{' '}
          {facts.missing} · 확인 대기 {facts.pending}
        </p>
      )}
      {fallback.method === 'lexical_no_rubric' && (
        <Note>평가 기준 없이 대본 글로만 본 값이에요.</Note>
      )}
    </div>
  );
}
