import { isNamedLegacyMarker, type MarkerScan } from '@/shared/lib/slideMarkers';
import { MAX_SCRIPT_CHARS } from './draft';

/**
 * 대본 입력 중 안내. 지금 대본대로 매핑하면 어떻게 나뉠지 미리 알려 줍니다.
 * 막지는 않습니다 — 기존 표기도 안내용으로 인식합니다 (`shared/lib/slideMarkers`).
 *
 * 예전 표기 안내는 **나누는 데 문제가 없을 때만** 냅니다. 구분 0 · 첫 구분 앞 · 번호 순서 ·
 * 장수 불일치가 먼저입니다 — 예전 표기 안내가 앞서면 대본이 제대로 나뉘었는데 장수가 다를 때의
 * "장수를 맞춰야" 안내가 가려집니다. 예전 표기는 첫 구분 앞의 줄만 셉니다 (`scanSlideMarkers`) —
 * 구분 뒤 본문의 "1. 첫째 이유는" 같은 번호 목록은 구분이 아닙니다.
 */
export function markerHint(scan: MarkerScan, pageCount: number, tooLong: boolean): string | null {
  if (tooLong) {
    return `대본은 ${MAX_SCRIPT_CHARS.toLocaleString()}자(공백 포함)까지 매핑할 수 있어요. 조금 줄여 주세요.`;
  }
  if (scan.numbers.length === 0) {
    // `## [슬라이드 1]` 처럼 구분을 꾸며 적었으면 무엇을 고칠지 그 줄을 짚습니다 — 개수에는 넣지 않습니다
    if (scan.wrapped.length > 0) {
      return `“${scan.wrapped[0]}”처럼 구분 앞에 다른 글자가 있으면 인식되지 않아요. “[슬라이드 1]”을 줄 맨 앞에 두세요.`;
    }
    // `1.` · `1)` 꼴만 있으면 본문 번호 목록일 수 있어 기본 안내를 냅니다 — 바꾸라고 하면 목록을 구분으로 바꿀 수 있습니다
    const named = scan.legacy.filter(isNamedLegacyMarker);
    return named.length > 0
      ? legacyHint(named)
      : '“[슬라이드 1]”처럼 슬라이드마다 구분해 주세요. 구분이 없으면 대본 전체가 한 슬라이드로 연결돼요.';
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
  // 첫 구분이 이미 1번이면 앞의 예전 표기 줄은 1번 구분이 아니라 제목입니다 — AI 도 LLM 이 제목 줄로 지웁니다.
  // 바꾸라고 하면 번호가 [1, 1, …] 로 겹쳐 오히려 나뉘지 않습니다
  if (scan.legacy.length > 0 && scan.numbers[0] !== 1) return legacyHint(scan.legacy);
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
    return scan.wrapped.length > 0
      ? `“${scan.wrapped[0]}”처럼 구분 앞에 다른 글자가 있어 대본 전체를 한 슬라이드로 연결했어요. “[슬라이드 1]”을 줄 맨 앞에 두세요.`
      : '구분자가 없어 대본 전체를 한 슬라이드로 연결했어요. “[슬라이드 1]”처럼 나눠 적으면 장마다 연결돼요.';
  }
  if (!scan.numbersValid) {
    return '구분 번호가 1부터 앞 번호보다 크게 이어지지 않아 대본 전체를 한 슬라이드로 연결했어요. 원문에서 번호를 고쳐 주세요.';
  }
  return '첫 구분 앞의 글을 발표할 말로 보고 대본 전체를 한 슬라이드로 연결했어요. 제목이 아니라면 “[슬라이드 1]”을 앞으로 옮겨 주세요.';
}

function legacyHint(legacy: string[]): string {
  const rest = legacy.length > 1 ? ` 등 ${legacy.length}줄은` : ' 같은 줄은';
  return `“${legacy[0]}”${rest} 구분으로 인식되지 않아요. “[슬라이드 1]”처럼 바꿔 주세요.`;
}
