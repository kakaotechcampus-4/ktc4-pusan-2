import type { PracticeCombo, PrepareResponse, ScriptMode } from '@/types/api';
import { normalizeScriptMode } from './prepareStore';

/**
 * 장치 점검 준비 정보 — BE 응답(`GET /prepare`, BE #296 · #298) ↔ 화면. **옮기는 규칙은 여기에만 둡니다.**
 *
 * 장치 점검은 "이번에 어느 버전으로 연습하나"를 두 곳에서 압니다.
 *   피치 생성에서 왔으면  매핑을 저장하며 고른 조합(`PracticeCombo`) — 이게 먼저입니다
 *   홈 · 새로고침이면     이 준비 정보 — 서버가 고른 가장 최근의 맞는 짝
 */

export interface PrepareInfo {
  title: string;
  timeLimitSec: number;
  presentation: { id: string; version: number; pageCount: number | null } | null;
  script: { id: string; version: number; slideCount: number | null } | null;
  /** 연습할 대본보다 새 대본이 아직 쓸 수 없는 상태(나누는 중 · 실패)면 그 번호와 상태 */
  newerScript: { version: number; status: 'PENDING' | 'FAILED' } | null;
  nextTakeNumber: number;
  defaultScriptMode: ScriptMode;
  /** 이번 Take 미션. 첫 Take 면 null, 리뷰에서 정한 미션이 없으면 `items: []` */
  missions: {
    /** 미션을 정한 리뷰의 Take 번호. 바로 앞 Take 가 아닐 수 있습니다 */
    sourceTakeNumber: number;
    /** 우선순위 순 */
    items: { id: string; description: string }[];
  } | null;
}

export function fromPrepare(res: PrepareResponse): PrepareInfo {
  return {
    title: res.title,
    timeLimitSec: res.time_limit_sec,
    presentation: res.presentation && {
      id: res.presentation.id,
      version: res.presentation.version,
      // #296 만 머지됐으면 이 칸이 없습니다 — 모르는 것으로 둡니다
      pageCount: res.presentation.page_count ?? null,
    },
    script: res.script && {
      id: res.script.id,
      version: res.script.version,
      slideCount: res.script.slide_count ?? null,
    },
    newerScript: res.newer_script && {
      version: res.newer_script.version,
      status: res.newer_script.parse_status,
    },
    nextTakeNumber: res.next_take_number,
    defaultScriptMode: normalizeScriptMode(res.default_script_mode),
    // area · target · reason 은 화면에 쓰지 않습니다 — 설명 한 줄이 미션의 전부이고, reason 은
    // 리뷰한 Take 기준 문장("이번 Take 7번")이라 다음 Take 화면에서 헷갈립니다.
    // #409 전 BE 는 이 칸이 없습니다 — 첫 Take 처럼 미션 없이 둡니다
    missions: res.missions
      ? {
          sourceTakeNumber: res.missions.source_take_number,
          items: [...res.missions.items]
            .sort((a, b) => a.priority - b.priority)
            .map((m) => ({ id: m.mission_id, description: m.description })),
        }
      : null,
  };
}

/**
 * 준비 정보로는 시작할 수 없는 이유. 없으면 null 입니다.
 *
 * 쪽수와 장 수는 **둘 다 알 때만** 비교합니다. 쪽수를 아직 세지 않은 옛 발표자료는 BE 도
 * 검사하지 않습니다 (#298) — 모르는 것을 다르다고 막으면 연습을 못 합니다.
 */
export function prepareProblem(info: PrepareInfo): string | null {
  if (!info.presentation) return '발표자료가 없어요. 피치에서 슬라이드를 먼저 올려 주세요.';
  if (!info.script) {
    return info.newerScript?.status === 'PENDING'
      ? '대본을 나누는 중이에요. 잠시 뒤 다시 들어와 주세요.'
      : '연습할 대본이 없어요. 피치에서 대본 매핑을 먼저 해 주세요.';
  }
  const { pageCount } = info.presentation;
  const { slideCount } = info.script;
  if (pageCount !== null && slideCount !== null && pageCount !== slideCount) {
    return `발표자료(${pageCount}쪽)와 대본(${slideCount}장)의 장 수가 달라요. 피치에서 대본 매핑을 다시 확인해 주세요.`;
  }
  return null;
}

/** 새 대본을 아직 못 써서 옛 대본으로 연습한다는 안내. 해당 없으면 null */
export function newerScriptNotice(info: PrepareInfo): string | null {
  if (!info.newerScript || !info.script) return null;
  const { version, status } = info.newerScript;
  const why = status === 'PENDING' ? '아직 나누는 중이라' : '나누지 못해서';
  // 조사는 숫자가 아니라 '대본'에 붙입니다 — "v2으로"처럼 읽히지 않게
  return `새 대본(v${version})은 ${why} 지난 대본(v${info.script.version})으로 연습해요`;
}

/**
 * 미션이 바로 앞 Take 가 아니라 그 전 Take 의 리뷰에서 왔을 때의 안내. 해당 없으면 null 입니다.
 *
 * "분석 중"이라고 말하지 않습니다 — 뒤 Take 의 리뷰가 실패했을 수도 있는데 `/prepare` 로는
 * 둘을 가를 수 없습니다. 리뷰를 마친 Take 만 말합니다.
 */
export function missionSourceNote(info: PrepareInfo): string | null {
  if (!info.missions) return null;
  const n = info.missions.sourceTakeNumber;
  return n < info.nextTakeNumber - 1 ? `Take ${n} 리뷰에서 정한 미션이에요` : null;
}

/** Take 에 박을 버전과 리허설 제한 시간. 화면 위에 보일 번호도 함께 둡니다 */
export interface TakeChoice {
  presentationVersionId: string;
  scriptVersionId: string;
  timeLimitSec: number;
  label: string;
}

/**
 * 이번 연습의 조합. 피치 생성에서 고른 조합이 먼저이고, 없으면 준비 정보입니다.
 * 준비 정보로 시작할 수 없으면(`prepareProblem`) null — Take 를 만들 수 없습니다.
 */
export function takeChoice(
  combo: PracticeCombo | null,
  info: PrepareInfo | null,
): TakeChoice | null {
  if (combo) {
    return {
      presentationVersionId: combo.presentationVersionId,
      scriptVersionId: combo.scriptVersionId,
      timeLimitSec: combo.goalTimeSec,
      label: `자료 v${combo.slideVersion} · 대본 v${combo.scriptVersion}`,
    };
  }
  if (!info?.presentation || !info.script || prepareProblem(info) !== null) return null;
  return {
    presentationVersionId: info.presentation.id,
    scriptVersionId: info.script.id,
    timeLimitSec: info.timeLimitSec,
    label: `자료 v${info.presentation.version} · 대본 v${info.script.version}`,
  };
}
