import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { getPresentation } from '@/shared/api/presentation';
import { getScript } from '@/shared/api/script';
import { usePdfDocument } from '@/shared/lib/usePdfDocument';
import type { RehearsalTicket } from '@/types/api';
import type { TextRange } from '../lib/scriptMarks';

/**
 * 무대에 올릴 발표자료와 대본. **시작 전에 한 번에 다 받아 둡니다** —
 * 발표 중에 네트워크를 타면 그 순간 화면이 빕니다 (CLAUDE.md 4번).
 *
 * ── 어디서 받나 ─────────────────────────────────────────────────────
 * 슬라이드: `GET /presentations/{id}` 의 PDF URL 하나를 pdf.js 로 열어 장마다 그립니다.
 *   서버는 장마다 이미지를 만들지 않습니다 (`shared/lib/pdf.ts`). 한 번 연 문서는 메모리에 있어
 *   발표 중에 presigned URL(1시간)이 만료돼도 화면이 깨지지 않습니다.
 * 대본: `GET /scripts/{id}` 가 AI 가 나눈 **슬라이드별 본문 · 키워드**를 줍니다.
 *
 * ── 못 받으면 ───────────────────────────────────────────────────────
 * 무대는 엽니다. 슬라이드는 자리표시, 대본은 빈 칸으로 — 발표 연습 자체(시간 · 시선 · 음성)는
 * 자료 없이도 됩니다. 그래서 `ready` 는 "성공"이 아니라 "받기를 끝냈다"입니다.
 */

export interface SlideScript {
  content: string;
  /** AI 가 뽑은 낱말 (대본에 있는 표현 그대로). 핵심 키워드 모드에서 칩으로 보입니다 */
  keywords: string[];
  /** 그 낱말이 content 안에 있는 자리 (end 미포함). 하이라이트 모드에서 강조합니다 */
  highlights: TextRange[];
}

export interface RehearsalMaterials {
  /** 받기를 끝냈나 (성공이든 실패든) */
  ready: boolean;
  /** 열어 둔 PDF. 못 열었으면 null — 자리표시를 그립니다 */
  doc: PDFDocumentProxy | null;
  presentationFailed: boolean;
  /** 장수. PDF 가 기준이고, 없으면 대본의 마지막 슬라이드 번호 */
  pageCount: number;
  /** 슬라이드 번호 → 그 장의 대본. AI 가 나눈 번호 그대로입니다 */
  scriptBySlide: Map<number, SlideScript>;
}

/** 발표 중에 다시 받지 않습니다 — 받아 둔 것으로 끝까지 갑니다 */
const ONCE = { staleTime: Infinity, gcTime: Infinity, retry: 1 } as const;

export function useRehearsalMaterials(ticket: RehearsalTicket | null): RehearsalMaterials {
  const pitchId = ticket?.pitchId ?? '';

  const presentation = useQuery({
    queryKey: ['presentation', pitchId, ticket?.presentationVersionId],
    queryFn: () => getPresentation(pitchId, ticket!.presentationVersionId),
    enabled: ticket !== null,
    ...ONCE,
  });

  const script = useQuery({
    queryKey: ['script', pitchId, ticket?.scriptVersionId],
    queryFn: () => getScript(pitchId, ticket!.scriptVersionId),
    enabled: ticket !== null,
    ...ONCE,
  });

  const opened = usePdfDocument(presentation.data?.file_url);

  const scriptBySlide = useMemo(() => {
    const map = new Map<number, SlideScript>();
    // 나누기가 끝난 대본만 씁니다. PENDING · FAILED 면 대본 칸은 비워 둡니다
    if (script.data?.parse_status !== 'DONE') return map;
    for (const s of script.data.slides) {
      map.set(s.slide_number, {
        content: s.content,
        keywords: s.keywords,
        highlights: s.highlights,
      });
    }
    return map;
  }, [script.data]);

  const presentationSettled = presentation.isError || opened !== null;
  const doc = opened && 'doc' in opened ? opened.doc : null;
  const lastScriptSlide = Math.max(0, ...scriptBySlide.keys());

  return {
    ready: ticket !== null && presentationSettled && !script.isPending,
    doc,
    presentationFailed: presentation.isError || (opened !== null && !('doc' in opened)),
    pageCount: doc?.numPages ?? lastScriptSlide,
    scriptBySlide,
  };
}
