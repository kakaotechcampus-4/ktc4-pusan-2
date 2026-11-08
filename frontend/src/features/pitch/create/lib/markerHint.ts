import {
  markerNumber,
  splitLines,
  type MarkerScan,
  type NearMarker,
} from './slideMarkers';
import { MAX_SCRIPT_CHARS } from './draft';

/**
 * 대본 입력 중 안내. 지금 대본대로 매핑하면 어떻게 나뉠지 미리 알려 줍니다.
 * 막지는 않습니다 — 구분처럼 보이지만 인식되지 않는 줄도 안내에만 씁니다 (`features/pitch/create/lib/slideMarkers` `near`).
 *
 * 그런 줄의 안내는 **구분이 하나도 없을 때**, 또는 나누는 데 문제가 없는데 첫 구분이 1번이 아닐 때만 냅니다.
 * 첫 구분 앞 · 번호 순서 · 장수 불일치가 먼저입니다 — 앞서면 대본이 제대로 나뉘었는데 장수가 다를 때의
 * "장수를 맞춰야" 안내가 가려집니다. 그런 줄은 첫 구분 앞의 줄만 셉니다 (`scanSlideMarkers`) —
 * 구분 뒤 본문의 "1. 첫째 이유는" 같은 번호 목록은 구분이 아닙니다.
 */
export function markerHint(scan: MarkerScan, pageCount: number, tooLong: boolean): string | null {
  if (tooLong) {
    return `대본은 ${MAX_SCRIPT_CHARS.toLocaleString()}자(공백 포함)까지 매핑할 수 있어요. 조금 줄여 주세요.`;
  }
  if (scan.numbers.length === 0) {
    // `1.` · `1)` 꼴만 있으면 본문 번호 목록일 수 있어 기본 안내를 냅니다 — 바꾸라고 하면 목록을 구분으로 바꿀 수 있습니다
    return (
      nearHint(
        scan.near.filter((n) => n.kind !== 'numbered'),
        false,
      ) ??
      '“[슬라이드 1]”처럼 슬라이드마다 구분해 주세요. 구분이 없으면 대본 전체가 한 슬라이드로 연결돼요.'
    );
  }
  if (scan.leadingText) {
    return '첫 구분 앞에 발표할 말이 있으면 나뉘지 않아요. 제목이 아니라면 “[슬라이드 1]”을 앞으로 옮겨 주세요.';
  }
  if (!scan.numbersValid) {
    return '구분 번호는 1부터 앞 번호보다 크게 이어져야 해요. 그렇지 않으면 대본 전체가 한 슬라이드로 연결돼요.';
  }
  const last = Math.max(...scan.numbers);
  if (pageCount > 0 && last !== pageCount) {
    return `구분은 ${last}번까지, 슬라이드는 ${pageCount}장이에요. 장수를 맞춰야 연습을 시작할 수 있어요.`;
  }
  // 첫 구분이 이미 1번이면 앞 줄은 1번 구분이 아니라 제목입니다 — AI 도 LLM 이 제목 줄로 지웁니다.
  // 바꾸라고 하면 번호가 [1, 1, …] 로 겹쳐 오히려 나뉘지 않습니다
  if (scan.numbers[0] !== 1) return nearHint(scan.near, false);
  return null;
}

/**
 * 매핑했는데 나뉘지 않았을 때(`segmented: false`)의 안내. AI 가 나누지 못하는 경우는 셋입니다 —
 * 구분자 없음 · 번호 0 · 겹침 · 거꾸로 · 첫 구분 앞에 제목이 아닌 글 (`find_slides` · `core.py` 4단계).
 * 매핑한 원문을 다시 훑어 입력 화면 안내(`markerHint`)와 같은 원인을 말합니다. 구분과 번호가 맞는데도
 * 나뉘지 않았으면 남은 원인은 첫 구분 앞의 글입니다 — 한 줄이어도 LLM 이 제목이 아니라고 보면 나누지 않습니다.
 */
export function unsegmentedNotice(scan: MarkerScan): string {
  if (scan.numbers.length === 0) {
    return (
      nearHint(
        scan.near.filter((n) => n.kind !== 'numbered'),
        true,
      ) ??
      '구분자가 없어 대본 전체를 한 슬라이드로 연결했어요. “[슬라이드 1]”처럼 나눠 적으면 장마다 연결돼요.'
    );
  }
  if (!scan.numbersValid) {
    return '구분 번호가 1부터 앞 번호보다 크게 이어지지 않아 대본 전체를 한 슬라이드로 연결했어요. 원문에서 번호를 고쳐 주세요.';
  }
  return '첫 구분 앞의 글을 발표할 말로 보고 대본 전체를 한 슬라이드로 연결했어요. 제목이 아니라면 “[슬라이드 1]”을 앞으로 옮겨 주세요.';
}

/**
 * 구분처럼 보이지만 인식되지 않는 줄을 짚습니다. 무엇을 고칠지 분명한 것(특수 공백 · 꾸밈 · 다른 괄호)이 먼저입니다.
 * `mapped` 면 매핑한 뒤의 말투(“…해 대본 전체를 한 슬라이드로 연결했어요”)입니다.
 */
function nearHint(near: NearMarker[], mapped: boolean): string | null {
  const joined = mapped ? ' 대본 전체를 한 슬라이드로 연결했어요.' : '';
  const space = near.find((n) => n.kind === 'space');
  if (space) {
    return mapped
      ? `“${space.line}” 앞에 전각 공백 같은 특수 공백이 있어${joined} 줄 맨 앞의 공백을 지워 주세요.`
      : `“${space.line}” 앞에 전각 공백 같은 특수 공백이 있어 구분으로 인식되지 않아요. 줄 맨 앞의 공백을 지워 주세요.`;
  }
  const forms = near.filter((n) => n.kind === 'form');
  const lines = forms.length > 0 ? forms : near;
  if (lines.length === 0) return null;
  const subject = `“${lines[0].line}”${lines.length > 1 ? ` 등 ${lines.length}줄은` : ' 같은 줄은'}`;
  const reason = mapped ? `구분으로 인식되지 않아${joined}` : '구분으로 인식되지 않아요.';
  const fix =
    forms.length > 0
      ? '꾸밈이나 다른 괄호 없이 “[슬라이드 1]”처럼 줄 맨 앞에 적어 주세요.'
      : '“[슬라이드 1]”처럼 바꿔 주세요.';
  return `${subject} ${reason} ${fix}`;
}

/**
 * 매핑 편집에서 장 대본에 쓴, 구분자로 읽히는 줄 (`[1] 통계청 자료` · `슬라이드 3: 요약` · `슬라이드 3`).
 * 다시 만든 원문(`joinScriptBlocks`)을 다시 매핑하면 그 줄에서 장이 새로 나뉘어 1..N 이 되지 않습니다.
 */
export function markerLinesInBlocks(blocks: string[]): { index: number; line: string }[] {
  return blocks.flatMap((block, index) =>
    splitLines(block)
      .filter((line) => markerNumber(line) !== null)
      .map((line) => ({ index, line: line.trim() })),
  );
}

/** 매핑 편집 안내 — 장 대본에 구분자로 읽히는 줄이 있으면 첫 줄을 짚습니다. 막지는 않습니다 */
export function mappingEditNotice(blocks: string[]): string | null {
  const found = markerLinesInBlocks(blocks);
  if (found.length === 0) return null;
  const { index, line } = found[0];
  const more = found.length > 1 ? ` (그 밖에 ${found.length - 1}줄 더)` : '';
  return `${index + 1}장 대본의 “${line}” 줄은 슬라이드 구분으로 읽혀요${more}. 그대로 다시 매핑하면 장이 다르게 나뉘어요. 줄 맨 앞에 다른 글자를 두거나 문장 안으로 옮겨 주세요.`;
}
