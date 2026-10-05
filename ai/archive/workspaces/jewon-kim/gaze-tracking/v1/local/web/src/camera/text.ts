/** Korean wording for the camera view (and the demo page). The engine stays English; the screen speaks Korean. */
import type { ConditionIssue } from '../engine/condition';
import type { GazeIssueType } from '../engine/evidence';
import type { PreconditionReason } from '../engine/preconditions';
import type { GazeDirection, State } from '../engine/types';

export const CHECK_NAME: Record<Exclude<PreconditionReason, 'OK'>, string> = {
  NO_FACE: '얼굴 인식',
  MULTIPLE_FACES: '한 명만 보이기',
  OFF_CENTER: '화면 가운데',
  TOO_FAR: '너무 멀지 않게',
  TOO_CLOSE: '너무 가깝지 않게',
  FACING_AWAY: '정면 바라보기',
  TOO_DARK: '얼굴 밝기',
  BACKLIT: '역광 없음',
  LOW_FPS: '카메라 속도',
};

export const CHECK_FIX: Record<PreconditionReason, string> = {
  OK: '좋아요. 잠시 그대로 계세요.',
  NO_FACE: '카메라 앞에 앉아 얼굴이 보이게 해 주세요.',
  MULTIPLE_FACES: '화면에 한 사람만 보이도록 해 주세요.',
  OFF_CENTER: '얼굴이 화면 가운데에 오도록 자리를 옮겨 주세요.',
  TOO_FAR: '카메라에 조금 더 가까이 와 주세요.',
  TOO_CLOSE: '카메라에서 조금 뒤로 물러나 주세요.',
  FACING_AWAY: '화면을 정면으로 바라봐 주세요.',
  TOO_DARK: '얼굴이 어두워요. 앞쪽에 조명을 켜 주세요.',
  BACKLIT: '뒤쪽이 너무 밝아요. 창문을 가리거나 빛을 마주 보세요.',
  LOW_FPS: '카메라가 느려요. 카메라나 CPU를 쓰는 다른 프로그램을 닫아 주세요.',
};

/** Gauge reject reasons (head-pose engine: no blink / head-moved rules). */
export const GAUGE_REASON: Record<string, string> = {
  NO_FACE: '얼굴이 보이지 않아요',
  LOW_FACE_CONFIDENCE: '얼굴이 일부만 보여요',
  FACE_TOO_SMALL: '얼굴이 너무 작아요 · 가까이 와 주세요',
  OUT_OF_FRAME: '얼굴이 화면 밖으로 나갔어요',
  EYES_CLOSED: '눈을 뜬 채로 바라봐 주세요',
  CROP_FAILED: '얼굴이 잘 보이지 않아요',
  BACKBONE_FAILED: '자세를 읽지 못했어요',
  LOW_GAZE_CONFIDENCE: '얼굴이 화면 가장자리에 걸려 있어요',
  OUTLIER: '한 지점을 계속 바라봐 주세요',
  HEAD_MOVED: '고개를 그대로 두세요',
  BLINK: '잠시 눈을 뜨고 바라봐 주세요',
  LOOK_HIGHER: '렌즈 쪽으로 고개를 조금 더 들어 주세요',
  LOOK_LOWER: '대본 쪽으로 고개를 조금 더 숙여 주세요',
  OFF_TARGET: '과녁 쪽으로 고개를 돌려 주세요',
};

export const ISSUE: Record<ConditionIssue, string> = {
  HEAD_TURNED: '고개가 보정 범위를 벗어남',
  TOO_FAR: '보정 때보다 멀어짐',
  TOO_CLOSE: '보정 때보다 가까워짐',
  OFF_CENTER: '보정 때 자리에서 옆·위아래로 움직임',
  SECOND_FACE: '다른 사람이 보임',
  LOW_VALID_RATIO: '얼굴 인식이 자주 끊김',
  NOISY_TRACKING: '고개 방향 값이 흔들림',
  FACE_LOST: '얼굴이 사라짐',
  FACE_REPLACED: '다른 사람으로 바뀐 것 같음',
  MOVED_TOO_FAR: '처음 위치에서 너무 벗어남 · 측정 불가',
};

/** Why a frame is "판정 보류": the classifier's reasons, then the measurement's. */
export const UNCERTAIN_REASON: Record<string, string> = {
  LOW_CONFIDENCE: '두 영역 사이라 어느 쪽인지 애매해요',
  LOW_MARGIN: '두 영역의 확률이 비슷해 고르지 않았어요',
  HEAD_AWAY: '고개가 보정 범위 밖으로 많이 돌아갔어요',
  NOT_CALIBRATED: '아직 보정하지 않았어요',
  FACE_LOST: '얼굴이 화면에서 사라졌어요',
  FACE_REPLACED: '다른 사람이 측정되고 있는 것 같아요',
  MOVED_TOO_FAR: '처음 위치에서 너무 벗어나 측정할 수 없어요',
};

export const STATE_NAME: Record<State, string> = {
  CAMERA: '청중 (카메라)',
  SCREEN: '화면',
  BOTTOM: '대본',
  OTHER: '다른 곳',
  UNCERTAIN: '판정 보류',
};

/** Where an OTHER look went, from the presenter's side (their own right and left). */
export const DIRECTION_NAME: Record<GazeDirection, string> = {
  RIGHT: '오른쪽',
  UP_RIGHT: '오른쪽 위',
  UP: '위',
  UP_LEFT: '왼쪽 위',
  LEFT: '왼쪽',
  DOWN_LEFT: '왼쪽 아래',
  DOWN: '아래',
  DOWN_RIGHT: '오른쪽 아래',
};

/** Arrows for the mirrored (selfie) preview, where the presenter's right is on screen right. */
export const DIRECTION_ARROW: Record<GazeDirection, string> = {
  RIGHT: '→',
  UP_RIGHT: '↗',
  UP: '↑',
  UP_LEFT: '↖',
  LEFT: '←',
  DOWN_LEFT: '↙',
  DOWN: '↓',
  DOWN_RIGHT: '↘',
};

/** Coach-input issues (the agents read the English `issue_type`). */
export const GAZE_ISSUE_NAME: Record<GazeIssueType, string> = {
  GAZE_ON_SCRIPT: '대본을 오래 보고 있어요',
  GAZE_ON_SCREEN: '화면을 오래 보고 있어요',
  GAZE_AWAY: '다른 곳을 오래 보고 있어요',
  GAZE_LOW_EYE_CONTACT: '청중을 보는 시간이 적어요',
  GAZE_UNMEASURABLE: '시선을 잴 수 없어요',
};

/** Short class names for compact lists. */
export const STATE_SHORT: Record<State, string> = {
  CAMERA: '청중',
  SCREEN: '화면',
  BOTTOM: '대본',
  OTHER: '다른 곳',
  UNCERTAIN: '판정 보류',
};

export const ZONE_NAME: Record<'CAMERA' | 'BOTTOM' | 'UNCERTAIN', string> = {
  CAMERA: '청중',
  BOTTOM: '화면·대본',
  UNCERTAIN: '판정 불가',
};

export const PLACEMENT_NAME: Record<string, string> = {
  TOP: '화면 위 가운데',
  BOTTOM: '화면 아래',
  SIDE_LEFT: '화면 왼쪽',
  SIDE_RIGHT: '화면 오른쪽',
  INCONCLUSIVE: '판정 불가',
};

export const PLACEMENT_HINT: Record<string, string> = {
  TOP: '카메라가 화면 위 가운데에 있어요.',
  BOTTOM:
    '카메라가 화면 아래에 있는 것 같아요. 위·아래 판정이 뒤바뀌니 화면 위 가운데로 옮겨 주세요.',
  SIDE_LEFT: '카메라가 화면 왼쪽에 있는 것 같아요. 화면 위 가운데로 옮겨 주세요.',
  SIDE_RIGHT: '카메라가 화면 오른쪽에 있는 것 같아요. 화면 위 가운데로 옮겨 주세요.',
  TARGETS_NOT_SEPARATED:
    '렌즈와 화면 가운데를 볼 때 고개 자세가 거의 같았어요. 카메라 위치는 판정하지 않고 넘어갑니다.',
  DISPLACEMENT_TOO_SMALL:
    '렌즈와 화면 가운데의 방향이 거의 같아요. 카메라 위치는 판정하지 않고 넘어갑니다.',
  AMBIGUOUS_AXIS: '카메라가 대각선으로 치우쳐 있어 위치를 판정하지 못했어요.',
  NOT_ENOUGH_SAMPLES: '쓸 수 있는 프레임이 부족했어요.',
};

export const CALIBRATION_HINT: Record<string, string> = {
  NOT_ENOUGH_SAMPLES: '쓸 수 있는 프레임이 부족해요. 얼굴이 잘 보이게 하고 다시 해 주세요.',
  DEGENERATE_FEATURES:
    '세 지점을 볼 때 자세가 전혀 바뀌지 않았어요. 각 지점을 확실히 바라봐 주세요.',
  CLASS_NOT_SEPARABLE:
    '렌즈를 볼 때와 대본을 볼 때가 거의 같게 측정됐어요. 대본 쪽을 더 확실히 바라봐 주세요.',
  CENTROIDS_TOO_CLOSE: '두 지점이 너무 가깝게 측정됐어요. 각 지점을 확실히 바라봐 주세요.',
  ANCHOR_AMBIGUOUS: '한 지점이 주변과 구분되지 않아요. 조금 가까이 앉아 다시 해 주세요.',
  LOW_LOO_ACCURACY: '지점별 측정값이 서로 섞여 있어요. 각 지점을 한동안 바라봐 주세요.',
  SCREEN_MERGED:
    '렌즈와 화면 가운데가 거의 같게 측정되어, 화면을 보는 것도 청중(카메라)으로 판정해요.',
  INVERTED_PITCH:
    '지점들의 위아래 순서가 뒤집혀 측정됐어요. 카메라가 화면 위에 있는지 확인해 주세요.',
};

/** Why a frame of the head circle lit nothing (sweep reasons, then preprocess reasons). */
export const SWEEP_REASON: Record<string, string> = {
  TOO_FAST: '조금 더 천천히 돌려 주세요',
  NO_FACE: '얼굴이 원을 벗어났어요 · 조금 덜 돌려 주세요',
  NO_HEAD_POSE: '얼굴 방향을 읽지 못했어요 · 조금 덜 돌려 주세요',
  OUT_OF_FRAME: '얼굴이 화면 밖으로 나갔어요 · 조금 덜 돌려 주세요',
  LOW_FACE_CONFIDENCE: '얼굴이 일부만 보여요 · 조금 덜 돌려 주세요',
  FACE_TOO_SMALL: '얼굴이 너무 작아요 · 가까이 와 주세요',
  EYES_CLOSED: '눈을 뜬 채로 돌려 주세요',
};

export const SWEEP_TEXT = {
  title: '천천히 고개를 돌려 원을 채워 주세요',
  sub: '코끝으로 원을 그리듯 한 바퀴 돌려 주세요',
  bigger: '고개를 조금 더 크게 돌려 주세요',
  done: '얼굴 확인 완료',
  doneSub: '정면 기준을 쟀어요 · 이제 세 지점으로 확인하고 맞출게요',
  partial: '일부 방향은 확인하지 못했어요',
  skip: '고개를 돌리기 어렵다면 건너뛰기',
} as const;

export const CUE_TEXT = {
  CAMERA: { title: '카메라 렌즈를 바라봐 주세요', sub: '편한 자세로 렌즈를 봐 주세요' },
  SCREEN: {
    title: '화면 가운데의 내 얼굴을 바라봐 주세요',
    sub: '평소 화면을 볼 때처럼 바라봐 주세요',
  },
  BOTTOM: { title: '대본 자리를 바라봐 주세요', sub: '발표할 때 대본을 읽듯이 바라봐 주세요' },
} as const;
