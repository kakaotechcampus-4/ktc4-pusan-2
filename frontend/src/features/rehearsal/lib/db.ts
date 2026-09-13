import { openDB, type DBSchema, type IDBPDatabase } from 'idb';
import type { GazeExcludedReason, Ms } from '@/types/api';
import type { ZoneDecision, ZoneReference } from '@/workers/gaze.contract';

/**
 * 리허설 중 쌓이는 모든 기록. **브라우저가 원본입니다** (CLAUDE.md 4번).
 *
 * 스키마를 이 파일 한 곳에 모읍니다. 흩어지면 복구 로직이 무너집니다 —
 * 비정상 종료를 감지하려면 session·gazeSegments·audioChunks 를 같은 관점에서
 * 봐야 하는데, 접근 경로가 갈리면 "기록이 남아 있는데 진행 중이었다"를 판단할 수 없습니다.
 *
 * 키는 `clientSessionId` 입니다. 업로드 재시도의 멱등 키와 **같은 값**입니다
 * (`CompleteRequest.clientSessionId`) — 그래서 재시도해도 서버가 Take 를 새로 만들지 않습니다.
 */

const DB_NAME = 'pitchcoach';
const DB_VERSION = 2;

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
  /** ★ 원본. 메모리에 10분을 들고 있으면 안 됩니다 */
  audioChunks: {
    key: [string, number];
    value: { clientSessionId: string; seq: number; offsetMs: Ms; blob: Blob };
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
    value: ZoneReference & { fittedAt: number };
  };
}

let dbPromise: Promise<IDBPDatabase<PitchDb>> | null = null;

export function openPitchDb(): Promise<IDBPDatabase<PitchDb>> {
  dbPromise ??= openDB<PitchDb>(DB_NAME, DB_VERSION, {
    upgrade(db) {
      const session = db.createObjectStore('session', { keyPath: 'clientSessionId' });
      session.createIndex('byStatus', 'status');

      // 복합 키 [clientSessionId, 시각] — 세션별 범위 조회가 그냥 됩니다.
      db.createObjectStore('gazeSegments', { keyPath: ['clientSessionId', 'tMs'] });
      db.createObjectStore('slideChanges', { keyPath: ['clientSessionId', 'atMs'] });
      db.createObjectStore('scriptScroll', { keyPath: ['clientSessionId', 'atMs'] });
      db.createObjectStore('coachLog', { keyPath: ['clientSessionId', 'atMs'] });
      db.createObjectStore('audioChunks', { keyPath: ['clientSessionId', 'seq'] });

      // v2 — 캘리브레이션 기준. 세션이 아니라 기기(layoutSignature)에 매입니다.
      if (!db.objectStoreNames.contains('zoneRefs')) {
        db.createObjectStore('zoneRefs', { keyPath: 'layoutSignature' });
      }
    },
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

// ── 오디오 조각 ────────────────────────────────────────────────────────

/**
 * 녹음 조각 하나를 쌓습니다. **메모리에 들고 있지 않습니다** —
 * 10분치 Blob 을 배열에 모으면 수십 MB 가 힙에 남고, 탭이 죽으면 전부 사라집니다.
 *
 * seq 는 도착 순서, offsetMs 는 그 조각이 시작된 발표 경과 시각입니다.
 * 나중에 이어 붙일 때와, 끊긴 구간을 배치로 다시 처리할 때 둘 다 필요합니다.
 */
export async function appendAudioChunk(
  clientSessionId: string,
  seq: number,
  offsetMs: Ms,
  blob: Blob,
): Promise<void> {
  const db = await openPitchDb();
  await db.put('audioChunks', { clientSessionId, seq, offsetMs, blob });
}

export async function countAudioChunks(clientSessionId: string): Promise<number> {
  const db = await openPitchDb();
  return db.count('audioChunks', sessionRange(clientSessionId));
}

/** 녹음 총 바이트 — dev 페이지에서 실제로 쌓이는지 눈으로 보려고 */
export async function audioBytes(clientSessionId: string): Promise<number> {
  const db = await openPitchDb();
  const rows = await db.getAll('audioChunks', sessionRange(clientSessionId));
  return rows.reduce((n, r) => n + r.blob.size, 0);
}

/** dev 페이지의 `기록: N건` 용 */
export async function countAll(clientSessionId: string): Promise<Record<string, number>> {
  const db = await openPitchDb();
  const range = sessionRange(clientSessionId);
  const [gaze, slides, scroll, coach, audio] = await Promise.all([
    db.count('gazeSegments', range),
    db.count('slideChanges', range),
    db.count('scriptScroll', range),
    db.count('coachLog', range),
    db.count('audioChunks', range),
  ]);
  return {
    gazeSegments: gaze,
    slideChanges: slides,
    scriptScroll: scroll,
    coachLog: coach,
    audioChunks: audio,
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
/* 캘리브레이션 기준 — 기기별로 보관하고 다음 Take 에서 되살립니다        */
/* ------------------------------------------------------------------ */

/**
 * 기준을 저장합니다. `fitCalibration` 이 낸 값을 그대로 넣습니다.
 *
 * `model` 안에 무엇이 들었는지 FE 는 모릅니다 — 분류기가 정하고,
 * IndexedDB 는 구조화 복제로 그대로 보관합니다.
 */
export async function saveZoneRef(ref: ZoneReference): Promise<void> {
  const db = await openPitchDb();
  await db.put('zoneRefs', { ...ref, fittedAt: Date.now() });
}

/**
 * 이 기기의 기준을 되살립니다. 없으면 `null` — 캘리브레이션을 다시 받습니다.
 *
 * `layoutSignature` 가 다르면 애초에 키가 달라 안 잡힙니다.
 * 같은 기기라도 오래된 기준은 쓰지 않습니다 — 카메라를 옮겼거나
 * 앉은 자리가 바뀌었을 가능성이 시간과 함께 커집니다.
 */
export async function loadZoneRef(
  layoutSignature: string,
  maxAgeMs = 7 * 24 * 60 * 60 * 1000,
): Promise<ZoneReference | null> {
  const db = await openPitchDb();
  const row = await db.get('zoneRefs', layoutSignature);
  if (!row) return null;
  if (Date.now() - row.fittedAt > maxAgeMs) return null;

  const { fittedAt: _fittedAt, ...ref } = row;
  return ref;
}
