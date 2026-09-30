import { useEffect, useRef, useState } from 'react';
import { createLevelMeter, openMeasureStream, type LevelMeter, type MeasureStream } from './level';

/** 화면에 쓰는 dB 바닥. 이보다 작으면 '입력 없음'으로 봅니다 */
const DB_FLOOR = -60;

/** 무음이 이만큼 이어지면 권한은 있는데 입력이 없는 상황입니다 (명세 8-3) */
const SILENT_WARN_MS = 8_000;

/**
 * 화면에 보여 줄 때 더하는 값. dBFS(-60~0)를 사람이 아는 dB(대화 60dB 같은)처럼 보이게 합니다.
 *
 * ★ **추정값입니다.** 마이크 감도를 모르므로 기기마다 ±10~15dB 틀릴 수 있습니다.
 *   보통 노트북 마이크에서 평소 말소리가 -30dBFS 안팎이고, 그게 60dB 쯤으로 보이게
 *   잡은 값입니다. 휴대폰 소음계 앱과 나란히 재 보며 맞출 값이지 근거가 있는 숫자가
 *   아닙니다 — 기기별 보정은 아직 정하지 않았습니다.
 *
 * **보여 주기에만 씁니다.** 코치 규칙(LOW_DB)은 여전히 dBFS 로 판정합니다 —
 * 추정값으로 판정하면 마이크에 따라 같은 목소리가 통과하기도 하고 떨어지기도 합니다.
 */
const DISPLAY_OFFSET_DB = 90;

function toDisplayDb(dbfs: number): number {
  return Math.round(dbfs + DISPLAY_OFFSET_DB);
}

/** 화면과 코치 규칙이 쓰는 범위로 자릅니다 */
function clampDb(db: number): number {
  return Math.max(DB_FLOOR, Math.min(0, db));
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
  /** 계량기 옆 숫자 (약 64). 추정 dB 입니다 — DISPLAY_OFFSET_DB */
  const dbRef = useRef<HTMLSpanElement>(null);
  /** 점검 항목 줄 전체 문구 ('마이크 입력 약 64dB') */
  const rowRef = useRef<HTMLSpanElement>(null);
  const silentRef = useRef<HTMLSpanElement>(null);

  /**
   * 지금 값. **코치 규칙이 읽는 창구입니다.**
   * 상태가 아니라 ref 인 이유는 초당 수십 번 바뀌기 때문입니다 —
   * 규칙 쪽은 1초에 한 번 들여다보기만 하면 됩니다.
   *
   * `db` 는 A-가중 · Fast 가중 dBFS 입니다. `speechLeqDb` 는 말한 구간만 모은
   * 평균이라 침묵이 섞이지 않습니다 — 리포트에 목소리 크기를 쓸 때는 이쪽입니다.
   */
  const statsRef = useRef<{
    db: number;
    silentMs: number;
    speechLeqDb: number | null;
    speechMs: number;
  }>({ db: -60, silentMs: 0, speechLeqDb: null, speechMs: 0 });

  const [heardFor, setHeardFor] = useState<MediaStream | null>(null);
  const [audio, setAudio] = useState<{ stream: MediaStream; state: AudioContextState } | null>(
    null,
  );
  /**
   * 계량기를 못 띄운 스트림. 이게 있으면 `micOk` 가 false 인 이유가
   * "소리가 안 들어옴" 이 아니라 **"재보지도 못함"** 입니다 — 사용자가 할 일이 다릅니다.
   */
  const [failedFor, setFailedFor] = useState<MediaStream | null>(null);

  useEffect(() => {
    if (!stream) return;

    let raf = 0;
    let cancelled = false;
    let meter: LevelMeter | null = null;
    let measure: MeasureStream | null = null;
    let heard = false;

    (async () => {
      // 크기는 가공 없는 트랙으로 잽니다. 못 받으면 녹음용 스트림으로 갑니다 (level.ts)
      measure = await openMeasureStream(stream);
      if (cancelled) {
        measure?.stream.getTracks().forEach((t) => t.stop());
        return;
      }

      // 제스처 뒤에 만듭니다 — getUserMedia가 이미 통한 시점이라 resume()이 먹습니다
      meter = await createLevelMeter(measure?.stream ?? stream);
      if (cancelled) {
        meter?.stop();
        return;
      }
      if (meter) setAudio({ stream, state: meter.state });

      const loop = () => {
        raf = requestAnimationFrame(loop);
        if (!meter) return;

        // read() 가 먼저입니다 — 무음 판정과 크기가 여기서 같이 갱신됩니다
        meter.read();
        const db = clampDb(meter.levelDb());
        const speech = meter.speech();
        statsRef.current.db = db;
        statsRef.current.silentMs = meter.silentMs();
        statsRef.current.speechLeqDb = speech.leqDb;
        statsRef.current.speechMs = speech.ms;
        paint(meterRef.current, ((db - DB_FLOOR) / -DB_FLOOR) * 100);

        const quiet = db <= DB_FLOOR;
        if (dbRef.current) dbRef.current.textContent = quiet ? '—' : String(toDisplayDb(db));
        if (rowRef.current) {
          rowRef.current.textContent = quiet
            ? '마이크 입력 없음'
            : `마이크 입력 약 ${toDisplayDb(db)}dB`;
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
      // ★ 전체를 `.catch(() => undefined)` 로 감싸면 안 됩니다 (CLAUDE.md 9번).
      //   createLevelMeter 가 실패하면 아래 루프가 아예 시작되지 않고, 화면에는
      //   '마이크 입력 없음' 만 영원히 남습니다 — 원인은 어디에도 안 보입니다.
    })().catch((err: unknown) => {
      console.error('[mic] 음량 계량기를 시작하지 못했습니다', err);
      if (!cancelled) setFailedFor(stream);
    });

    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
      meter?.stop();
      // ★ 따로 받은 트랙도 꼭 놓습니다. 안 그러면 화면을 떠나도 마이크 표시등이 남습니다
      measure?.stream.getTracks().forEach((t) => t.stop());
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
    /** 계량기 자체가 못 떴을 때의 문구. null 이면 정상입니다 */
    meterError:
      stream !== null && failedFor === stream
        ? '마이크 음량을 측정하지 못했어요. 장치를 다시 선택해 주세요.'
        : null,
  };
}
