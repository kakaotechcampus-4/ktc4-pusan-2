import { afterEach, describe, expect, it, vi } from 'vitest';
import { deleteDB, openDB, type IDBPDatabase } from 'idb';

/**
 * db.ts 의 스키마 마이그레이션.
 *
 * ── 왜 이 테스트가 있나 ────────────────────────────────────────────
 * `upgrade` 는 **버전이 오를 때만** 도는 데다, 브라우저마다 출발점이 다릅니다.
 * 처음 여는 사람은 0 에서, 예전부터 쓰던 사람은 v1 에서 올라옵니다. 두 경로가
 * 다른 모양으로 끝나면 사람마다 다른 DB 가 생기는데, **에러가 안 납니다.**
 * 개발 중에는 DB 를 자주 지워서 늘 "처음 여는" 경로만 밟으니 재현도 안 됩니다.
 *
 * 그래서 출발점을 손으로 만들어 두고, 도착점이 같은지 기계가 보게 합니다.
 *
 * IndexedDB 는 브라우저 API 라 Node 에 없습니다 — fake-indexeddb 를
 * setupFiles 로 꽂습니다 (vite.config.ts).
 */

const DB_NAME = 'pitchcoach';

/** v1 이 실제로 만들던 것. 히스토리의 9ec354a^ 시점 upgrade 그대로입니다 */
async function createV1(): Promise<void> {
  const db = await openDB(DB_NAME, 1, {
    upgrade(db) {
      const session = db.createObjectStore('session', { keyPath: 'clientSessionId' });
      session.createIndex('byStatus', 'status');
      db.createObjectStore('gazeSegments', { keyPath: ['clientSessionId', 'tMs'] });
      db.createObjectStore('slideChanges', { keyPath: ['clientSessionId', 'atMs'] });
      db.createObjectStore('scriptScroll', { keyPath: ['clientSessionId', 'atMs'] });
      db.createObjectStore('coachLog', { keyPath: ['clientSessionId', 'atMs'] });
      db.createObjectStore('audioChunks', { keyPath: ['clientSessionId', 'seq'] });
    },
  });
  db.close();
}

type DbModule = typeof import('./db');

/** 이번 테스트가 만든 모듈들. afterEach 에서 이들이 연 핸들을 닫습니다 */
const loaded: DbModule[] = [];

/**
 * db.ts 는 `dbPromise` 를 모듈 수준에 캐시합니다. 매번 새로 import 해야
 * 앞 테스트가 열어 둔 핸들을 물고 가지 않습니다.
 *
 * ★ 연 것은 반드시 닫아야 합니다 — 연결이 하나라도 남아 있으면
 *   `deleteDB` 가 영원히 기다립니다 (타임아웃으로만 드러납니다).
 */
async function freshDb(): Promise<DbModule> {
  vi.resetModules();
  const mod = await import('./db');
  loaded.push(mod);
  return mod;
}

/** 스토어와 인덱스를 한 덩이로 — 두 경로의 결과를 이걸로 비교합니다 */
function shapeOf(db: IDBPDatabase<unknown>) {
  const tx = db.transaction([...db.objectStoreNames], 'readonly');
  const shape = [...db.objectStoreNames].sort().map((name) => {
    const store = tx.objectStore(name);
    return {
      name,
      keyPath: store.keyPath,
      indexes: [...store.indexNames].sort(),
    };
  });
  tx.done.catch(() => undefined);
  return shape;
}

let open: IDBPDatabase<unknown> | null = null;

afterEach(async () => {
  open?.close();
  open = null;

  // 모듈이 캐시해 둔 연결까지 닫습니다. 열린 적이 없으면 여기서 한 번 열렸다
  // 바로 닫히므로 무해합니다.
  for (const mod of loaded) {
    const db = await mod.openPitchDb();
    db.close();
  }
  loaded.length = 0;

  await deleteDB(DB_NAME);
});

describe('스키마 마이그레이션', () => {
  it('처음 여는 브라우저는 스토어 7개와 byStatus 인덱스를 갖는다', async () => {
    const { openPitchDb } = await freshDb();
    open = (await openPitchDb()) as unknown as IDBPDatabase<unknown>;

    expect([...open.objectStoreNames].sort()).toEqual([
      'audioChunks',
      'coachLog',
      'gazeSegments',
      'scriptScroll',
      'session',
      'slideChanges',
      'zoneRefs',
    ]);
    expect(open.version).toBe(2);
  });

  /**
   * ★ 이 테스트가 리뷰어의 "다른 곳에 영향이 없는지" 에 대한 답입니다.
   *   어디서 출발하든 끝나면 같은 모양이어야 합니다.
   */
  it('0 에서 열든 v1 에서 올라오든 같은 스키마가 된다', async () => {
    const first = await freshDb();
    open = (await first.openPitchDb()) as unknown as IDBPDatabase<unknown>;
    const fromScratch = shapeOf(open);
    open.close();
    open = null;
    await deleteDB(DB_NAME);

    await createV1();
    const second = await freshDb();
    open = (await second.openPitchDb()) as unknown as IDBPDatabase<unknown>;
    const fromV1 = shapeOf(open);

    expect(fromV1).toEqual(fromScratch);
  });

  it('v1 에 쌓여 있던 기록은 v2 로 올라와도 살아 있다', async () => {
    await createV1();

    // v1 스토어에 직접 한 행 넣습니다
    const v1 = await openDB(DB_NAME, 1);
    await v1.put('gazeSegments', {
      clientSessionId: 's1',
      tMs: 1000,
      zone: 'CAMERA',
      confidence: 0.9,
      sampleCount: 12,
    });
    v1.close();

    const { readGazeDecisions } = await freshDb();
    const rows = await readGazeDecisions('s1');

    expect(rows).toHaveLength(1);
    expect(rows[0]?.zone).toBe('CAMERA');
  });

  it('v1 에서 올라오면 zoneRefs 가 새로 생긴다 — v2 가 더한 것', async () => {
    await createV1();

    const v1 = await openDB(DB_NAME, 1);
    expect([...v1.objectStoreNames]).not.toContain('zoneRefs');
    v1.close();

    const { openPitchDb } = await freshDb();
    open = (await openPitchDb()) as unknown as IDBPDatabase<unknown>;

    expect([...open.objectStoreNames]).toContain('zoneRefs');
  });

  it('byStatus 인덱스가 두 경로 모두에 있다 — findAbandonedSessions 가 이걸 쓴다', async () => {
    await createV1();
    const { openPitchDb, startSession, findAbandonedSessions } = await freshDb();
    open = (await openPitchDb()) as unknown as IDBPDatabase<unknown>;

    const tx = open.transaction('session', 'readonly');
    expect([...tx.objectStore('session').indexNames]).toContain('byStatus');
    tx.done.catch(() => undefined);

    // 인덱스가 실제로 조회에 쓰이는지까지 봅니다
    await startSession();
    await expect(findAbandonedSessions(-1)).resolves.toHaveLength(1);
  });
});

describe('키 구조가 만드는 성질', () => {
  /**
   * ★ 이번에 실제로 터진 사고의 메커니즘입니다.
   *   종료 실패 뒤 무대를 되살리면 녹음이 seq 0 부터 다시 시작하는데,
   *   키가 [clientSessionId, seq] 라 put 이 원본을 소리 없이 덮었습니다.
   *   단계 잠금(rehearsalStore)으로 막았지만, 왜 위험한지는 여기 남깁니다.
   */
  it('같은 seq 로 다시 넣으면 원본이 덮인다', async () => {
    const { appendAudioChunk, countAudioChunks, audioBytes } = await freshDb();

    await appendAudioChunk('s1', 1, 0, new Blob(['원본원본원본']));
    await appendAudioChunk('s1', 2, 5000, new Blob(['원본2']));
    const before = await audioBytes('s1');

    // 다시 녹음이 시작된 상황 — seq 가 1 부터 또 옵니다
    await appendAudioChunk('s1', 1, 0, new Blob(['새']));

    expect(await countAudioChunks('s1')).toBe(2); // 늘지 않습니다
    expect(await audioBytes('s1')).toBeLessThan(before); // 줄어듭니다 — 덮였다는 뜻
  });

  it('세션이 다르면 같은 seq 라도 따로 쌓인다', async () => {
    const { appendAudioChunk, countAudioChunks } = await freshDb();

    await appendAudioChunk('s1', 1, 0, new Blob(['a']));
    await appendAudioChunk('s2', 1, 0, new Blob(['b']));

    expect(await countAudioChunks('s1')).toBe(1);
    expect(await countAudioChunks('s2')).toBe(1);
  });

  it('조각을 넣은 만큼 세어진다 — 종료 시 대조가 이 값을 믿는다', async () => {
    const { appendAudioChunk, countAudioChunks } = await freshDb();

    for (let seq = 1; seq <= 5; seq++) {
      await appendAudioChunk('s1', seq, (seq - 1) * 5000, new Blob(['x']));
    }

    expect(await countAudioChunks('s1')).toBe(5);
  });
});
