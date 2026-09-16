import { useEffect, useRef, useState } from 'react';
import { createLevelMeter, type LevelMeter } from './level';

/** 화면에 쓰는 dB 바닥. 이보다 작으면 '입력 없음'으로 봅니다 */
const DB_FLOOR = -60;

/** 무음이 이만큼 이어지면 권한은 있는데 입력이 없는 상황입니다 (명세 8-3) */
const SILENT_WARN_MS = 8_000;

function toDb(rms: number): number {
  if (rms <= 0) return DB_FLOOR;
  return Math.max(DB_FLOOR, Math.min(0, 20 * Math.log10(rms)));
}

/**
 * 계량기를 칠합니다. **상태로 올리지 않습니다** — 초당 수십 번 바뀝니다.
 *
 * 두 모양을 한 함수가 처리합니다. 어느 쪽인지는 엘리먼트가 `data-meter`로 말합니다.
 *   clip  — 칸 나뉜 막대(장치 점검). 칸 수를 유지해야 해서 폭 대신 잘라냅니다
 *   width — 한 줄 막대(리허설 준비)
 */
function paint(el: HTMLElement | null, percent: number): void {
  if (!el) return;
  const v = Math.max(0, Math.min(100, percent));
  if (el.dataset.meter === 'clip') {
    el.style.clipPath = `inset(0 ${(100 - v).toFixed(1)}% 0 0)`;
  } else {
    el.style.width = `${v.toFixed(1)}%`;
  }
}

/**
 * 마이크 음량.
 *
 * 그리는 일은 전부 DOM에 직접 씁니다. 상태로 올라가는 건 **한 번씩만 바뀌는 두 가지**뿐입니다
 * — 소리가 들어왔나, AudioContext가 흐르나. 같은 메인 스레드에서 시선 프레임 펌프가
 * 도는 화면이라, 음량을 setState로 올리면 그만큼 fps가 깎입니다.
 *
 * 둘 다 "어느 스트림에서 나온 값인가"를 같이 들고 있다가 렌더에서 비교합니다.
 * 그래야 장치를 바꿨을 때 이전 마이크의 판정이 그대로 남지 않고,
 * 이펙트 안에서 상태를 되돌리는 setState도 필요 없습니다.
 */
export function useMicLevel(stream: MediaStream | null) {
  const meterRef = useRef<HTMLDivElement>(null);
  /** 계량기 옆 숫자 (-12) */
  const dbRef = useRef<HTMLSpanElement>(null);
  /** 점검 항목 줄 전체 문구 ('마이크 입력 -12dB') */
  const rowRef = useRef<HTMLSpanElement>(null);
  const silentRef = useRef<HTMLSpanElement>(null);

  /**
   * 지금 값. **코치 규칙이 읽는 창구입니다.**
   * 상태가 아니라 ref 인 이유는 초당 수십 번 바뀌기 때문입니다 —
   * 규칙 쪽은 1초에 한 번 들여다보기만 하면 됩니다.
   */
  const statsRef = useRef({ db: -60, silentMs: 0 });

  const [heardFor, setHeardFor] = useState<MediaStream | null>(null);
  const [audio, setAudio] = useState<{ stream: MediaStream; state: AudioContextState } | null>(
    null,
  );

  useEffect(() => {
    if (!stream) return;

    let raf = 0;
    let cancelled = false;
    let meter: LevelMeter | null = null;
    let heard = false;

    void (async () => {
      // 제스처 뒤에 만듭니다 — getUserMedia가 이미 통한 시점이라 resume()이 먹습니다
      meter = await createLevelMeter(stream);
      if (cancelled) {
        meter?.stop();
        return;
      }
      if (meter) setAudio({ stream, state: meter.state });

      const loop = () => {
        raf = requestAnimationFrame(loop);
        if (!meter) return;

        const db = toDb(meter.read());
        statsRef.current.db = db;
        statsRef.current.silentMs = meter.silentMs();
        paint(meterRef.current, ((db - DB_FLOOR) / -DB_FLOOR) * 100);

        const quiet = db <= DB_FLOOR;
        if (dbRef.current) dbRef.current.textContent = quiet ? '—' : String(Math.round(db));
        if (rowRef.current) {
          rowRef.current.textContent = quiet
            ? '마이크 입력 없음'
            : `마이크 입력 ${Math.round(db)}dB`;
        }

        // 한 번이라도 소리가 들어오면 점검 통과입니다. 이 setState는 스트림당 한 번 돕니다
        if (!heard && meter.silentMs() === 0) {
          heard = true;
          setHeardFor(stream);
        }

        if (silentRef.current) {
          const ms = meter.silentMs();
          silentRef.current.textContent =
            ms > SILENT_WARN_MS ? `${Math.floor(ms / 1000)}초 동안 소리가 들어오지 않습니다` : '';
        }
      };
      raf = requestAnimationFrame(loop);
    })();

    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
      meter?.stop();
    };
  }, [stream]);

  return {
    meterRef,
    dbRef,
    rowRef,
    silentRef,
    statsRef,
    micOk: stream !== null && heardFor === stream,
    audioState: audio && audio.stream === stream ? audio.state : null,
  };
}
