import { fetchPitchList } from '@/shared/api/home';
import { createPitch, getPitchResources } from '@/shared/api/pitch';
import { uploadPresentation } from '@/shared/api/presentation';
import { createScript, getScript, reparseScript } from '@/shared/api/script';
import { ApiFailure } from '@/shared/api/tokenStore';
import type { ScriptDetail } from '@/types/script';
import { FirstTakeError } from './firstTakeError';
import { createFirstTakePdf } from './firstTakePdf';
import {
  FIRST_TAKE_SCRIPT,
  FIRST_TAKE_SLIDES,
  FIRST_TAKE_TIME_SEC,
  FIRST_TAKE_TITLE,
} from './firstTakeMaterials';

type Progress = (message: string) => void;
const POLL_MS = 1000;
const PARSE_TIMEOUT_MS = 100_000;

/** 조회에서 복구 불가능한 4xx는 즉시 사용자에게 알립니다 (408/429 제외). */
function isFinalFailure(error: unknown): boolean {
  return (
    error instanceof ApiFailure &&
    error.status >= 400 &&
    error.status < 500 &&
    error.status !== 408 &&
    error.status !== 429
  );
}

function ensureNotAborted(signal?: AbortSignal) {
  if (signal?.aborted) throw new DOMException('샘플 준비가 중단되었어요.', 'AbortError');
}

function pauseUntilNextPoll(signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('샘플 준비가 중단되었어요.', 'AbortError'));
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException('샘플 준비가 중단되었어요.', 'AbortError'));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, POLL_MS);
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

function validMapping(result: ScriptDetail): boolean {
  return (
    result.segmented === true &&
    result.slides.length === FIRST_TAKE_SLIDES.length &&
    result.slides.every((slide, index) => slide.slide_number === index + 1)
  );
}

async function waitForScript(
  pitchId: string,
  scriptVersionId: string,
  progress: Progress,
  signal?: AbortSignal,
) {
  const start = Date.now();
  let retried = false;

  while (Date.now() - start < PARSE_TIMEOUT_MS) {
    ensureNotAborted(signal);
    let detail: ScriptDetail;
    try {
      detail = await getScript(pitchId, scriptVersionId);
    } catch (error) {
      ensureNotAborted(signal);
      if (isFinalFailure(error)) throw error;
      // 일시적인 네트워크/서버 실패는 다른 대본 매핑 화면과 같이 재조회합니다.
      await pauseUntilNextPoll(signal);
      continue;
    }
    ensureNotAborted(signal);
    if (detail.parse_status === 'DONE') {
      if (!validMapping(detail)) {
        throw new FirstTakeError(
          '샘플 대본이 5개 슬라이드에 맞게 나뉘지 않았어요. 내 피치에서 대본을 확인해 주세요.',
        );
      }
      return;
    }

    if (detail.parse_status === 'FAILED') {
      if (retried)
        throw new FirstTakeError(
          '샘플 대본 분석에 실패했어요. 내 피치에서 대본을 확인해 주세요.',
        );
      progress('대본 분석을 다시 요청하고 있어요…');
      try {
        await reparseScript(pitchId, scriptVersionId);
      } catch (error) {
        // 다른 탭이 분석을 시작했거나 이미 완료한 경우에는 상태 조회를 계속합니다.
        if (
          !(error instanceof ApiFailure) ||
          error.status !== 409 ||
          !['SCRIPT_PARSE_IN_PROGRESS', 'SCRIPT_ALREADY_PARSED'].includes(error.code)
        ) {
          throw error;
        }
      }
      retried = true;
    }

    await pauseUntilNextPoll(signal);
  }
  throw new FirstTakeError(
    '대본 분석이 오래 걸리고 있어요. 다시 시도하면 기존 작업을 이어서 확인합니다.',
  );
}

/**
 * 샘플 피치를 실제 사용자 계정에 등록하고 기존 장치 점검 화면으로 넘길 준비를 합니다.
 * Take는 여기서 생성하지 않습니다. 기존 DeviceCheckPage의 시작 버튼에서만 만들어집니다.
 *
 * FE에서 제목으로 기존 샘플을 찾는 MVP입니다. 서로 다른 탭에서 최초 클릭을 동시에
 * 진행하는 완전한 멱등성 및 is_demo 저장은 추후 BE API가 필요합니다 (#931).
 */
export async function ensureFirstTakePitch(
  progress: Progress,
  signal?: AbortSignal,
): Promise<string> {
  ensureNotAborted(signal);
  progress('기존 샘플 발표를 확인하고 있어요…');
  const list = await fetchPitchList();
  ensureNotAborted(signal);
  const matches = list.pitches.filter((pitch) => pitch.pitch_title === FIRST_TAKE_TITLE);

  // 복수 탭의 동시 생성으로 중복된 경우에도 첫 샘플을 이어서 사용합니다.
  // 중복 자체의 방지는 서버의 멱등 API(#1109)가 필요합니다.
  let pitchId = matches[0]?.pitch_id;
  let preparedPdf: File | undefined;
  if (!pitchId) {
    // 브라우저에서 PDF를 만들 수 없으면 빈 피치가 생기지 않도록 먼저 검증합니다.
    progress('튜토리얼 발표자료 5장을 준비하고 있어요…');
    preparedPdf = await createFirstTakePdf();
    ensureNotAborted(signal);
    progress('첫 미션을 등록하고 있어요…');
    const created = await createPitch({
      title: FIRST_TAKE_TITLE,
      time_limit_sec: FIRST_TAKE_TIME_SEC,
      lower_deviation: 30,
      upper_deviation: 60,
      presentation_date: null,
    });
    pitchId = created.pitch_id;
    ensureNotAborted(signal);
  }

  // 각 저장 단계가 중단되더라도 다음 방문에서 서버에 남은 버전을 이어 씁니다.
  const resources = await getPitchResources(pitchId);
  ensureNotAborted(signal);
  const presentation = [...resources.presentation_versions].sort(
    (a, b) => b.version - a.version,
  )[0];
  const script = [...resources.script_versions].sort((a, b) => b.version - a.version)[0];

  if (!presentation) {
    progress('튜토리얼 발표자료 5장을 준비하고 있어요…');
    const file = preparedPdf ?? (await createFirstTakePdf());
    ensureNotAborted(signal);
    const uploaded = await uploadPresentation(pitchId, file);
    ensureNotAborted(signal);
    if (
      uploaded.presentation.page_count !== undefined &&
      uploaded.presentation.page_count !== FIRST_TAKE_SLIDES.length
    ) {
      throw new FirstTakeError('업로드된 PDF 장수가 샘플 자료와 달라요. 내 피치에서 확인해 주세요.');
    }
  } else if (
    presentation.page_count != null &&
    presentation.page_count !== FIRST_TAKE_SLIDES.length
  ) {
    throw new FirstTakeError('기존 샘플 PDF가 5장이 아니에요. 내 피치에서 자료를 확인해 주세요.');
  }

  let scriptId = script?.id;
  if (!scriptId) {
    progress('슬라이드에 맞는 대본을 등록하고 있어요…');
    const created = await createScript(pitchId, FIRST_TAKE_SCRIPT);
    scriptId = created.script_version_id;
    ensureNotAborted(signal);
  }

  progress('슬라이드별 대본을 연결하고 있어요…');
  await waitForScript(pitchId, scriptId, progress, signal);
  ensureNotAborted(signal);
  progress('준비가 완료되었어요. 장치 점검으로 이동합니다.');
  return pitchId;
}
