import { toMessage } from '@/shared/api/errorMessage';
import { createScript, getScript, reparseScript } from '@/shared/api/script';
import { useCreateStore } from './createStore';
import { fromScriptCreated, fromScriptDetail } from './lib/beAdapter';

/**
 * 대본 매핑 — 서버에 올리고(`POST /scripts`, 202) → 나눌 때까지 폴링(`GET /scripts/{id}`).
 *
 * ── 왜 컴포넌트 밖인가 ─────────────────────────────────────────────
 * `slideUpload.ts` 와 같은 이유입니다. 나누는 동안 사이드바에서 다른 화면으로 가도
 * 결과는 그 대본 버전에 들어가야 합니다. 그래서 진행 상태를 스토어에 두고 여기서 씁니다.
 *
 * 실패는 스토어의 `parse` 로 화면에 올리고, 이 함수들은 던지지 않습니다.
 */

/** BE 가 정한 폴링 간격 (컨트롤러 주석: "FE 가 1초마다 부른다") */
const POLL_MS = 1000;

/**
 * 이만큼 기다려도 PENDING 이면 멈춥니다. BE 는 90초가 지나면 스스로 FAILED(PARSE_EXPIRED)로
 * 바꾸므로, 그보다 조금 길게 잡아 BE 의 판정을 받아 봅니다.
 */
const GIVE_UP_MS = 100_000;

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

const failed = (message: string) => ({ status: 'failed' as const, message });

/** 그사이 글을 고쳤거나 다시 올렸으면, 이 서버 버전의 결과는 더 이상 그 대본의 것이 아닙니다 */
function stillCurrent(version: number, remoteId: string): boolean {
  const script = useCreateStore.getState().draft.scripts.find((v) => v.version === version);
  return script?.remote?.id === remoteId;
}

async function poll(pitchId: string, version: number, remoteId: string): Promise<void> {
  const { patchScript } = useCreateStore.getState();
  const startedAt = Date.now();

  while (Date.now() - startedAt < GIVE_UP_MS) {
    await sleep(POLL_MS);
    if (!stillCurrent(version, remoteId)) return;

    let progress;
    try {
      progress = fromScriptDetail(await getScript(pitchId, remoteId));
    } catch (error) {
      // 한 번 못 받은 것으로 멈추지 않습니다 — 다음 폴링이 받으면 됩니다
      console.error('[대본] 나누기 상태를 받지 못했습니다', { remoteId, error });
      continue;
    }
    if (!stillCurrent(version, remoteId)) return;

    if (progress.status === 'done') {
      patchScript(version, {
        parse: { status: 'idle' },
        blocks: progress.blocks,
        segmented: progress.segmented,
      });
      return;
    }
    if (progress.status === 'failed') {
      console.error('[대본] 서버가 나누지 못했습니다', { remoteId, code: progress.errorCode });
      patchScript(version, {
        parse: failed('대본을 슬라이드로 나누지 못했어요. 다시 시도해 주세요.'),
      });
      return;
    }
  }
  patchScript(version, { parse: failed('나누는 데 너무 오래 걸려요. 다시 시도해 주세요.') });
}

/** "대본 매핑" — 이 대본을 서버에 새 버전으로 올리고 나눈 결과를 기다립니다 */
async function upload(version: number): Promise<void> {
  const { pitchId, draft, patchScript } = useCreateStore.getState();
  const script = draft.scripts.find((v) => v.version === version);
  if (!pitchId || !script || script.parse.status === 'pending') return;

  patchScript(version, { parse: { status: 'pending' } });

  let created;
  try {
    created = fromScriptCreated(await createScript(pitchId, script.text));
  } catch (error) {
    console.error('[대본] 올리지 못했습니다', { pitchId, version, error });
    patchScript(version, { parse: failed(toMessage(error)) });
    return;
  }

  patchScript(version, {
    remote: created,
  });
  await poll(pitchId, version, created.id);
}

/** FAILED 였던 서버 버전을 다시 나눕니다. 원문은 서버가 들고 있습니다 */
async function retry(version: number): Promise<void> {
  const { pitchId, draft, patchScript } = useCreateStore.getState();
  const remote = draft.scripts.find((v) => v.version === version)?.remote;
  if (!pitchId) return;
  // 올리기부터 실패했으면 서버에 아무것도 없습니다 — 처음부터 올립니다
  if (!remote) return upload(version);

  patchScript(version, { parse: { status: 'pending' } });
  try {
    await reparseScript(pitchId, remote.id);
  } catch (error) {
    console.error('[대본] 다시 나누기를 요청하지 못했습니다', { remoteId: remote.id, error });
    patchScript(version, { parse: failed(toMessage(error)) });
    return;
  }
  await poll(pitchId, version, remote.id);
}

/** 화면에서 부르는 입구. 위 함수들은 실패를 스토어로 올리므로, 여기까지 오는 건 예상 밖의 오류뿐입니다 */
export function startScriptMapping(version: number): void {
  upload(version).catch((err: unknown) => {
    console.error('[대본] 매핑 중 예상 밖의 오류', err);
  });
}

export function retryScriptMapping(version: number): void {
  retry(version).catch((err: unknown) => {
    console.error('[대본] 다시 나누기 중 예상 밖의 오류', err);
  });
}
