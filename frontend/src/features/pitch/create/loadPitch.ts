import { fetchPitchList } from '@/shared/api/home';
import { getPitchResources } from '@/shared/api/pitch';
import { getPresentation } from '@/shared/api/presentation';
import { getScript } from '@/shared/api/script';
import { getEvaluation } from '@/shared/api/standards';
import { openPdf } from '@/shared/lib/pdf';
import type { VersionRef } from '@/types/pitch';
import { fromEvaluation, fromPitchListItem, fromScriptDetail } from './lib/beAdapter';
import type {
  CriteriaVersion,
  PitchDraft,
  ScriptParse,
  ScriptVersion,
  SlideVersion,
} from './lib/draft';

/**
 * 수정 화면 — 서버에 있는 피치를 피치 생성 화면의 초안 모양으로 읽어 옵니다.
 *
 *   발표정보   GET /pitches (목록에서 찾기 — 단건 API 가 없습니다. 허용오차는 모름)
 *   버전 목록  GET /pitches/{id}/resources
 *   슬라이드   GET …/presentations/{id} → PDF 를 열어 장수를 셉니다 (업로드 때와 같음)
 *   대본       GET …/scripts/{id}
 *   평가기준   GET …/evaluations/{번호}
 *
 * **불러온 버전은 모두 저장된 것입니다.** 서버에 있는 것은 이미 올린 것이라, 고치면 생성 화면과
 * 같은 규칙으로 새 버전이 생깁니다. 사이드바 번호는 서버의 번호를 그대로 씁니다.
 *
 * 하나라도 못 읽으면 전체를 실패로 돌립니다. 몇 버전만 빠진 사이드바는 번호가 비어
 * 무엇이 없는지 알 수 없고, 빠진 채로 새 버전을 만들면 번호가 서버와 어긋납니다.
 */

/** 목록에 이 피치가 없습니다 — 지워졌거나 남의 피치입니다 */
export class PitchNotFound extends Error {
  constructor(pitchId: string) {
    super(`피치를 찾을 수 없습니다: ${pitchId}`);
    this.name = 'PitchNotFound';
  }
}

const byVersion = (a: VersionRef, b: VersionRef) => a.version - b.version;

async function loadSlide(pitchId: string, ref: VersionRef): Promise<SlideVersion> {
  // 부를 때마다 새 presigned URL 을 줍니다. 장수는 서버가 주지 않아 PDF 를 열어 셉니다
  const detail = await getPresentation(pitchId, ref.id);
  const pageCount = (await openPdf(detail.file_url)).numPages;
  return {
    version: ref.version,
    pageCount,
    fileUrl: detail.file_url,
    presentationVersionId: ref.id,
  };
}

/** 나누기 상태 → 화면 상태. 나누는 중이던 대본은 여기서 기다리지 않습니다 */
function parseOf(progress: ReturnType<typeof fromScriptDetail>): ScriptParse {
  if (progress.status === 'failed')
    return { status: 'failed', message: '대본을 슬라이드로 나누지 못했어요. 다시 시도해 주세요.' };
  if (progress.status === 'pending')
    return { status: 'failed', message: '나누는 중이던 대본이에요. 잠시 뒤 다시 열어 주세요.' };
  return { status: 'idle' };
}

/**
 * 대본 한 버전. 서버는 "어느 슬라이드에 맞춘 대본인가"를 따로 두지 않아(Take 에만 남습니다)
 * 장수가 같은 슬라이드 중 가장 최근 것에 연결합니다. 맞는 것이 없으면 최신 슬라이드에 둡니다 —
 * 그러면 매핑 확인 화면이 장수가 다르다고 알려 줍니다.
 */
async function loadScript(
  pitchId: string,
  ref: VersionRef,
  slides: SlideVersion[],
): Promise<ScriptVersion> {
  const detail = await getScript(pitchId, ref.id);
  const progress = fromScriptDetail(detail);
  const blocks = progress.status === 'done' ? progress.blocks : null;
  const matching = slides.findLast((s) => blocks !== null && s.pageCount === blocks.length);
  return {
    version: ref.version,
    text: detail.original_content ?? blocks?.join('\n\n') ?? '',
    remote: { id: ref.id, version: ref.version },
    parse: parseOf(progress),
    segmented: progress.status === 'done' ? progress.segmented : null,
    slideVersion: (matching ?? slides.at(-1))?.version ?? null,
    blocks,
    saved: true,
  };
}

async function loadCriteria(pitchId: string, ref: VersionRef): Promise<CriteriaVersion> {
  const standards = fromEvaluation(await getEvaluation(pitchId, ref.version));
  return {
    version: ref.version,
    items: standards.map((text) => ({ id: crypto.randomUUID(), text })),
    saved: true,
  };
}

export async function loadPitch(pitchId: string): Promise<PitchDraft> {
  const [list, resources] = await Promise.all([fetchPitchList(), getPitchResources(pitchId)]);
  const item = list.pitches.find((p) => p.pitch_id === pitchId);
  if (!item) throw new PitchNotFound(pitchId);

  const slides = await Promise.all(
    [...resources.presentation_versions].sort(byVersion).map((ref) => loadSlide(pitchId, ref)),
  );
  const [scripts, criteria] = await Promise.all([
    Promise.all(
      [...resources.script_versions].sort(byVersion).map((ref) => loadScript(pitchId, ref, slides)),
    ),
    Promise.all(
      [...resources.evaluation_versions].sort(byVersion).map((ref) => loadCriteria(pitchId, ref)),
    ),
  ]);

  return { ...fromPitchListItem(item), slides, scripts, criteria };
}
