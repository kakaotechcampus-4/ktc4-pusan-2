import { useCallback, useEffect, useRef, useState } from 'react';
import {
  STT_ERROR_MESSAGE,
  type SttState,
  type SttStatusMessage,
  type TranscriptMessage,
} from '../lib/sttProtocol';
import { SttSocket, sttUrl } from '../lib/sttSocket';
import { startPcmCapture, type PcmCapture } from './pcmCapture';
import { refresh } from '@/shared/api/tokenStore';

/**
 * 실시간 STT 프로토콜 검증 페이지. **제품 화면이 아닙니다.**
 *
 * ── 왜 따로 있나 ──────────────────────────────────────────────────
 *
 * 리허설 화면은 `GET /api/takes/{id}`·`GET /api/pitches/{id}`가 서 있어야 뜨는데
 * BE에는 아직 그 둘이 없습니다. 그래서 무대를 실서버로 띄울 수가 없고,
 * 목을 켜면 MSW가 `/api/auth/refresh`를 가로채 실 토큰을 못 받습니다.
 *
 * 이 페이지는 takeId를 손으로 넣어 **WebSocket 경로만** 실서버에 붙입니다.
 * `backend/dev/stt-test.html`과 같은 역할이지만, 무대가 쓰는 것과 **같은 모듈**
 * (`sttSocket`·`pcmCapture`)을 태웁니다 — 그래야 여기서 통과한 것이 무대에서도 돕니다.
 *
 * ── 쓰는 법 ───────────────────────────────────────────────────────
 *
 *   cd backend && uv run uvicorn pitch_coach_backend.main:app --port 8000
 *   uv run python dev/stt-send-pcm.py --create-take --email <가입한 이메일>
 *   cd frontend && VITE_USE_MOCK=false npm run dev     # 3000 이어야 Origin 검사를 통과합니다
 */

const TAKE_ID_STORAGE_KEY = 'stt-dev-take-id';

const MAX_LOG_LINES = 60;

/**
 * 목이 켜져 있으면 WebSocket 도 MSW 가 가로챕니다 — 실서버가 아니라 가짜 전사가 옵니다.
 * 이 줄이 없으면 "붙긴 붙는데 BE 로그에 아무것도 안 찍히는" 시간을 한참 씁니다.
 */
const MOCKED = import.meta.env.DEV && import.meta.env.VITE_USE_MOCK !== 'false';

export function SttDevPage() {
  // 마지막에 쓴 takeId 를 그대로 띄웁니다 — dev 페이지를 열 때마다 붙여넣지 않게
  const [takeId, setTakeId] = useState(() => localStorage.getItem(TAKE_ID_STORAGE_KEY) ?? '');
  const [tokenNote, setTokenNote] = useState('아직 받지 않음');
  const [connected, setConnected] = useState(false);
  const [sttState, setSttState] = useState<SttState | null>(null);
  const [status, setStatus] = useState<SttStatusMessage | null>(null);
  const [finals, setFinals] = useState<TranscriptMessage[]>([]);
  const [log, setLog] = useState<string[]>([]);

  /** 중간 결과는 100~300ms마다 옵니다 — 무대와 같은 이유로 DOM에 직접 씁니다 */
  const interimRef = useRef<HTMLParagraphElement>(null);
  const socketRef = useRef<SttSocket | null>(null);
  const captureRef = useRef<PcmCapture | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const say = useCallback((line: string) => {
    const at = new Date().toLocaleTimeString('ko-KR', { hour12: false });
    setLog((previous) => [`[${at}] ${line}`, ...previous].slice(0, MAX_LOG_LINES));
  }, []);

  const releaseDevices = useCallback(async () => {
    await captureRef.current?.stop().catch(() => undefined);
    captureRef.current = null;

    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  const getToken = useCallback(async () => {
    try {
      await refresh();
      setTokenNote('있음 (메모리)');
      say('토큰을 받았습니다. WebSocket 첫 메시지로 보냅니다.');
    } catch {
      setTokenNote('실패');
      say('토큰을 받지 못했습니다 — 로그인 상태인지, VITE_USE_MOCK=false 인지 확인하세요.');
    }
  }, [say]);

  const start = useCallback(async () => {
    const id = takeId.trim();
    if (id === '') {
      say('takeId 가 필요합니다 (dev/stt-send-pcm.py --create-take 로 발급).');
      return;
    }
    localStorage.setItem(TAKE_ID_STORAGE_KEY, id);

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
    } catch (error) {
      say(`마이크를 열지 못했습니다: ${(error as Error).name}`);
      return;
    }
    streamRef.current = stream;

    setFinals([]);
    setStatus(null);

    const socket = new SttSocket({
      takeId: id,
      onTranscript: (message) => {
        if (message.is_final) {
          setFinals((previous) => [...previous, message]);
          if (interimRef.current) interimRef.current.textContent = '';
          return;
        }
        if (interimRef.current) interimRef.current.textContent = message.text;
      },
      onState: (state) => {
        setSttState(state);
        say(`stt_state = ${state}`);
      },
      onStatus: (message) => setStatus(message),
      onError: (code, message) => say(`error ${code}: ${message} — ${STT_ERROR_MESSAGE[code]}`),
      onGiveUp: (reason) => say(`연결을 포기했습니다 (${reason}).`),
    });
    socketRef.current = socket;
    socket.start();
    say(`${sttUrl(id)} 에 붙습니다.`);

    const { capture, error } = await startPcmCapture(stream, {
      onFrame: ({ pcm, offsetMs }) => socket.sendFrame(pcm, offsetMs),
    });

    if (error || !capture) {
      say(`오디오 캡처 실패: ${error}`);
      socket.dispose();
      socketRef.current = null;
      await releaseDevices();
      return;
    }

    captureRef.current = capture;
    setConnected(true);
    say(`마이크를 열었습니다 (${capture.sampleRate}Hz, AudioContext ${capture.state}).`);
  }, [takeId, say, releaseDevices]);

  const stop = useCallback(async () => {
    say('stop 을 보내고 closed 를 기다립니다 (최대 15초).');

    await releaseDevices();
    await socketRef.current?.stop();
    socketRef.current = null;

    setConnected(false);
    say('정리했습니다.');
  }, [say, releaseDevices]);

  // 화면을 떠날 때 마이크와 소켓을 놓습니다 — 안 하면 탭에 녹음 표시가 남습니다
  useEffect(
    () => () => {
      socketRef.current?.dispose();
      socketRef.current = null;
      releaseDevices().catch(() => undefined);
    },
    [releaseDevices],
  );

  return (
    <div className="min-h-full bg-greige px-6 py-8">
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-5">
        <header className="flex flex-col gap-1">
          <h1 className="text-xl font-bold">실시간 STT 검증 · /dev/stt</h1>
          <p className="text-sm text-stone">
            마이크 → AudioWorklet(16kHz mono Int16) → <code>WS /api/ws/takes/{'{takeId}'}</code> →
            Deepgram. 무대와 같은 모듈을 씁니다.
          </p>
          {MOCKED && (
            <p className="text-sm font-semibold text-coral">
              지금은 목 모드입니다 — MSW 가 WebSocket 을 가로채 가짜 전사를 보냅니다. 실서버로
              확인하려면 <code>VITE_USE_MOCK=false</code> 로 다시 띄우세요.
            </p>
          )}
        </header>

        <section className="flex flex-wrap items-end gap-3 rounded-xl bg-panel p-4">
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-semibold">takeId</span>
            <input
              className="w-96 rounded-lg border border-line px-3 py-2 font-mono text-sm"
              value={takeId}
              onChange={(event) => setTakeId(event.target.value)}
              placeholder="dev/stt-send-pcm.py --create-take 로 발급"
            />
          </label>

          <button
            type="button"
            className="rounded-lg border border-line-strong px-4 py-2 text-sm font-semibold"
            onClick={() => {
              getToken().catch((error: Error) => say(`getToken 실패: ${error.message}`));
            }}
          >
            1 · 토큰 받기
          </button>

          <button
            type="button"
            className="rounded-lg bg-coral px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
            disabled={connected}
            onClick={() => {
              start().catch((error: Error) => say(`start 실패: ${error.message}`));
            }}
          >
            2 · 시작
          </button>

          <button
            type="button"
            className="rounded-lg border border-line-strong px-4 py-2 text-sm font-semibold disabled:opacity-40"
            disabled={!connected}
            onClick={() => {
              stop().catch((error: Error) => say(`stop 실패: ${error.message}`));
            }}
          >
            3 · 중지
          </button>
        </section>

        <section className="grid grid-cols-2 gap-3 rounded-xl bg-panel p-4 text-sm sm:grid-cols-4">
          <Stat label="토큰" value={tokenNote} />
          <Stat label="STT 상태" value={sttState ?? '—'} />
          <Stat
            label="프레임 (무음)"
            value={status ? `${status.frames} (${status.silence_ms}ms)` : '—'}
          />
          <Stat
            label="유실"
            value={status ? `${status.dropped_frames}개 / ${status.lost_ms}ms` : '—'}
          />
        </section>

        <section className="flex flex-col gap-2 rounded-xl bg-panel p-4">
          <h2 className="text-sm font-bold">중간 결과</h2>
          <p ref={interimRef} className="min-h-6 text-sm text-stone" />

          <h2 className="mt-2 text-sm font-bold">확정 전사 ({finals.length})</h2>
          <ol className="flex max-h-64 flex-col gap-1 overflow-auto text-sm">
            {finals.map((final) => (
              <li key={final.segment_id} className="flex gap-2">
                <span className="shrink-0 font-mono text-xs text-stone">
                  {(final.start_ms / 1000).toFixed(1)}s
                </span>
                <span>{final.text}</span>
              </li>
            ))}
          </ol>
        </section>

        <section className="flex flex-col gap-1 rounded-xl bg-cream p-4">
          <h2 className="text-sm font-bold">로그</h2>
          <pre className="max-h-64 overflow-auto text-xs leading-5 whitespace-pre-wrap">
            {log.join('\n')}
          </pre>
        </section>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-xs text-stone">{label}</span>
      <span className="font-semibold">{value}</span>
    </div>
  );
}
