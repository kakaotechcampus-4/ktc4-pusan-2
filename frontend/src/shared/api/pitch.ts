import { useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { apiRequest, postJson } from './client';
import { pitchesKey, pitchListKey } from './home';
import { prepareKey } from './prepare';
import { takeSurveyKeys } from './takeSurvey';
import { ApiFailure } from './tokenStore';
import type { PitchListResponse } from '@/types/home';
import type { PitchDetail, PitchRequest, PitchResources, PitchSavedResponse } from '@/types/pitch';

/** 피치를 만듭니다. 발표자료 · 대본 · 평가기준은 전부 이 응답의 pitch_id 아래에 올라갑니다 */
export function createPitch(body: PitchRequest) {
  return postJson<PitchSavedResponse>('/api/pitches/add', body);
}

/**
 * 발표정보를 고칩니다 (`PATCH /pitches/update/{id}`). 본문은 만들 때와 같은 `PitchSaveRequestDTO` 이고,
 * BE 는 받은 값으로 덮어씁니다(제목 · 시간 · 허용오차 · 날짜). 화면은 발표정보를 한 번에
 * 저장하므로 다섯 필드를 모두 보냅니다.
 */
export function updatePitch(pitchId: string, body: PitchRequest) {
  return apiRequest<PitchSavedResponse>(`/api/pitches/update/${pitchId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/** 피치의 버전 목록 — 수정 화면이 사이드바를 채울 때 씁니다 */
export function getPitchResources(pitchId: string) {
  return apiRequest<PitchResources>(`/api/pitches/${pitchId}/resources`);
}

/** 허용 오차를 포함한 발표 기본정보 — BE #570 */
export function getPitch(pitchId: string) {
  return apiRequest<PitchDetail>(`/api/pitches/${pitchId}`);
}

/**
 * 피치를 영구히 지웁니다 (`DELETE /pitches/delete/{id}`). 복구 API 는 없습니다.
 * 발표자료 · 대본 · 평가기준 · Take 와 그 리포트가 함께 지워지고, S3 파일은 BE 가 응답 뒤에 지웁니다
 */
export function deletePitch(pitchId: string) {
  return apiRequest<PitchSavedResponse>(`/api/pitches/delete/${encodeURIComponent(pitchId)}`, {
    method: 'DELETE',
  });
}

/** 연습이나 분석이 도는 중이라 BE 가 삭제를 거절했습니다 (409 `PITCH_BUSY`, BE #1055) */
export const isPitchBusy = (error: unknown) =>
  error instanceof ApiFailure && error.status === 409 && error.code === 'PITCH_BUSY';

/**
 * 홈의 피치 삭제. 지워도 되는지는 BE 가 정합니다 — 연습이나 분석이 도는 중이면 409 `PITCH_BUSY` 로
 * 거절합니다(#1057). 그때는 캐시를 그대로 두고 홈 목록만 다시 받아 카드 메뉴(`can_delete`)를 맞춥니다.
 * 서버가 404 면 이미 지워진 것으로 봅니다.
 *
 * 지운 뒤 캐시에서 뺄 Take 는 확인 창이 알던 것(`takeIds`)입니다. 창을 연 뒤 시작한 연습은 `RUNNING` 이라
 * BE 가 409 로 막으므로, 그사이 생길 수 있는 건 다른 탭에서 만든 `READY` Take 뿐이고 이 탭에는 그 캐시가 없습니다
 */
export function useDeletePitch() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ pitchId }: { pitchId: string; takeIds: readonly string[] }) => {
      try {
        await deletePitch(pitchId);
      } catch (error) {
        if (!(error instanceof ApiFailure && error.status === 404)) throw error;
      }
    },
    onSuccess: (_, { pitchId, takeIds }) => forgetPitch(client, pitchId, takeIds),
    onError: (error) => {
      if (isPitchBusy(error))
        client.invalidateQueries({ queryKey: pitchListKey }).catch(() => undefined);
    },
  });
}

/** 지운 피치와 그 Take 를 캐시에서 뺍니다. 목록은 바로 빼고, 서버 값으로 다시 받습니다 */
function forgetPitch(client: QueryClient, pitchId: string, takeIds: readonly string[]) {
  client.setQueryData<PitchListResponse>(
    pitchListKey,
    (old) => old && { pitches: old.pitches.filter((pitch) => pitch.pitch_id !== pitchId) },
  );
  client.invalidateQueries({ queryKey: pitchesKey }).catch(() => undefined);
  for (const queryKey of [
    prepareKey(pitchId),
    ['pitch-edit', pitchId],
    ['presentation', pitchId],
    ['script', pitchId],
    ['takes', 'history', pitchId],
  ])
    client.removeQueries({ queryKey });
  for (const takeId of takeIds) {
    // 리포트 · 확인 목록(['takes', id, …])과 설문 상태
    client.removeQueries({ queryKey: ['takes', takeId] });
    client.removeQueries({ queryKey: takeSurveyKeys.state(takeId) });
  }
  // 모든 피치의 Take 기록과 홈의 남은 설문에도 지운 Take 가 있었을 수 있습니다
  client.invalidateQueries({ queryKey: ['takes', 'history', 'all'] }).catch(() => undefined);
  client.invalidateQueries({ queryKey: takeSurveyKeys.pending }).catch(() => undefined);
}
