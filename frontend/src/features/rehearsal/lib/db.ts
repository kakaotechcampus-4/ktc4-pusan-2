import { openDB, type DBSchema, type IDBPDatabase } from 'idb';
import type { DeviceChoice } from '../media/useCameraStream';
import type { CalibrationSummary, GazeExcludedReason, Ms, RehearsalTicket } from '@/types/api';
import type { ZoneDecision, ZoneReference } from '@/workers/gaze.contract';

/**
 * 리허설 중 쌓이는 모든 기록. **브라우저가 원본입니다** (CLAUDE.md 4번).
 *
 * 스키마를 이 파일 한 곳에 모읍니다. 흩어지면 복구 로직이 무너집니다 —
 * 비정상 종료를 감지하려면 session·gazeSegments 를 같은 관점에서
 * 봐야 하는데, 접근 경로가 갈리면 "기록이 남아 있는데 진행 중이었다"를 판단할 수 없습니다.
 *
 * 키는 `clientSessionId` 입니다. 업로드 재시도의 멱등 키와 **같은 값**입니다
 * (`CompleteRequest.clientSessionId`) — 그래서 재시도해도 서버가 Take 를 새로 만들지 않습니다.
 */

const DB_NAME = 'pitchcoach';
const DB_VERSION = 3;

/** 세션 상태. 서버의 TakeStatus 와 다릅니다 — 이건 브라우저 쪽 진행 상태입니다. */
export type SessionStatus = 'RUNNING' | 'ENDED' | 'ABORTED';

export interface SessionRow {
  clientSessionId: string;
  /** 준비 화면에서 발급받은 takeId. 아직 없으면 null (dev 페이지 등) */
  takeId: string | null;
  status: SessionStatus;
  /** 서버에 보낼 ISO 시각 */
  startedAtIso: string;
  /**
   * ★ 하트비트. performance.now() 가 아니라 Date.now() 입니다 —
   * 탭이 죽고 다시 열리면 performance.now() 의 기준점이 리셋되어
   * 이전 세션과 비교할 수 없습니다. 벽시계여야 합니다.
   */
  lastBeatAt: number;
  elapsedMs: Ms;

  /** 시선 측정이 제외됐나. 제외 사유는 화면 문구와 리포트에 그대로 쓰입니다 */
  gazeExcluded: boolean;
  gazeExcludedReason: GazeExcludedReason | null;
  /** 워커가 ready 로 보낸 값. 종료 시점에 영구 고정됩니다 — 나중에 재계산할 수 없습니다 */
  engineVersion: string | null;

  /** clientPerf 용 누적. 1초마다 갱신됩니다 */
  gazeAvgFps: number | null;
  gazeDroppedFrames: number;

  /**
   * 리허설이 무대를 열 때 쓰는 값. 새로고침하면 라우터 state 가 사라지므로 여기서 다시 꺼냅니다.
   * 이 필드가 생기기 전에 만든 세션에는 없습니다 — 그래서 선택 필드입니다 (스키마 변경 없음).
   */
  ticket?: RehearsalTicket;

  /**
   * 장치 점검에서 정한 것들. 리허설은 이 값을 메모리 스토어(`prepareStore`)에서 읽는데,
   * 새로고침하면 그 스토어가 비어서 여기서 되살립니다. 없으면 시선 기준을 못 찾아
   * Take 전체의 시선이 빠지고, 점검하지 않은 기본 장치가 열립니다.
   *
   * 값 필드라 DB 버전을 올리지 않습니다 (인덱스가 없습니다). 이 필드가 생기기 전에 만든 행에는
   * 키가 없으므로 읽을 때 `?? null` 로 받습니다.
   */
  prepare: PrepareContext | null;
  /**
   * 끝내기를 누른 순간 고정한 값. **있으면 종료 중이던 Take 입니다** — 그 뒤에 새로고침하면
   * 무대를 다시 열지 않고 이 값으로 종료를 이어갑니다. 새로 재면 정리하는 동안과
   * 새로고침한 시간이 발표 길이에 얹힙니다.
   */
  ending: { durationMs: Ms; endedAtIso: string } | null;
  /**
   * `/complete` 가 성공한 시각. 있으면 이 Take 는 끝났습니다 — 서버에서 Take 상태를 읽을
   * API 가 없어서 다시 들어왔을 때(새로고침 · 뒤로 가기) 무대를 열지 않을 근거가 이것뿐입니다.
   */
  submittedAt: number | null;
}

export interface PrepareContext {
  calibration: CalibrationSummary | null;
  devices: DeviceChoice;
}

interface PitchDb extends DBSchema {
  session: {
    key: string;
    value: SessionRow;
    indexes: { byStatus: SessionStatus };
  };
  /**
   * 1초 판정 하나가 한 행입니다. 10분이면 최대 600행.
   * 전송 직전에 compressToSegments 로 같은 zone 끼리 묶습니다 —
   * 여기서 미리 묶지 않는 이유는, 중간에 죽었을 때 부분 기록이 그대로 살아야 하기 때문입니다.
   */
  gazeSegments: {
    key: [string, number];
    value: ZoneDecision & { clientSessionId: string };
  };
  slideChanges: {
    key: [string, number];
    value: { clientSessionId: string; atMs: Ms; slideNumber: number };
  };
  /** 대본 의존도의 보조 근거 — 화면에 보인 글자 범위 */
  scriptScroll: {
    key: [string, number];
    value: { clientSessionId: string; atMs: Ms; firstCharIndex: number; lastCharIndex: number };
  };
  /** 발동한 것과 **억제한 것 둘 다.** 억제 기록이 코칭 임계값 조정의 유일한 근거입니다 */
  coachLog: {
    key: [string, number];
    value: {
      clientSessionId: string;
      atMs: Ms;
      type: string;
      fired: boolean;
      message: string | null;
      suppressedReason: string | null;
    };
  };
  /**
   * 캘리브레이션 기준. **서버로 보내지 않습니다** (CLAUDE.md 1번) —
   * 서버에는 품질 요약(`CalibrationSummary`)만 갑니다.
   *
   * 키가 `layoutSignature` 인 이유 — 해상도·배율·카메라 위치가 바뀌면
   * 시선 각도가 달라져 지난 기준을 쓸 수 없습니다. 키로 두면
   * "다른 기기에서는 자동으로 재사용되지 않는" 동작이 그냥 나옵니다.
   */
  zoneRefs: {
    key: string;
    /**
     * `layoutSignature` 는 FE 가 만든 키입니다 — 분류기는 모릅니다 (layoutSignature.ts).
     * `engineVersion` 은 이 기준을 만든 엔진입니다. 다른 엔진에는 넣지 않습니다.
     */
    value: ZoneReference & { layoutSignature: string; engineVersion: string; fittedAt: number };
  };
}

let dbPromise: Promise<IDBPDatabase<PitchDb>> | null = null;

export function openPitchDb(): Promise<IDBPDatabase<PitchDb>> {
  dbPromise ??= openDB<PitchDb>(DB_NAME, DB_VERSION, {
    /**
     * ★ 버전별 장부입니다. 위에서부터 순서대로, 각 블록은 브라우저마다 **평생 한 번만** 돕니다.
     *
     * `oldVersion` 은 올라오기 **직전** 버전입니다 (처음 여는 브라우저면 0). 그래서
     * `oldVersion < N` 은 "N 단계를 아직 안 거친 브라우저만" 이 됩니다. 어디서 출발하든
     * 끝나면 같은 모양이 됩니다.
     *
     * ── "있으면 건너뛴다" 로 하면 안 되는 이유 ──────────────────────
     * `objectStoreNames.contains` 는 **있다/없다**만 답합니다. 그래서 답할 수 있는 건
     * 스토어를 통째로 새로 만드는 경우 하나뿐입니다. 이미 있는 스토어에 인덱스를 붙이거나
     * 저장된 행을 옮기는 일은 전부 "스토어는 있는데 안을 고쳐야 하는" 변경이라,
     * 있다/없다로는 판단할 수 없습니다.
     *
     * 실제로 v3 에서 session 에 인덱스를 붙이면, 처음 여는 사람만 인덱스를 갖고
     * v2 를 쓰던 사람은 못 갖습니다. 에러도 안 나고 사람마다 다른 DB 가 생깁니다.
     * 개발 중에는 DB 를 자주 지워서 본인 화면에서는 재현도 안 됩니다.
     *
     * 스키마가 묻는 질문은 "있나" 가 아니라 **"어디까지 해놨나"** 이고,
     * `oldVersion` 이 IndexedDB 가 그 답으로 준 값입니다.
     */
    upgrade(db, oldVersion) {
      // 스키마 타입에서 빠진 스토어(audioChunks)를 만들고 지우려면 타입 없는 핸들이 필요합니다.
      // 지나간 단계는 그대로 재생해야 하므로 v1 의 생성 줄을 지우지 않고 이걸로 돌립니다.
      const untyped = db as unknown as IDBPDatabase;

      // ── v1 · 세션과 기록들 ──────────────────────────────────────
      if (oldVersion < 1) {
        const session = db.createObjectStore('session', { keyPath: 'clientSessionId' });
        session.createIndex('byStatus', 'status');

        // 복합 키 [clientSessionId, 시각] — 세션별 범위 조회가 그냥 됩니다.
        db.createObjectStore('gazeSegments', { keyPath: ['clientSessionId', 'tMs'] });
        db.createObjectStore('slideChanges', { keyPath: ['clientSessionId', 'atMs'] });
        db.createObjectStore('scriptScroll', { keyPath: ['clientSessionId', 'atMs'] });
        db.createObjectStore('coachLog', { keyPath: ['clientSessionId', 'atMs'] });
        untyped.createObjectStore('audioChunks', { keyPath: ['clientSessionId', 'seq'] });
      }

      // ── v2 · 캘리브레이션 기준. 세션이 아니라 기기(layoutSignature)에 매입니다 ──
      if (oldVersion < 2) {
        db.createObjectStore('zoneRefs', { keyPath: 'layoutSignature' });
      }

      // ── v3 · 로컬 녹음 폐기. 음성은 WebSocket 으로만 갑니다 (CLAUDE.md 4번) ──
      //   이미 쌓인 조각이 용량만 차지하므로 스토어째 지웁니다.
      if (oldVersion < 3) {
        untyped.deleteObjectStore('audioChunks');
      }

      // ── v4 를 만들 때는 여기에 블록을 덧붙입니다. 위는 절대 고치지 않습니다 ──
      //   이미 그 단계를 지나온 브라우저는 다시 밟지 않으므로, 위를 고치면
      //   새로 여는 사람에게만 반영되고 기존 사용자와 구조가 갈립니다.
      //
      //   이미 있는 스토어에 인덱스를 붙일 때는 db 가 아니라 upgrade 가 넘겨주는
      //   versionchange 트랜잭션에서 꺼냅니다 — db.createObjectStore 는 이미 있는
      //   이름에 ConstraintError 를 냅니다.
      //
      //   upgrade(db, oldVersion, _newVersion, tx) {
      //     if (oldVersion < 4) tx.objectStore('session').createIndex('byTakeId', 'takeId');
      //   }
    },
  })
    // 실패한 약속을 캐시하면 새로고침 전까지 영원히 실패합니다.
    // 한 번 열기에 실패하면 다음 호출이 다시 시도하게 비워 둡니다.
    .catch((e: unknown) => {
      dbPromise = null;
      throw e;
    });

  return dbPromise;
}

/**
 * 복합 키 [id, n] 범위. 배열 키는 원소별로 비교되므로
 * [id] 가 [id, 무엇이든] 보다 항상 작습니다.
 */
const sessionRange = (clientSessionId: string) =>
  IDBKeyRange.bound([clientSessionId], [clientSessionId, Number.MAX_SAFE_INTEGER]);

/**
 * 세션을 엽니다. `clientSessionId` 를 여기서 만듭니다.
 *
 * 준비 화면(P4)의 시작 CTA 가 이 함수를 부르고, 같은 값을 `POST /takes` 의
 * 멱등 키로 씁니다. takeId 는 그 응답으로 받아서 나중에 채웁니다.
 */
export async function startSession(takeId: string | null = null): Promise<string> {
  const db = await openPitchDb();
  const clientSessionId = crypto.randomUUID();
  const now = Date.now();
  await db.put('session', {
    clientSessionId,
    takeId,
    status: 'RUNNING',
    startedAtIso: new Date(now).toISOString(),
    lastBeatAt: now,
    elapsedMs: 0,
    gazeExcluded: false,
    gazeExcludedReason: null,
    engineVersion: null,
    gazeAvgFps: null,
    gazeDroppedFrames: 0,
    prepare: null,
    ending: null,
    submittedAt: null,
  });
  return clientSessionId;
}

export async function getSession(clientSessionId: string): Promise<SessionRow | undefined> {
  return (await openPitchDb()).get('session', clientSessionId);
}

async function patchSession(
  clientSessionId: string,
  patch: Partial<SessionRow>,
): Promise<SessionRow | null> {
  const db = await openPitchDb();
  const tx = db.transaction('session', 'readwrite');
  const row = await tx.store.get(clientSessionId);
  if (!row) {
    await tx.done;
    return null;
  }
  const next = { ...row, ...patch };
  await tx.store.put(next);
  await tx.done;
  return next;
}

/** 하트비트. 5초마다. 벽시계로 찍습니다 — SessionRow.lastBeatAt 주석 참고 */
export async function beat(clientSessionId: string, elapsedMs: Ms): Promise<void> {
  await patchSession(clientSessionId, { lastBeatAt: Date.now(), elapsedMs });
}

/** 장치 점검에서 정한 것들을 남깁니다 (`SessionRow.prepare`) */
export async function setPrepareContext(
  clientSessionId: string,
  prepare: PrepareContext,
): Promise<void> {
  await patchSession(clientSessionId, { prepare });
}

/** 끝내기를 눌렀다 (`SessionRow.ending`). 처음 고정한 값을 지킵니다 */
export async function markEnding(
  clientSessionId: string,
  ending: NonNullable<SessionRow['ending']>,
): Promise<void> {
  const row = await getSession(clientSessionId);
  if (row?.ending) return;
  await patchSession(clientSessionId, { ending });
}

/** `/complete` 가 성공했다 (`SessionRow.submittedAt`) */
export async function markSubmitted(clientSessionId: string): Promise<void> {
  await patchSession(clientSessionId, { submittedAt: Date.now() });
}

export async function endSession(
  clientSessionId: string,
  status: Exclude<SessionStatus, 'RUNNING'> = 'ENDED',
): Promise<void> {
  await patchSession(clientSessionId, { status, lastBeatAt: Date.now() });
}

/**
 * 시선 측정을 제외로 표시합니다. **한 번 정해지면 되돌리지 않습니다** —
 * 발표 중간에 엔진이 죽었다면 그 Take 의 시선 숫자는 신뢰할 수 없습니다.
 */
export async function markGazeExcluded(
  clientSessionId: string,
  reason: GazeExcludedReason,
): Promise<void> {
  const row = await getSession(clientSessionId);
  if (row?.gazeExcluded) return; // 첫 사유를 유지합니다
  await patchSession(clientSessionId, { gazeExcluded: true, gazeExcludedReason: reason });
}

export async function setEngineVersion(clientSessionId: string, version: string): Promise<void> {
  await patchSession(clientSessionId, { engineVersion: version });
}

export async function setGazePerf(
  clientSessionId: string,
  avgFps: number,
  droppedFrames: number,
): Promise<void> {
  await patchSession(clientSessionId, { gazeAvgFps: avgFps, gazeDroppedFrames: droppedFrames });
}

// ── 시선 판정 ──────────────────────────────────────────────────────────

/** 1초 판정 하나를 쌓습니다. 초당 한 번 호출되므로 트랜잭션 비용이 문제되지 않습니다. */
export async function appendGazeDecision(
  clientSessionId: string,
  decision: ZoneDecision,
): Promise<void> {
  const db = await openPitchDb();
  await db.put('gazeSegments', { ...decision, clientSessionId });
}

export async function countGazeDecisions(clientSessionId: string): Promise<number> {
  const db = await openPitchDb();
  return db.count('gazeSegments', sessionRange(clientSessionId));
}

/** tMs 순으로 돌려줍니다 — compressToSegments 가 순서를 전제합니다 */
export async function readGazeDecisions(clientSessionId: string): Promise<ZoneDecision[]> {
  const db = await openPitchDb();
  const rows = await db.getAll('gazeSegments', sessionRange(clientSessionId));
  return rows
    .map(({ tMs, zone, confidence, sampleCount }) => ({ tMs, zone, confidence, sampleCount }))
    .sort((a, b) => a.tMs - b.tMs);
}

/**
 * 준비 화면에서 발급받은 takeId 를 세션에 적습니다 (CLAUDE.md 8번).
 * 세션은 takeId 보다 먼저 생깁니다 — clientSessionId 가 POST /takes 의 멱등키라서,
 * 발급을 요청하려면 그 값이 이미 있어야 합니다.
 */
export async function setTakeId(clientSessionId: string, takeId: string): Promise<void> {
  await patchSession(clientSessionId, { takeId });
}

/** 리허설이 무대를 열 때 쓸 값을 남깁니다. 장치 점검이 Take 를 만든 직후에 부릅니다 */
export async function setSessionTicket(
  clientSessionId: string,
  ticket: RehearsalTicket,
): Promise<void> {
  await patchSession(clientSessionId, { ticket });
}

/**
 * takeId 로 세션을 찾습니다. 리허설 주소를 다른 탭에서 열었을 때 쓰는 길입니다 —
 * 라우터 state 는 새로고침하면 남지만 탭을 옮기면 없습니다. 기록은 IndexedDB 에 남아 있습니다.
 * 같은 Take 로 두 번 시작된 세션이 있으면 **가장 최근 것**을 잇습니다.
 */
export async function findSessionByTakeId(takeId: string): Promise<SessionRow | null> {
  const db = await openPitchDb();
  const rows = await db.getAll('session');
  const mine = rows.filter((r) => r.takeId === takeId);
  if (mine.length === 0) return null;
  return mine.reduce((a, b) => (a.lastBeatAt >= b.lastBeatAt ? a : b));
}

/**
 * 슬라이드가 바뀐 시각. 전송 직전에 구간(`slideEvents`)으로 접습니다 —
 * 여기서 미리 접지 않는 이유는 시선 판정과 같습니다. 중간에 죽어도 부분 기록이 살아야 합니다.
 */
export async function appendSlideChange(
  clientSessionId: string,
  atMs: Ms,
  slideNumber: number,
): Promise<void> {
  const db = await openPitchDb();
  await db.put('slideChanges', { clientSessionId, atMs, slideNumber });
}

export async function readSlideChanges(
  clientSessionId: string,
): Promise<{ atMs: Ms; slideNumber: number }[]> {
  const db = await openPitchDb();
  const rows = await db.getAll('slideChanges', sessionRange(clientSessionId));
  return rows
    .map((r) => ({ atMs: r.atMs, slideNumber: r.slideNumber }))
    .sort((a, b) => a.atMs - b.atMs);
}

/**
 * 코치가 **띄운 것과 참은 것 둘 다** 남깁니다.
 *
 * 참은 기록이 없으면 "왜 안 떴나"를 나중에 알 수 없습니다. 임계값을 조정할 근거가
 * 그것뿐이라, 발동 기록만 남기면 다음 Take 에서 같은 문제가 반복됩니다
 * (`CompleteRequest.suppressedFeedbacks`).
 */
export async function appendCoachLog(
  clientSessionId: string,
  entry: {
    atMs: Ms;
    type: string;
    fired: boolean;
    message: string | null;
    suppressedReason: string | null;
  },
): Promise<void> {
  const db = await openPitchDb();
  await db.put('coachLog', { clientSessionId, ...entry });
}

export async function readCoachLog(clientSessionId: string) {
  const db = await openPitchDb();
  const rows = await db.getAll('coachLog', sessionRange(clientSessionId));
  return rows.sort((a, b) => a.atMs - b.atMs);
}

/** dev 페이지의 `기록: N건` 용 */
export async function countAll(clientSessionId: string): Promise<Record<string, number>> {
  const db = await openPitchDb();
  const range = sessionRange(clientSessionId);
  const [gaze, slides, scroll, coach] = await Promise.all([
    db.count('gazeSegments', range),
    db.count('slideChanges', range),
    db.count('scriptScroll', range),
    db.count('coachLog', range),
  ]);
  return {
    gazeSegments: gaze,
    slideChanges: slides,
    scriptScroll: scroll,
    coachLog: coach,
  };
}

/**
 * 비정상 종료로 남은 세션을 찾습니다 (T10 하트비트가 쓸 판단).
 *
 * lastBeatAt 이 10초 이내면 다른 탭에서 아직 돌고 있는 것이고,
 * 10초를 넘겼는데 RUNNING 이면 브라우저가 죽은 것입니다.
 * 발표 중에는 아무것도 띄울 수 없으니, 다음에 들어올 때 이 시각이 유일한 근거입니다.
 */
export async function findAbandonedSessions(staleAfterMs = 10_000): Promise<SessionRow[]> {
  const db = await openPitchDb();
  const running = await db.getAllFromIndex('session', 'byStatus', 'RUNNING');
  const now = Date.now();
  return running.filter((s) => now - s.lastBeatAt > staleAfterMs);
}

/* ------------------------------------------------------------------ */
/* 캘리브레이션 기준 — 이번 Take 동안만 둡니다                           */
/* ------------------------------------------------------------------ */

/**
 * 기준을 얼마나 믿나. 리허설 새로고침에서 되살리는 데만 쓰므로 Take 한 번이면 충분합니다.
 *
 * 기준(`model`)에는 얼굴 측정값(얼굴 위치·크기, 홍채 크기, 머리 거리)이 들어 있어
 * AI 엔진은 메모리에만 두라고 합니다 (`CalibrationModel`). 장치 점검과 리허설이 서로 다른
 * 워커라 넘겨 줄 곳이 필요하고, 리허설을 새로고침해도 시선이 이어져야 해서 IndexedDB 에 둡니다.
 * 대신 **Take 가 끝나면 지우고**(`clearZoneRefs`), 다음 Take 에서 다시 쓰지 않습니다.
 * 이 시간은 지우지 못하고 떠났을 때(탭을 닫음 · 종료를 누르지 않음)의 안전장치입니다.
 */
export const ZONE_REF_TTL_MS = 3 * 60 * 60 * 1000;

/**
 * 기준을 저장합니다. `fitCalibration` 이 낸 값을 그대로 넣고, 키는 FE 가 붙입니다.
 *
 * `model` 안에 무엇이 들었는지 FE 는 모릅니다 — 분류기가 정하고,
 * IndexedDB 는 구조화 복제로 그대로 보관합니다. 서버로는 보내지 않습니다.
 */
export async function saveZoneRef(
  layoutSignature: string,
  ref: ZoneReference,
  engineVersion: string,
): Promise<void> {
  const db = await openPitchDb();
  await db.put('zoneRefs', { ...ref, layoutSignature, engineVersion, fittedAt: Date.now() });
}

/**
 * 이번 Take 의 기준을 꺼냅니다. 없으면 `null` — 리허설은 시선을 제외합니다.
 *
 * `layoutSignature` 가 다르면 애초에 키가 달라 안 잡힙니다.
 * `ZONE_REF_TTL_MS` 보다 오래된 기준은 지우지 못하고 남은 것이라 쓰지 않습니다.
 *
 * **엔진이 바뀌었으면 버립니다.** 모델이 업데이트되면 `model` 안의 모양과 뜻이 달라질 수
 * 있는데, 분류기의 `calibrate()` 는 반환값이 없어 "이건 못 쓴다"고 말할 수 없습니다.
 * 그대로 넣으면 판정이 조용히 틀어집니다. AI 도 스키마가 다른 기준은 거부합니다.
 * 버전이 없는 옛 행도 같은 이유로 버립니다.
 */
export async function loadZoneRef(
  layoutSignature: string,
  engineVersion: string,
  maxAgeMs = ZONE_REF_TTL_MS,
): Promise<ZoneReference | null> {
  const db = await openPitchDb();
  const row = await db.get('zoneRefs', layoutSignature);
  if (!row) return null;
  if (row.engineVersion !== engineVersion) return null;
  if (Date.now() - row.fittedAt > maxAgeMs) return null;

  const { fittedAt: _fittedAt, layoutSignature: _key, engineVersion: _engine, ...ref } = row;
  return ref;
}

/**
 * 저장된 기준을 모두 지웁니다. 장치 점검에 들어올 때(지난 Take 가 남긴 것)와
 * Take 를 끝낼 때 부릅니다. 한 기기에서 동시에 도는 Take 는 하나라 키를 가리지 않습니다.
 */
export async function clearZoneRefs(): Promise<void> {
  const db = await openPitchDb();
  await db.clear('zoneRefs');
}
