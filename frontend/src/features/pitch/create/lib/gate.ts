import { CHOOSE_LATEST, countChars, resolveChosen, type Chosen, type PitchDraft } from './draft';

/**
 * 좌측 하단 "다음으로 넘어가려면" 상자.
 *
 * ── 왜 순수 함수인가 ────────────────────────────────────────────────
 * 목업 다섯 장에서 이 상자가 매번 다릅니다. 줄이 세 개로 고정인데 **세 번째 줄만
 * 바뀝니다** — 대본이 있고 아직 매핑 전이면 `매핑`, 그 외에는 `평가기준`.
 * 이 규칙을 컴포넌트 안에 두면 다섯 조합을 눈으로 대조해야 하고, 한 장면만
 * 틀려도 "다음" 버튼이 잘못 열립니다. 아래 테스트가 다섯 장을 그대로 재현합니다.
 *
 * ★ **고른 버전을 기준으로 봅니다** (최신이 아닙니다). 대본 V2 는 매핑했는데
 *   연습에 V1 을 들고 가기로 골랐다면, 그 V1 이 안 나뉘어 있으면 시작하면 안 됩니다.
 */

export interface GateRow {
  key: 'slides' | 'script' | 'mapping' | 'criteria';
  label: string;
  done: boolean;
}

export interface Gate {
  /** 상자 제목. 다 됐으면 "준비 완료" */
  heading: string;
  /** 굵은 안내 한 줄 */
  message: string;
  rows: GateRow[];
  /** 연습을 시작할 수 있나 — "다음" 버튼의 활성 여부 */
  ready: boolean;
}

export function computeGate(draft: PitchDraft, chosen: Chosen = CHOOSE_LATEST): Gate {
  const { slides, script, criteria } = resolveChosen(draft, chosen);

  const pageCount = slides?.pageCount ?? null;
  const slidesDone = pageCount !== null && pageCount > 0;

  const charCount = script ? countChars(script.text) : 0;
  const scriptWritten = charCount > 0;
  const blocks = script?.blocks ?? null;
  const mapped = blocks !== null && blocks.length > 0;
  // 나눈 뒤에 슬라이드 장수가 바뀌었다 — 뒤쪽 슬라이드에 붙을 대본이 없습니다.
  // 버전을 따로 고르므로(슬라이드 V2 + 대본 V1) 쉽게 생깁니다.
  // 변환 전이면 장수를 모르니 비교하지 않습니다 (그때는 슬라이드 줄이 막습니다)
  const mappingStale = mapped && slidesDone && blocks.length !== pageCount;
  const mappingDone = mapped && !mappingStale;

  // ★ 평가기준은 **선택**입니다 (시안 04 — "평가기준 (선택)"). 없어도 시작할 수 있습니다.
  //   항목은 서버가 나눠 준 것만 들어오므로 빈 칸이 생기지 않습니다 (직접 입력은 없습니다)
  const criteriaCount = criteria?.items.length ?? 0;

  const slidesRow: GateRow = {
    key: 'slides',
    label: slidesDone ? `슬라이드 ${pageCount}장` : '슬라이드 미등록',
    done: slidesDone,
  };

  const scriptRow: GateRow = {
    key: 'script',
    // 매핑까지 끝나면 줄 하나가 그 사실을 말합니다 — 목업 07 · 08.
    // 장수가 어긋났으면 끝난 것이 아니므로 "매핑 완료"라고 하지 않습니다
    label: scriptLabel(mappingDone, scriptWritten, blocks?.length ?? 0, charCount),
    done: scriptWritten,
  };

  // ★ 세 번째 줄만 갈립니다. 대본은 썼는데 아직 안 나눴으면(또는 장수가 어긋났으면)
  //   그게 다음 할 일입니다.
  const thirdRow: GateRow =
    scriptWritten && !mappingDone
      ? {
          key: 'mapping',
          label: mappingStale
            ? `장수 다름 (대본 ${blocks.length} / 슬라이드 ${pageCount}장)`
            : '매핑 미실행',
          done: false,
        }
      : {
          key: 'criteria',
          label: criteriaCount === 0 ? '평가기준 (선택)' : `평가기준 ${criteriaCount}개`,
          done: criteriaCount > 0,
        };

  const ready = slidesDone && mappingDone;

  return {
    heading: ready ? '준비 완료' : '다음으로 넘어가려면',
    message: nextAction({ slidesDone, scriptWritten, mappingDone, mappingStale }),
    rows: [slidesRow, scriptRow, thirdRow],
    ready,
  };
}

/** 한 번에 하나만 시킵니다. 두 개를 같이 적으면 무엇부터 할지가 안 보입니다 */
function nextAction({
  slidesDone,
  scriptWritten,
  mappingDone,
  mappingStale,
}: {
  slidesDone: boolean;
  scriptWritten: boolean;
  mappingDone: boolean;
  mappingStale: boolean;
}): string {
  if (!slidesDone && !scriptWritten) return '슬라이드와 대본을 준비해 주세요';
  if (!slidesDone) return '슬라이드를 올려주세요';
  if (!scriptWritten) return '대본을 입력해주세요';
  // 서버(AI)는 구분자대로만 나눕니다 — 다시 눌러도 같으니, 고칠 곳은 대본의 구분입니다
  if (mappingStale) return '대본과 슬라이드 장수가 달라요. 대본의 구분을 확인해 주세요';
  if (!mappingDone) return '매핑을 실행해주세요';
  return '바로 연습을 시작할 수 있어요';
}

function scriptLabel(
  mappingDone: boolean,
  scriptWritten: boolean,
  blockCount: number,
  charCount: number,
): string {
  if (mappingDone) return `대본 ${blockCount}블록 매핑 완료`;
  if (scriptWritten) return `대본 ${charCount.toLocaleString()}자`;
  return '대본 미등록';
}
