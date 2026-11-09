import type { ReactNode } from 'react';
import { COVERAGE_REVISION_CHANGED, useSlideCoverage } from '@/shared/api/report';
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
  reasonText,
  sentenceStatusLabel,
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
  if (query.isError) return <LoadError error={query.error} retry={() => query.refetch()} />;
  return <SlideBody detail={query.data} />;
}

function Note({ children }: { children: ReactNode }) {
  return <p className="text-sm text-ink/80">{children}</p>;
}

function LoadError({ error, retry }: { error: unknown; retry: () => unknown }) {
  if (error instanceof ApiFailure && error.code === COVERAGE_REVISION_CHANGED)
    return <Note>확인 답이 반영돼 결과가 바뀌었어요. 새 결과를 불러오고 있어요.</Note>;
  if (error instanceof ApiFailure && error.status === 404)
    return <Note>이 장은 자세한 결과가 없어요.</Note>;
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Note>이 장의 결과를 불러오지 못했어요.</Note>
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

function SlideBody({ detail }: { detail: SlideCoverageDetail }) {
  if (detail.state !== 'SCORED') return <FailedSlide detail={detail} />;
  return (
    <div className="flex flex-col gap-5">
      {detail.detail?.rubric_quality === 'sentence_fallback' && (
        <Note>{reasonText('SENTENCE_FALLBACK')}</Note>
      )}
      <Sentences sentences={detail.sentences} />
      <SpokenSentences detail={detail} />
      {detail.detail && <SpeechMixBlock mix={detail.detail.speech_mix} title="이 장의 말의 구성" />}
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

function SpokenSentences({ detail }: { detail: SlideCoverageDetail }) {
  if (detail.stt_sentences.length === 0) return null;
  const offscript = detail.offscript;
  const spans = offscript?.spans ?? [];
  // 대본 밖 말을 나누지 못했으면 강조 없이 문장만 보이고 이유를 붙입니다
  const why =
    offscript && offscript.state !== 'ok' ? reasonText(offscript.reason_codes[0] ?? null) : null;
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
      <Note>{reasonText(failure?.error?.code ?? null, true)}</Note>
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
