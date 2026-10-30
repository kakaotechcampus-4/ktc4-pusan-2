import { useMutation } from '@tanstack/react-query';
import { createPitch, updatePitch } from '@/shared/api/pitch';
import { isCurrentDraft, useCreateStore } from './createStore';
import { fromPitchSaved, toPitchRequest } from './lib/beAdapter';
import { infoOf, infoReady, type KnownInfo } from './lib/draft';

/** 본문과 고정 버튼이 같은 저장 상태를 사용합니다. 저장 중 입력은 잠급니다. */
export function useSavePitchInfo() {
  return useMutation({
    mutationFn: async () => {
      const state = useCreateStore.getState();
      const form = state.infoEdits ?? infoOf(state.draft);
      if (state.infoSaving || state.slideUpload.status === 'uploading')
        throw new Error('진행 중인 저장이 끝난 뒤 다시 시도해 주세요.');
      if (!form.title.trim() || !infoReady(form))
        throw new Error('발표 제목과 시간 허용범위를 확인해 주세요.');
      const saved: KnownInfo = { ...form, title: form.title.trim() };
      useCreateStore.setState({ infoSaving: true });
      try {
        const body = toPitchRequest(saved);
        const response = state.pitchId
          ? await updatePitch(state.pitchId, body)
          : await createPitch(body);
        if (!isCurrentDraft(state.draftId)) return false;
        useCreateStore.getState().setPitchId(fromPitchSaved(response));
        useCreateStore.getState().setMeta(saved);
        return true;
      } finally {
        if (isCurrentDraft(state.draftId)) useCreateStore.setState({ infoSaving: false });
      }
    },
  });
}
