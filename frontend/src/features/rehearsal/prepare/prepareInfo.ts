import type { PracticeCombo, PrepareResponse, ScriptMode } from '@/types/api';
import type { LatestSlide } from '@/shared/api/prepare';
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
 * 넘겨받은 조합보다 서버의 최신 조합이 새 것일 때의 안내. 해당 없으면 null (멘토 리뷰 #923).
 *
 * 조합은 라우터 state 라 새로고침 · 앞으로 가기에도 남아, 그사이 새로 매핑한 대본 대신 예전 조합이 쓰일 수 있습니다.
 * 예전 조합으로 연습하는 것은 막지 않습니다 — 피치 수정에서 예전 버전을 골라 연습할 수 있고, 실패한 Take 에서
 * 돌아올 때도 같은 조합을 넘깁니다(`deviceCheckLink`). 그래서 알리고 [최신으로]만 둡니다.
 *
 * 최신 조합으로 시작할 수 없거나(`prepareProblem`) 어느 한쪽이라도 조합보다 낮으면(서로 엇갈리면) 알리지 않습니다 —
 * 무엇이 "최신"인지 말할 수 없습니다.
 *
 * 서버 id 가 둘 다 같으면 번호와 관계없이 같은 조합입니다. 조합의 번호는 수정 화면 번호라, 같은 버전의 매핑을 고쳐
 * 다시 올리면 서버 번호만 앞서갑니다(`ScriptVersion.remote`) — 번호만 견주면 같은 조합에도 안내가 뜹니다.
 */
export function newerComboNotice(combo: PracticeCombo, info: PrepareInfo): string | null {
  if (!info.presentation || !info.script || prepareProblem(info) !== null) return null;
  if (
    combo.presentationVersionId === info.presentation.id &&
    combo.scriptVersionId === info.script.id
  )
    return null;
  const slide = info.presentation.version;
  const script = info.script.version;
  if (slide < combo.slideVersion || script < combo.scriptVersion) return null;
  if (slide === combo.slideVersion && script === combo.scriptVersion) return null;
  // 조사는 숫자 뒤에 붙이지 않습니다 — "v3예요"처럼 읽기 어렵지 않게
  return `최신 조합이 있어요: 자료 v${slide} · 대본 v${script}`;
}

/**
 * 준비 정보가 가장 최근 자료 대신 **지난 자료**를 골랐을 때의 안내. 해당 없으면 null (멘토 리뷰 #923).
 *
 * 리포트의 [Take N 연습하기] · 대화의 [연습 준비로]는 피치 수정을 거치지 않고 조합 없이 들어옵니다. 그러면
 * `/prepare` 가 대본과 장 수가 맞는 가장 최근 자료를 고르는데, 장 수가 다른 새 PDF 를 올린 뒤면 그게 지난 자료입니다.
 * 막아 버리지는 않고(일부러 지난 자료로 연습할 수 있게) 화면이 한 번 확인받습니다.
 *
 * 준비 정보로 시작할 수 없으면(`prepareProblem`) 그 이유가 먼저라 알리지 않습니다.
 */
export function olderSlideNotice(info: PrepareInfo, latest: LatestSlide | null): string | null {
  if (!latest || !info.presentation || !info.script || prepareProblem(info) !== null) return null;
  if (latest.id === info.presentation.id || latest.version <= info.presentation.version)
    return null;
  const chosen = info.presentation.version;
  const { slideCount } = info.script;
  if (latest.pageCount !== null && slideCount !== null && latest.pageCount !== slideCount) {
    return `가장 최근 자료(v${latest.version} · ${latest.pageCount}쪽)는 대본(${slideCount}장)과 장 수가 달라 지난 자료(v${chosen})로 연습하게 돼요`;
  }
  return `가장 최근 자료(v${latest.version}) 대신 지난 자료(v${chosen})로 연습하게 돼요`;
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
      // 시간은 고른 조합이 아니라 피치의 지금 설정입니다. BE 가 Take 를 그 값으로 기록해서,
      // 다시 찍기로 옛 조합을 되살려도 타이머가 어긋나지 않게 합니다. 준비 정보가 오기 전에만 조합 값을 씁니다
      timeLimitSec: info?.timeLimitSec ?? combo.goalTimeSec,
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
