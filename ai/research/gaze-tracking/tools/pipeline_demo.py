"""Staged walkthrough of the vision pipeline -- the thing to run first.

Three stages, in the order the model actually needs them:

    1. CHECK   set-up check            (one person, centred, distance, light, fps)
    2. CALIB   calibration             (look naturally: lens -> screen centre -> video bottom;
                                        head held still only with an eye backbone)
    3. LIVE    smoothed GAZE_STATE     (CAMERA / SCREEN / BOTTOM / OTHER) + reliability

The camera position is read off the first two calibration looks and checked
before the third.  In strict mode (``preconditions.strict``, the default) a
failed set-up check, an unsupported camera position or a failed calibration
blocks the next step until it is fixed (``C`` forces past it and is logged);
``--advisory`` only reports.

Run it::

    uv run python tools/pipeline_demo.py                 # webcam
    uv run python tools/pipeline_demo.py --video x.mp4   # recorded
    uv run python tools/pipeline_demo.py --video x.mp4 --no-window --advisory

Keys: SPACE continue, R retry, C continue anyway, A re-anchor (live),
D debug overlay, M mesh, S dump events to JSONL, Q quit.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import cv2
import numpy as np

from gaze_lab.config import REPORTS_DIR, VisionConfig, load_config
from gaze_lab.preprocess.sampler import FrameSampler
from gaze_lab.runtime.placement import TARGET_CAMERA
from gaze_lab.runtime.policy import gate_calibration, gate_placement
from gaze_lab.runtime.session import VisionSession
from gaze_lab.runtime.sweep import summary_line
from gaze_lab.schemas import (
    INVERTED_PITCH_HINT_PREFIX,
    CalibrationFailReason,
    GazeState,
)

# BGR, because everything below draws onto an OpenCV frame.
_WHITE = (255, 255, 255)
_GREY = (170, 170, 170)
_DARK = (32, 32, 32)
_GREEN = (90, 220, 120)
_AMBER = (60, 180, 250)
_RED = (80, 80, 240)
_BLUE = (240, 180, 90)

_PURPLE = (200, 120, 200)

_STATE_COLOURS = {
    GazeState.CAMERA.value: _GREEN,
    GazeState.SCREEN.value: _BLUE,
    GazeState.BOTTOM.value: _AMBER,
    GazeState.OTHER.value: _PURPLE,
    GazeState.UNCERTAIN.value: _GREY,
}


# --------------------------------------------------------------------------
# Korean-capable text drawing
# --------------------------------------------------------------------------

_FONT_CANDIDATES = (
    "C:/Windows/Fonts/malgun.ttf",
    "C:/Windows/Fonts/malgunbd.ttf",
    "C:/Windows/Fonts/gulim.ttc",
)


class TextLayer:
    """Batched text drawing that can render Hangul.

    ``cv2.putText`` cannot draw Hangul at all, and converting the frame to PIL
    per string would cost more than the inference does.  So calls are queued and
    flushed in one conversion per frame.  Without Pillow or a CJK font we fall
    back to OpenCV and the romanised labels each caller supplies.
    """

    def __init__(self) -> None:
        self._queue: List[Tuple[Tuple[int, int], str, int, Tuple[int, int, int]]] = []
        self._fonts: Dict[int, object] = {}
        self._font_path: Optional[str] = None
        self._image_mod = None
        self._draw_mod = None
        self._font_mod = None
        try:
            from PIL import Image, ImageDraw, ImageFont

            for candidate in _FONT_CANDIDATES:
                if Path(candidate).exists():
                    self._font_path = candidate
                    break
            if self._font_path:
                self._image_mod, self._draw_mod, self._font_mod = Image, ImageDraw, ImageFont
        except ImportError:
            pass

    @property
    def unicode_ready(self) -> bool:
        return self._font_path is not None

    def put(self, frame_xy: Tuple[int, int], text: str, size: int = 20, colour=_WHITE) -> None:
        self._queue.append((frame_xy, text, size, colour))

    def _font(self, size: int):
        if size not in self._fonts:
            self._fonts[size] = self._font_mod.truetype(self._font_path, size)
        return self._fonts[size]

    def flush(self, frame: np.ndarray) -> np.ndarray:
        """Draw everything queued and return the frame (in place when possible)."""
        if not self._queue:
            return frame
        if not self.unicode_ready:
            for (x, y), text, size, colour in self._queue:
                cv2.putText(
                    frame, text, (x, y + size), cv2.FONT_HERSHEY_SIMPLEX,
                    size / 32.0, colour, max(1, size // 14), cv2.LINE_AA,
                )
            self._queue.clear()
            return frame

        pil = self._image_mod.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        draw = self._draw_mod.Draw(pil)
        for (x, y), text, size, colour in self._queue:
            # Colours arrive BGR because the rest of the file is OpenCV.
            draw.text((x, y), text, font=self._font(size), fill=(colour[2], colour[1], colour[0]))
        self._queue.clear()
        return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


def text_size(text: str, size: int) -> int:
    """Rough pixel width, good enough for centring and card sizing.

    Hangul is full-width, so treating every character as ~0.6 em -- fine for
    ASCII -- underestimates a Korean string by nearly half and pushes headlines
    off the panel they were measured for.
    """
    width = 0.0
    for ch in text:
        width += size * (1.0 if _is_wide(ch) else 0.55)
    return int(width)


def _is_wide(ch: str) -> bool:
    code = ord(ch)
    return (
        0x1100 <= code <= 0x11FF      # Hangul Jamo
        or 0x3000 <= code <= 0x9FFF   # CJK punctuation, Kana, CJK ideographs
        or 0xAC00 <= code <= 0xD7A3   # Hangul syllables
        or 0xFF00 <= code <= 0xFF60   # full-width forms
    )


# --------------------------------------------------------------------------
# Face overlay
# --------------------------------------------------------------------------


def _connection_pairs(name: str) -> Tuple[Tuple[int, int], ...]:
    from mediapipe.tasks.python import vision as mpv

    return tuple(
        (c.start, c.end) for c in getattr(mpv.FaceLandmarksConnections, name)
    )


class FaceOverlay:
    """Draw the tracked face with landmark-anchored full-face axes.

    Everything is drawn from the same ``FrameObservation`` the model scored, so
    what is on screen is the evidence behind the decision rather than a second,
    prettier estimate.  The old ray from between the eyes was easy to read as a
    precise gaze pointer; the crosshair instead spans forehead-to-chin and
    cheek-to-cheek, following the detected face's size and roll.
    Connection sets are pulled from MediaPipe once, lazily, so importing this
    module stays cheap for the headless paths.
    """

    #: Contours (face oval, brows, eyes, nose, lips) -- 124 segments, not the
    #: 2556-segment tesselation, which is unreadable at webcam resolution.
    _SETS = ("FACE_LANDMARKS_CONTOURS",)
    _IRIS_SETS = ("FACE_LANDMARKS_LEFT_IRIS", "FACE_LANDMARKS_RIGHT_IRIS")

    def __init__(self) -> None:
        self._contours: Optional[Tuple[Tuple[int, int], ...]] = None
        self._irises: Optional[Tuple[Tuple[int, int], ...]] = None

    def _load(self) -> None:
        if self._contours is None:
            self._contours = sum((_connection_pairs(n) for n in self._SETS), ())
            self._irises = sum((_connection_pairs(n) for n in self._IRIS_SETS), ())

    def draw(self, view: np.ndarray, obs, *, mesh: bool = True,
             mirrored: bool = True) -> None:
        if obs is None or obs.landmarks is None:
            return
        self._load()
        h, w = view.shape[:2]
        pts = obs.landmarks[:, :2] * np.asarray([w, h], dtype=np.float64)
        if mirrored:
            pts = pts.copy()
            pts[:, 0] = (w - 1) - pts[:, 0]
        pts = pts.astype(np.int32)

        colour = _GREEN if obs.face_valid else _RED
        if mesh:
            for a, b in self._contours:
                if a < len(pts) and b < len(pts):
                    cv2.line(view, tuple(pts[a]), tuple(pts[b]), (130, 130, 130), 1, cv2.LINE_AA)
            for a, b in self._irises:
                if a < len(pts) and b < len(pts):
                    cv2.line(view, tuple(pts[a]), tuple(pts[b]), colour, 1, cv2.LINE_AA)

        # Iris centres: landmark 468 is the image-left eye, 473 the image-right
        # one (verified against MediaPipe's own LEFT/RIGHT_IRIS index blocks).
        for idx in (468, 473):
            if idx < len(pts):
                cv2.circle(view, tuple(pts[idx]), 3, colour, -1, cv2.LINE_AA)

        if obs.face_bbox is not None:
            x, y, bw, bh = obs.face_bbox
            x0 = (w - x - bw) if mirrored else x
            cv2.rectangle(view, (x0, y), (x0 + bw, y + bh), colour, 1)

        self._draw_face_crosshair(view, pts, colour)

    @staticmethod
    def _draw_face_crosshair(view, pts, colour) -> None:
        """Draw full-face axes without implying a precise gaze landing point."""
        # These MediaPipe anchors remain stable when the irises move.  Using the
        # landmark segments directly also makes the axes follow head roll; adding
        # the estimated pose rotation again would rotate them twice.
        if len(pts) <= 454:
            return

        top, chin = np.asarray(pts[10], dtype=np.float64), np.asarray(pts[152], dtype=np.float64)
        cheek_a = np.asarray(pts[234], dtype=np.float64)
        cheek_b = np.asarray(pts[454], dtype=np.float64)
        anchors = np.stack((top, chin, cheek_a, cheek_b))
        if not np.isfinite(anchors).all():
            return

        vertical = chin - top
        horizontal = cheek_b - cheek_a
        face_h = float(np.linalg.norm(vertical))
        face_w = float(np.linalg.norm(horizontal))
        if face_h < 2.0 or face_w < 2.0:
            return

        # p + t*r == q + u*s.  The true segment intersection is preferable to
        # an axis-aligned midpoint on yawed faces.  Fall back safely for corrupt
        # or near-parallel landmark geometry.
        denominator = float(vertical[0] * horizontal[1] - vertical[1] * horizontal[0])
        centre = None
        if abs(denominator) > 1e-6:
            delta = cheek_a - top
            t = float((delta[0] * horizontal[1] - delta[1] * horizontal[0]) / denominator)
            u = float((delta[0] * vertical[1] - delta[1] * vertical[0]) / denominator)
            if -0.02 <= t <= 1.02 and -0.02 <= u <= 1.02:
                centre = top + t * vertical
        if centre is None or not np.isfinite(centre).all():
            centre = (top + chin) * 0.5

        gap = int(np.clip(round(min(face_w, face_h) * 0.025), 3, 7))
        v_unit = vertical / face_h
        h_unit = horizontal / face_w

        def pixel(point):
            rounded = np.rint(point).astype(int)
            return int(rounded[0]), int(rounded[1])

        segments = (
            (pixel(top), pixel(centre - v_unit * gap)),
            (pixel(centre + v_unit * gap), pixel(chin)),
            (pixel(cheek_a), pixel(centre - h_unit * gap)),
            (pixel(centre + h_unit * gap), pixel(cheek_b)),
        )
        for line_colour, thickness in ((_WHITE, 4), (colour, 2)):
            for start, end in segments:
                cv2.line(view, start, end, line_colour, thickness, cv2.LINE_AA)
        cx, cy = pixel(centre)
        cv2.circle(view, (cx, cy), gap + 1, _WHITE, 2, cv2.LINE_AA)
        cv2.circle(view, (cx, cy), gap + 1, colour, 1, cv2.LINE_AA)


# --------------------------------------------------------------------------
# Stage machine
# --------------------------------------------------------------------------


class Stage(str, Enum):
    INTRO = "INTRO"
    CHECK = "CHECK"
    SWEEP = "SWEEP"
    CALIB_CAMERA = "CALIB_CAMERA"
    CALIB_SCREEN = "CALIB_SCREEN"
    PLACE_RESULT = "PLACE_RESULT"
    CALIB_BOTTOM = "CALIB_BOTTOM"
    CALIB_RESULT = "CALIB_RESULT"
    LIVE = "LIVE"
    DONE = "DONE"


@dataclass
class CueSpec:
    """A calibration cue: what to tell the user and where the target is."""

    cue: str
    title_ko: str
    title_en: str
    detail_ko: str
    detail_en: str
    colour: Tuple[int, int, int]
    #: Target dot as a fraction of the view, or ``None`` for the lens (it is
    #: outside the picture, so an arrow points up at it instead).
    target: Optional[Tuple[float, float]]


_CUES: Dict[str, CueSpec] = {
    Stage.CALIB_CAMERA.value: CueSpec(
        "CAMERA",
        "카메라 렌즈를 보세요", "LOOK AT THE LENS",
        "편한 자세로 렌즈를 바라보세요", "Look into the lens as you naturally would",
        _GREEN, None,
    ),
    Stage.CALIB_SCREEN.value: CueSpec(
        "SCREEN",
        "화면 가운데 점을 보세요", "LOOK AT THE CENTRE DOT",
        "평소 화면을 볼 때처럼 한가운데를 보세요", "Look at the centre as you normally would",
        _BLUE, (0.5, 0.5),
    ),
    Stage.CALIB_BOTTOM.value: CueSpec(
        "BOTTOM",
        "영상 하단의 점을 보세요", "LOOK AT THE VIDEO-BOTTOM DOT",
        "대본이 놓일 화면 아래 가운데를 보세요", "Look at the bottom centre, where a script would be",
        _AMBER, (0.5, 0.965),
    ),
}

#: Cue wording when the backbone reads the eyes (``session.eye_based``): the
#: head has to stay put so that only the eyes move between the targets.
_EYE_DETAIL: Dict[str, Tuple[str, str]] = {
    Stage.CALIB_CAMERA.value: ("고개는 그대로 두고 눈만 움직여 렌즈를 보세요",
                               "Keep your head still and look into the lens"),
    Stage.CALIB_SCREEN.value: ("고개는 그대로, 눈만 화면 한가운데로",
                               "Head still - move only your eyes to the centre"),
}

#: Korean renderings of the library hints. The library stays English because it
#: is not a UI, but a Korean-speaking presenter should not hit an English wall
#: at the exact moment something went wrong.
_HINT_KO: Dict[str, str] = {
    # placement verdicts
    "TOP": "카메라가 화면 위 가운데에 있습니다.",
    "BOTTOM": "카메라가 화면 아래에 있는 것으로 보입니다. 이 배치에서는 위·아래 판정이 뒤바뀌므로"
              " 카메라를 화면 위 가운데로 옮겨 주세요.",
    "SIDE_LEFT": "카메라가 화면 왼쪽에 있는 것으로 보입니다. 카메라를 화면 위 가운데로 옮겨 주세요.",
    "SIDE_RIGHT": "카메라가 화면 오른쪽에 있는 것으로 보입니다. 카메라를 화면 위 가운데로 옮겨 주세요.",
    # placement / calibration failures
    "NOT_ENOUGH_SAMPLES": "쓸 수 있는 프레임이 부족합니다. 얼굴이 화면에 잘 보이고"
                          " 조명이 충분한지 확인한 뒤 다시 시도하세요.",
    "TARGETS_NOT_SEPARATED": "두 지시가 같게 측정됐습니다. 첫 번째는 렌즈를, 두 번째는"
                             " 화면 한가운데를 확실히 바라보세요.",
    "DISPLACEMENT_TOO_SMALL": "렌즈와 화면 중앙의 방향이 거의 같습니다. 카메라가 화면"
                              " 중앙에 있거나, 너무 멀리 앉아 계실 수 있습니다.",
    "AMBIGUOUS_AXIS": "카메라가 화면 중앙에서 대각선으로 치우쳐 있어 '아래'인지 '옆'인지"
                      " 구분되지 않습니다. 웹캠을 화면 위 가운데에 두고 다시 시도하세요.",
    "CLASS_NOT_SEPARABLE": "카메라를 볼 때와 영상 하단을 볼 때의 시선이 충분히 다르지 않습니다."
                           " 영상 하단을 더 확실히 본 뒤 다시 보정하세요.",
    "LOW_LOO_ACCURACY": "보정 표본이 서로 섞여 있습니다. 각 지점을 확실히, 한동안 바라본 뒤"
                        " 다시 시도하세요.",
    "CENTROIDS_TOO_CLOSE": "두 지점의 시선이 너무 가깝습니다. 각 지점을 더 확실히 바라본 뒤"
                           " 다시 보정해 보세요.",
    "ANCHOR_AMBIGUOUS": "한 지점이 주변 영역과 구분되지 않습니다. 조금 더 가까이 앉아"
                        " 각 지점을 확실히 바라보세요.",
    "DEGENERATE_FEATURES": "보정 중 시선 값이 전혀 변하지 않았습니다. 조명과 얼굴 인식 상태를"
                           " 확인한 뒤 다시 시도하세요.",
    "INVERTED_PITCH": "지점들의 위아래 순서가 뒤집혀 측정됐습니다. 안내 지점을 반대로 보지"
                      " 않았는지, 카메라가 화면 위에 있는지 확인하세요.",
    "SCREEN_MERGED": "렌즈와 화면 가운데를 볼 때가 거의 같게 측정되어, 화면을 보는 것도"
                     " 정면(카메라)으로 판정합니다.",
}

_HINT_EN: Dict[str, str] = {
    "TOP": "The camera is above the centre of the screen.",
    "BOTTOM": "The camera seems to be below the screen. Up and down would be inverted - move it "
              "to the top centre of the screen.",
    "SIDE_LEFT": "The camera seems to be left of the screen. Move it to the top centre.",
    "SIDE_RIGHT": "The camera seems to be right of the screen. Move it to the top centre.",
    "NOT_ENOUGH_SAMPLES": "Too few usable frames were collected. Keep your face visible "
                          "and well lit, then retry if needed.",
    "TARGETS_NOT_SEPARATED": "The two reference looks measured alike. Look at the lens first "
                             "and the display centre second if you want to retry.",
    "DISPLACEMENT_TOO_SMALL": "The lens and display-centre directions measured almost alike.",
    "AMBIGUOUS_AXIS": "The camera is diagonally off the screen centre. Put it at the top centre.",
    "CLASS_NOT_SEPARABLE": "Your camera and video-bottom looks were not clearly separated. "
                           "Retry if the live result is unstable.",
    "LOW_LOO_ACCURACY": "The calibration samples overlap. Hold each look steady if you "
                        "choose to retry.",
    "CENTROIDS_TOO_CLOSE": "Two targets measured close together. Look clearly at each one.",
    "ANCHOR_AMBIGUOUS": "One target could not be told apart from its surroundings. Sit closer "
                        "and look clearly at each target.",
    "DEGENERATE_FEATURES": "The calibration samples barely changed. Check face tracking and "
                           "lighting before retrying.",
    "INVERTED_PITCH": "The targets measured in the wrong vertical order. Check that they were "
                      "not followed in reverse and that the camera is above the screen.",
    "SCREEN_MERGED": "The lens and the screen centre measured alike, so a look at the screen "
                     "counts as facing front (CAMERA).",
}

#: Set-up checks (``runtime.preconditions``): checklist name and the fix.
_CHECK_NAME_KO = {
    "NO_FACE": "얼굴 인식", "MULTIPLE_FACES": "한 명만", "OFF_CENTER": "화면 가운데",
    "TOO_FAR": "너무 멀지 않음", "TOO_CLOSE": "너무 가깝지 않음", "FACING_AWAY": "정면 응시",
    "TOO_DARK": "얼굴 밝기", "BACKLIT": "역광 없음", "LOW_FPS": "프레임 속도",
}
_CHECK_NAME_EN = {
    "NO_FACE": "face found", "MULTIPLE_FACES": "one person", "OFF_CENTER": "centred",
    "TOO_FAR": "not too far", "TOO_CLOSE": "not too close", "FACING_AWAY": "facing screen",
    "TOO_DARK": "face lit", "BACKLIT": "no backlight", "LOW_FPS": "frame rate",
}
_CHECK_FIX_KO = {
    "OK": "잠시 그대로 계세요.",
    "NO_FACE": "카메라 앞에 앉아 얼굴이 보이게 해 주세요.",
    "MULTIPLE_FACES": "화면에 한 사람만 보이도록 해 주세요.",
    "OFF_CENTER": "얼굴이 화면 가운데에 오도록 자리를 옮겨 주세요.",
    "TOO_FAR": "카메라에 조금 더 가까이 와 주세요.",
    "TOO_CLOSE": "카메라에서 조금 뒤로 물러나 주세요.",
    "FACING_AWAY": "화면을 정면으로 바라봐 주세요.",
    "TOO_DARK": "얼굴이 너무 어둡습니다. 앞쪽에 조명을 켜 주세요.",
    "BACKLIT": "뒤쪽이 너무 밝습니다. 창문을 가리거나 빛을 마주 보세요.",
    "LOW_FPS": "카메라가 느립니다. 카메라나 CPU를 쓰는 다른 프로그램을 닫아 주세요.",
}

#: Gauge reject reasons (``calibration.gauge``) and preprocess invalid reasons.
_GAUGE_KO = {
    "NO_FACE": "얼굴이 보이지 않습니다",
    "LOW_FACE_CONFIDENCE": "얼굴이 일부만 보입니다",
    "FACE_TOO_SMALL": "얼굴이 너무 작습니다 - 가까이 오세요",
    "OUT_OF_FRAME": "얼굴이 화면 밖으로 나갔습니다",
    "EYES_CLOSED": "눈을 뜬 채로 바라봐 주세요",
    "CROP_FAILED": "눈 영역이 보이지 않습니다",
    "BACKBONE_FAILED": "시선을 측정하지 못했습니다",
    "LOW_GAZE_CONFIDENCE": "시선이 잘 읽히지 않습니다 - 얼굴에 빛을 더해 주세요",
    "HEAD_MOVED": "고개는 그대로 두고 눈만 움직여 주세요",
    "BLINK": "깜빡임 - 잠시 눈을 뜨고 바라봐 주세요",
    "OUTLIER": "같은 지점을 계속 바라봐 주세요",
    "LOOK_HIGHER": "렌즈 쪽으로 고개를 조금 더 들어 주세요",
    "LOOK_LOWER": "영상 하단 쪽으로 고개를 조금 더 숙여 주세요",
    "OFF_TARGET": "목표 쪽으로 고개를 돌려 주세요",
}

#: Live condition issues (``runtime.condition``).
_ISSUE_KO = {
    "HEAD_TURNED": "고개가 보정 때보다 많이 돌아감",
    "TOO_FAR": "보정 때보다 멀어짐",
    "TOO_CLOSE": "보정 때보다 가까워짐",
    "OFF_CENTER": "화면 가운데에서 벗어남",
    "SECOND_FACE": "참고: 다른 사람이 보임 (신뢰도 영향 없음)",
    "LOW_VALID_RATIO": "얼굴 인식이 자주 끊김",
    "NOISY_TRACKING": "고개 방향 값이 흔들림",
    "FACE_LOST": "얼굴이 사라짐",
    "FACE_REPLACED": "다른 사람이 측정되고 있음",
    "MOVED_TOO_FAR": "처음 위치에서 너무 벗어남 - 측정 불가",
}

_STATE_KO = {
    GazeState.CAMERA.value: "카메라 응시",
    GazeState.SCREEN.value: "화면 응시",
    GazeState.BOTTOM.value: "대본(영상 하단) 응시",
    GazeState.OTHER.value: "다른 곳 응시",
    GazeState.UNCERTAIN.value: "판단 보류",
}

#: OTHER direction, from the presenter's side (their own right / left).
_DIRECTION_KO = {
    "RIGHT": "오른쪽", "UP_RIGHT": "오른쪽 위", "UP": "위", "UP_LEFT": "왼쪽 위",
    "LEFT": "왼쪽", "DOWN_LEFT": "왼쪽 아래", "DOWN": "아래", "DOWN_RIGHT": "오른쪽 아래",
}

#: Coach-input gaze issues (gaze_lab.evidence.gaze), as the live view names them.
_COACH_KO = {
    "GAZE_ON_SCRIPT": "대본을 오래 봄",
    "GAZE_ON_SCREEN": "화면을 오래 봄",
    "GAZE_AWAY": "다른 곳을 오래 봄",
    "GAZE_LOW_EYE_CONTACT": "청중을 보는 시간이 적음",
    "GAZE_UNMEASURABLE": "시선 측정 불가 · 피드백 보류",
}

#: Why a frame of the head circle lit nothing.
_SWEEP_REASON_KO = {
    "TOO_FAST": "조금 더 천천히 돌려 주세요",
    "NO_FACE": "얼굴이 화면을 벗어났어요 · 조금 덜 돌려 주세요",
    "NO_HEAD_POSE": "얼굴 방향을 읽지 못했어요 · 조금 덜 돌려 주세요",
    "OUT_OF_FRAME": "얼굴이 화면 밖으로 나갔어요 · 조금 덜 돌려 주세요",
}

_PLACEMENT_KO = {
    "TOP": "화면 위 가운데",
    "BOTTOM": "화면 아래",
    "SIDE_LEFT": "화면 왼쪽",
    "SIDE_RIGHT": "화면 오른쪽",
    "INCONCLUSIVE": "판별 어려움",
}


def _aim_text(aim, ko: bool) -> str:
    """Where the head points from the screen-centre look: "좌우 왼쪽 8° · 상하 위 3°"."""
    if aim is None:
        return ""
    right, up = float(aim[0]), float(aim[1])

    def part(v: float, pos: str, neg: str) -> str:
        return ("가운데" if ko else "centre") if abs(v) < 0.5 else f"{pos if v > 0 else neg} {abs(v):.0f}°"

    if ko:
        return f"좌우 {part(right, '오른쪽', '왼쪽')} · 상하 {part(up, '위', '아래')}"
    return f"x {part(right, 'right', 'left')} · y {part(up, 'up', 'down')}"


def _hint_text(demo: "PipelineDemo", key: Optional[str], fallback: Optional[str]) -> Optional[str]:
    """Return UI wording for a quality code in the language actually rendered."""
    hints = _HINT_KO if (demo.korean and demo._unicode_ok) else _HINT_EN

    translated: List[str] = []
    if key and key in hints:
        translated.append(hints[key])

    # A failed calibration can carry the pitch-ordering warning in addition to
    # its primary reason.  Preserve both pieces of advice when localising it.
    # The warning never reaches ``quality.reason`` (it is advisory, see
    # schemas.CalibrationFailReason.INVERTED_PITCH), so the hint prefix is the
    # only thing there is to match on.
    inverted_key = CalibrationFailReason.INVERTED_PITCH.value
    if fallback and INVERTED_PITCH_HINT_PREFIX in fallback and key != inverted_key:
        translated.append(hints[inverted_key])
    if fallback and "SCREEN_MERGED" in fallback:
        translated.append(hints["SCREEN_MERGED"])

    if translated:
        return " ".join(translated)
    return fallback


_STAGE_STEP = {
    Stage.CHECK.value: (1, "환경 점검", "SETUP CHECK"),
    Stage.SWEEP.value: (1, "얼굴 확인", "HEAD CIRCLE"),
    Stage.CALIB_CAMERA.value: (2, "시선 보정", "GAZE CALIBRATION"),
    Stage.CALIB_SCREEN.value: (2, "시선 보정", "GAZE CALIBRATION"),
    Stage.PLACE_RESULT.value: (2, "카메라 위치 확인", "CAMERA POSITION"),
    Stage.CALIB_BOTTOM.value: (2, "시선 보정", "GAZE CALIBRATION"),
    Stage.CALIB_RESULT.value: (2, "시선 보정 결과", "CALIBRATION RESULT"),
    Stage.LIVE.value: (3, "실시간 시선 판별", "LIVE GAZE"),
}

_PROB_ORDER = (GazeState.CAMERA.value, GazeState.SCREEN.value, GazeState.BOTTOM.value, GazeState.OTHER.value)


class PipelineDemo:
    """Drives set-up check -> calibration -> live gaze over any frame source.

    ``flow="placement"`` stops at the camera-position verdict (what the dataset
    collector records); ``strict`` (default: ``preconditions.strict``) decides
    whether a failed check, an unsupported camera position or a failed
    calibration blocks the next step (see ``gaze_lab.runtime.policy``).
    """

    def __init__(self, cfg: VisionConfig, session: VisionSession, *, countdown_s: float = 1.5,
                 korean: bool = True, flow: str = "full", strict: Optional[bool] = None) -> None:
        if flow not in ("full", "placement"):
            raise ValueError(f"flow must be 'full' or 'placement', got {flow!r}")
        self.cfg = cfg
        self.session = session
        self.countdown_s = countdown_s
        self.korean = korean
        self.flow = flow
        self.strict = bool(cfg.preconditions.strict) if strict is None else bool(strict)
        self.sampler = FrameSampler(cfg.preprocess.analysis_fps)
        self.stage = Stage.INTRO
        self.stage_started_ms: Optional[int] = None
        self._sweep_started = False
        self._cue_started = False
        self._cue_failed = False
        self.debug = False
        self.overlay = FaceOverlay()
        # Probed once: without Pillow and a CJK font, Korean strings would be
        # drawn by cv2.putText as boxes, so every caller must agree on this.
        # Keep the layer as well: its font objects are expensive enough that
        # throwing the cache away on every camera frame is visible as UI jitter.
        self.text_layer = TextLayer()
        self._unicode_ok = self.text_layer.unicode_ready
        self.show_overlay = True
        self.show_mesh = True
        self.log: List[str] = []
        self._fps_ema: Optional[float] = None
        self._coach_key: Optional[Tuple[int, int]] = None
        self._coach: List[dict] = []
        self._last_wall: Optional[float] = None
        self._forced = False

    @property
    def _eye_based(self) -> bool:
        """Eye backbone: hold the head still.  Default head-pose: look naturally."""
        return bool(getattr(self.session, "eye_based", False))

    # -- helpers ----------------------------------------------------------
    def _t(self, ko: str, en: str) -> str:
        return ko if (self.korean and self._unicode_ok) else en

    def _enter(self, stage: Stage, t_ms: int) -> None:
        self.stage = stage
        self.stage_started_ms = t_ms
        self._sweep_started = False
        self._cue_started = False
        self._cue_failed = False
        self.sampler.reset()
        self.log.append(f"[{t_ms:>7} ms] -> {stage.value}")

    def _elapsed(self, t_ms: int) -> float:
        if self.stage_started_ms is None:
            return 0.0
        return (t_ms - self.stage_started_ms) / 1000.0

    @property
    def is_collecting(self) -> bool:
        return self.stage.value in _CUES

    # -- main step --------------------------------------------------------
    def step(self, bgr: np.ndarray, t_ms: int) -> np.ndarray:
        """Advance the machine by one captured frame and return the display frame."""
        if self._last_wall is not None:
            dt = time.perf_counter() - self._last_wall
            if dt > 0:
                inst = 1.0 / dt
                self._fps_ema = inst if self._fps_ema is None else 0.9 * self._fps_ema + 0.1 * inst
        self._last_wall = time.perf_counter()

        if self.stage_started_ms is None:
            self.stage_started_ms = t_ms

        analyse = self.sampler.should_process(t_ms)

        if self.stage is Stage.CHECK:
            if analyse:
                self._step_check(bgr, t_ms)
        elif self.stage is Stage.SWEEP:
            if analyse:
                self._step_sweep(bgr, t_ms)
        elif self.is_collecting:
            self._step_collect(bgr, t_ms, analyse)
        elif self.stage is Stage.LIVE and analyse:
            self.session.process_frame(bgr, t_ms)
        elif analyse:
            # Intro and verdict screens still track, so the preview never freezes
            # on a face that is no longer in front of the lens.
            self.session.preview(bgr, t_ms)

        return self._render(bgr, t_ms)

    def _step_check(self, bgr: np.ndarray, t_ms: int) -> None:
        report = self.session.check_preconditions(bgr, t_ms)
        if report.passed:
            self.log.append(f"          set-up check: PASS {report.measurements}")
            self._after_check(t_ms)

    def _after_check(self, t_ms: int) -> None:
        """The head circle comes next in the full flow; the placement flow goes straight to the looks."""
        self._enter(Stage.SWEEP if self.flow == "full" else Stage.CALIB_CAMERA, t_ms)

    def _step_sweep(self, bgr: np.ndarray, t_ms: int) -> None:
        if not self._sweep_started:
            self.session.start_head_sweep(t_ms)
            self._sweep_started = True
        status = self.session.offer_sweep_frame(bgr, t_ms)
        if status.finished:
            self._finish_sweep("", t_ms)

    def _finish_sweep(self, how: str, t_ms: int) -> None:
        """The ring never blocks: log how far it got and go on to the looks."""
        self.log.append(f"          head circle: {summary_line(self.session.head_sweep)}{how}")
        self._enter(Stage.CALIB_CAMERA, t_ms)

    def _step_collect(self, bgr: np.ndarray, t_ms: int, analyse: bool) -> None:
        cue = _CUES[self.stage.value]
        if self._cue_failed:
            if analyse:
                self.session.preview(bgr, t_ms)
            return
        if self._elapsed(t_ms) < self.countdown_s:
            # Track during the countdown too: the point of those seconds is to
            # let the user find the target before anything is counted.
            if analyse:
                self.session.preview(bgr, t_ms)
            return
        if not self._cue_started:
            self.session.start_calibration_cue(cue.cue, t_ms)
            self._cue_started = True
        if not analyse:
            return
        status = self.session.offer_calibration_frame(bgr, t_ms)
        if status.finished:
            self._finish_cue(status, t_ms)

    def _finish_cue(self, status, t_ms: int) -> None:
        self.log.append(
            f"          {status.cue}: {status.state} good={status.good}/{status.target} "
            f"rejected={status.rejected} dominant={status.dominant_reason}"
        )
        check = getattr(self.session, "baseline_check", None)
        if status.cue == "SCREEN" and check is not None:
            verdict = "confirmed" if check.confirmed else "remeasured"
            self.log.append(f"          head-circle centre {verdict}: shift {check.shift_deg:.1f} deg, "
                            f"{check.seeded} circle frames folded in")
        if status.state == "TIMED_OUT" and self.strict:
            self._cue_failed = True  # wait for R (retry this look) or C (force on)
            return
        self._next_after_cue(t_ms)

    def _next_after_cue(self, t_ms: int) -> None:
        if self.stage is Stage.CALIB_CAMERA:
            self._enter(Stage.CALIB_SCREEN, t_ms)
        elif self.stage is Stage.CALIB_SCREEN:
            result = self.session.estimate_placement()
            self.log.append(f"          placement: {None if result is None else result.summary()}")
            # Caught before the last look: an unsupported camera position would
            # make the script cue meaningless.
            if self.flow == "placement":
                self._enter(Stage.PLACE_RESULT, t_ms)
            elif result is not None and result.supported:
                self._enter(Stage.CALIB_BOTTOM, t_ms)
            elif not self._eye_based and (result is None or result.placement == "INCONCLUSIVE"):
                # With the head-pose backbone the lens and the screen centre may
                # share one head posture, which says nothing about the camera.
                self.log.append("          placement not readable from the head posture; continuing")
                self._enter(Stage.CALIB_BOTTOM, t_ms)
            else:
                self._enter(Stage.PLACE_RESULT, t_ms)
        elif self.stage is Stage.CALIB_BOTTOM:
            quality = self.session.finish_calibration()
            self.log.append(
                f"          calibration: {quality.status} method={quality.method} "
                f"loo={quality.loo_accuracy:.2f} sep={quality.separability:.2f} "
                f"n=({quality.n_camera},{quality.n_screen},{quality.n_bottom}) {quality.reason or ''}"
            )
            self._enter(Stage.CALIB_RESULT, t_ms)

    # -- keyboard ---------------------------------------------------------
    def on_key(self, key: int, t_ms: int) -> bool:
        """Handle a key. Returns False to quit."""
        if key in (ord("q"), 27):
            return False
        if key == ord("d"):
            self.debug = not self.debug
        elif key == ord("m"):
            self.show_mesh = not self.show_mesh
        elif key == ord("r"):
            self._retry(t_ms)
        elif key in (ord(" "), 13):
            self._advance(t_ms)
        elif key == ord("c"):
            self._force(t_ms)
        elif key == ord("s"):
            self._dump()
        elif key == ord("a"):
            self._reanchor(t_ms)
        return True

    def _retry(self, t_ms: int) -> None:
        if self.stage is Stage.CHECK:
            self.session.reset_preconditions()
            self._enter(Stage.CHECK, t_ms)
        elif self.stage is Stage.SWEEP:
            self._enter(Stage.SWEEP, t_ms)
        elif self.is_collecting:
            # Restarting a cue replaces only that look's samples.
            self._enter(self.stage, t_ms)
        elif self.stage in (Stage.PLACE_RESULT, Stage.CALIB_RESULT, Stage.LIVE):
            self.session.reset_calibration()
            self._enter(Stage.CALIB_CAMERA, t_ms)

    def _advance(self, t_ms: int) -> None:
        if self.stage is Stage.INTRO:
            self.session.reset_preconditions()
            self._enter(Stage.CHECK, t_ms)
        elif self.stage is Stage.CHECK:
            if not self.strict:
                self.log.append("          set-up check skipped (advisory)")
                self._after_check(t_ms)
        elif self.stage is Stage.SWEEP:
            self._finish_sweep(" (skipped)", t_ms)
        elif self.stage is Stage.PLACE_RESULT:
            if self.flow == "placement":
                return
            if gate_placement(self.session.placement_result, self.strict,
                              block_inconclusive=self._eye_based).allowed:
                self._enter(Stage.CALIB_BOTTOM, t_ms)
        elif self.stage is Stage.CALIB_RESULT:
            decision = gate_calibration(
                self.session.calibration_quality, self.session.is_calibrated, self.strict
            )
            if decision.allowed:
                self._enter(Stage.LIVE, t_ms)

    def _force(self, t_ms: int) -> None:
        """Continue past a blocking verdict, and remember that we did."""
        if self.stage is Stage.CHECK:
            self._forced = True
            self.log.append("          set-up check overridden by operator")
            self._after_check(t_ms)
        elif self.is_collecting and self._cue_failed:
            self._forced = True
            self.log.append("          failed cue overridden by operator")
            self._cue_failed = False
            self._next_after_cue(t_ms)
        elif self.stage is Stage.PLACE_RESULT and self.flow == "full":
            self._forced = True
            self.log.append("          placement verdict overridden by operator")
            self._enter(Stage.CALIB_BOTTOM, t_ms)
        elif self.stage is Stage.CALIB_RESULT and self.session.is_calibrated:
            self._forced = True
            self._enter(Stage.LIVE, t_ms)

    def _reanchor(self, t_ms: int) -> None:
        if self.stage is Stage.LIVE:
            status = self.session.begin_reanchor(t_ms)
            self.log.append(f"[{t_ms:>7} ms] re-anchor {status.state}")

    def auto_advance(self, t_ms: int) -> bool:
        """Headless driver: press SPACE where a person would.  False = blocked.

        Applies exactly the gates the keys apply, so an unattended run stops on
        the same verdicts a strict UI would; advisory runs record and continue.
        """
        if self.stage is Stage.INTRO:
            self._advance(t_ms)
            return True
        if self.stage is Stage.CHECK:
            report = self.session.precondition_report
            if report is not None and report.status == "REJECT":
                if self.strict:
                    return False
                self.log.append(f"          set-up check REJECT {report.reason} ignored (advisory)")
                self._after_check(t_ms)
            return True
        if self.stage is Stage.SWEEP:
            # Unattended: nobody is in front of the lens to turn a head.
            self._finish_sweep(" (skipped: headless)", t_ms)
            return True
        if self.is_collecting:
            return not self._cue_failed
        if self.stage in (Stage.PLACE_RESULT, Stage.CALIB_RESULT):
            if self.stage is Stage.PLACE_RESULT and self.flow == "placement":
                return True
            before = self.stage
            self._advance(t_ms)
            return self.stage is not before
        return True

    def _dump(self) -> None:
        out = REPORTS_DIR / "demo_events.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        self.session.dump_events(out, debug=True)
        conditions = self.session.dump_condition_events(
            REPORTS_DIR / "demo_conditions.jsonl", debug=True
        )
        self.log.append(f"          events -> {out}, conditions -> {conditions}")

    # -- rendering --------------------------------------------------------
    def _render(self, bgr: np.ndarray, t_ms: int) -> np.ndarray:
        # Analysis runs on the raw frame (placement yaw signs depend on it);
        # only the display is mirrored, so the user sees a natural selfie view.
        view = cv2.flip(bgr, 1)
        h, w = view.shape[:2]
        layer = self.text_layer
        ko = self.korean and self._unicode_ok

        if self.show_overlay:
            self.overlay.draw(
                view, self.session.last_observation,
                mesh=self.show_mesh, mirrored=True,
            )
        self._draw_header(view, layer, ko, w)
        if self.stage is Stage.INTRO:
            self._draw_intro(view, layer, ko, w, h)
        elif self.stage is Stage.CHECK:
            self._draw_check(view, layer, ko, w, h)
        elif self.stage is Stage.SWEEP:
            self._draw_sweep(view, layer, ko, w, h)
        elif self.is_collecting:
            self._draw_cue(view, layer, ko, w, h, t_ms)
        elif self.stage is Stage.PLACE_RESULT:
            self._draw_placement_result(view, layer, ko, w, h)
        elif self.stage is Stage.CALIB_RESULT:
            self._draw_calibration_result(view, layer, ko, w, h)
        elif self.stage is Stage.LIVE:
            self._draw_live(view, layer, ko, w, h)

        return layer.flush(view)

    def _draw_header(self, view, layer, ko, w) -> None:
        cv2.rectangle(view, (0, 0), (w, 34), _DARK, -1)
        step = _STAGE_STEP.get(self.stage.value)
        if step is None:
            title = self._t("피치코치 시선 인식 데모", "Pitch Coach gaze demo") if ko else "Pitch Coach gaze demo"
            layer.put((10, 7), title, 18, _WHITE)
            return
        n, ko_name, en_name = step
        name = ko_name if ko else en_name
        mode = self._t("엄격", "strict") if self.strict else self._t("참고", "advisory")
        layer.put((10, 7), f"STEP {n}/3  {name}   [{mode}]", 18, _WHITE)
        for i in range(3):
            colour = _GREEN if i + 1 < n else (_BLUE if i + 1 == n else (70, 70, 70))
            cv2.rectangle(view, (w - 130 + i * 40, 12), (w - 100 + i * 40, 22), colour, -1)

    def _draw_intro(self, view, layer, ko, w, h) -> None:
        _dim(view, 0.55)
        lines = (
            [
                ("피치코치 시선 인식", 34, _WHITE),
                ("", 10, _WHITE),
                ("1단계  환경 점검 (한 명 · 화면 가운데 · 거리 · 조명) → 고개를 돌려 원 채우기", 20, _GREY),
                ("2단계  " + ("고개는 고정, 눈만: " if self._eye_based else "편하게 바라보기: ")
                 + "렌즈 → 화면 가운데 → 영상 하단", 20, _GREY),
                ("3단계  실시간 판별 (카메라 / 화면 / 대본 / 다른 곳)", 20, _GREY),
                ("", 10, _WHITE),
                ("이 창을 한 번 클릭한 뒤", 16, _GREY),
                ("SPACE 를 눌러 시작", 22, _GREEN),
            ]
            if ko
            else [
                ("Pitch Coach gaze demo", 34, _WHITE),
                ("", 10, _WHITE),
                ("Step 1  set-up check, then turn the head to fill the circle", 20, _GREY),
                ("Step 2  " + ("head still, eyes only: " if self._eye_based else "look naturally: ")
                 + "lens -> centre -> video bottom", 20, _GREY),
                ("Step 3  live CAMERA / SCREEN / BOTTOM / OTHER", 20, _GREY),
                ("", 10, _WHITE),
                ("Click this window first, then", 16, _GREY),
                ("press SPACE to start", 22, _GREEN),
            ]
        )
        y = h // 2 - 110
        for text, size, colour in lines:
            if text:
                layer.put(((w - text_size(text, size)) // 2, y), text, size, colour)
            y += size + 12

    def _draw_check(self, view, layer, ko, w, h) -> None:
        report = self.session.precondition_report
        names = _CHECK_NAME_KO if ko else _CHECK_NAME_EN
        x, y, row_h = 14, 46, 24
        rows = report.checks if report is not None else []
        _scrim(view, x - 6, y - 8, 300, 36 + row_h * max(1, len(rows)), 0.72)
        layer.put((x, y), self._t("환경 점검", "Set-up check"), 20, _WHITE)
        y += 30
        for check in rows:
            mark, colour = ("O", _GREEN) if check.ok else ("X", _RED)
            layer.put((x, y), f"{mark}  {names.get(check.name, check.name)}", 16, colour)
            y += row_h

        if report is None:
            status_text, colour, fix = self._t("카메라 프레임 대기 중", "waiting for frames"), _GREY, ""
        elif report.status == "PASS":
            status_text, colour, fix = self._t("통과", "PASS"), _GREEN, ""
        elif report.status == "REJECT":
            status_text, colour = self._t("조건 미달", "NOT SUITABLE"), _RED
            fix = _CHECK_FIX_KO.get(report.reason, report.hint) if ko else report.hint
        else:
            held = report.held_ms / 1000.0
            status_text = self._t(f"확인 중 ({held:.1f}초 유지)", f"checking (held {held:.1f}s)")
            colour = _AMBER if report.reason != "OK" else _BLUE
            fix = _CHECK_FIX_KO.get(report.reason, report.hint) if ko else report.hint

        _scrim(view, 0, h - 112, w, 112, 0.72)
        layer.put(((w - text_size(status_text, 26)) // 2, h - 104), status_text, 26, colour)
        if fix:
            layer.put(((w - text_size(fix, 17)) // 2, h - 66), fix, 17, _WHITE)
        footer = (
            self._t("R 다시 확인   C 무시하고 진행", "R check again   C continue anyway")
            if self.strict
            else self._t("SPACE 건너뛰기   R 다시 확인", "SPACE skip   R check again")
        )
        layer.put(((w - text_size(footer, 15)) // 2, h - 32), footer, 15, _GREY)

    def _draw_sweep(self, view, layer, ko, w, h) -> None:
        """The ring around the face: lit where the head went (mirrored view: your right is on the right)."""
        status = self.session.head_sweep
        obs = self.session.last_observation
        if obs is not None and obs.face_bbox is not None:
            bx, by, bw, bh = obs.face_bbox
            cx, cy = w - (bx + bw / 2.0), by + bh / 2.0  # mirrored x
            radius = 0.72 * max(bw, bh)
        else:
            cx, cy, radius = w / 2.0, h / 2.0, 0.22 * h
        n = len(status.ticks) or 32
        current = None
        if status.pointer_deg is not None:
            current = int(((status.pointer_deg + 180.0 / n) % 360.0) // (360.0 / n)) % n
        for i in range(n):
            a = 2.0 * np.pi * i / n
            lit = bool(status.ticks[i]) if status.ticks else False
            length = 16 if lit else 10
            colour = _GREEN if lit else (110, 110, 110)
            if i == current:
                length, colour = 10 + int(10 * min(1.0, status.reach)), _AMBER
            r0, r1 = radius, radius + length
            p0 = (int(cx + r0 * np.cos(a)), int(cy - r0 * np.sin(a)))
            p1 = (int(cx + r1 * np.cos(a)), int(cy - r1 * np.sin(a)))
            cv2.line(view, p0, p1, colour, 3 if lit else 2, cv2.LINE_AA)

        if status.state == "DONE":
            title, colour = self._t("얼굴 확인 완료", "circle complete"), _GREEN
        elif status.hint:
            name = _DIRECTION_KO.get(status.hint, status.hint) if ko else status.hint
            title, colour = self._t(f"{name} 쪽으로 천천히 돌려 주세요", f"turn slowly toward {name}"), _AMBER
        else:
            title, colour = self._t("천천히 고개를 돌려 원을 채워 주세요", "turn your head slowly to fill the circle"), _WHITE
        reason = status.last_reason
        detail = (_SWEEP_REASON_KO.get(reason, reason) if ko else reason) if reason else f"{status.filled}/{status.total}"
        _scrim(view, 0, h - 112, w, 112, 0.72)
        layer.put(((w - text_size(title, 24)) // 2, h - 104), title, 24, colour)
        layer.put(((w - text_size(detail, 17)) // 2, h - 66), detail, 17, _AMBER if reason else _GREY)
        footer = self._t("SPACE 건너뛰기   R 처음부터", "SPACE skip   R restart")
        layer.put(((w - text_size(footer, 15)) // 2, h - 32), footer, 15, _GREY)

    def _draw_target(self, view, layer, ko, w, h, cue: CueSpec, t_ms: int) -> None:
        """The thing to look at: a pulsing dot, or an arrow up at the lens."""
        pulse = 10 + int(4 * (1 + np.sin(t_ms / 160.0)))
        if cue.target is None:
            tip, base = (w // 2, 40), (w // 2, 120)
            cv2.arrowedLine(view, base, tip, cue.colour, 4, cv2.LINE_AA, tipLength=0.35)
            label = self._t("렌즈", "lens")
            layer.put((w // 2 + 14, 70), label, 18, cue.colour)
            return
        cx, cy = int(cue.target[0] * w), int(cue.target[1] * h)
        cv2.circle(view, (cx, cy), pulse + 6, _DARK, -1, cv2.LINE_AA)
        cv2.circle(view, (cx, cy), pulse, cue.colour, -1, cv2.LINE_AA)
        cv2.circle(view, (cx, cy), 3, _WHITE, -1, cv2.LINE_AA)

    def _draw_cue(self, view, layer, ko, w, h, t_ms) -> None:
        cue = _CUES[self.stage.value]
        elapsed = self._elapsed(t_ms)
        counting = elapsed < self.countdown_s and not self._cue_started

        # The cue lives in a band at the top and the gauge at the bottom, so
        # the middle of the frame -- where the face is -- stays unobstructed.
        _scrim(view, 0, 34, w, 92, 0.66)
        title = cue.title_ko if ko else cue.title_en
        detail = cue.detail_ko if ko else cue.detail_en
        if self._eye_based and self.stage.value in _EYE_DETAIL:
            detail = _EYE_DETAIL[self.stage.value][0 if ko else 1]
        layer.put(((w - text_size(title, 34)) // 2, 44), title, 34, cue.colour)
        layer.put(((w - text_size(detail, 17)) // 2, 92), detail, 17, _GREY)
        self._draw_target(view, layer, ko, w, h, cue, t_ms)
        self._draw_face_status(view, layer, ko, w, h)

        if counting:
            remain = int(np.ceil(self.countdown_s - elapsed))
            radius = 46
            centre = (w // 2, h - 140)
            _scrim(view, centre[0] - radius, centre[1] - radius, radius * 2, radius * 2, 0.6)
            cv2.circle(view, centre, radius, cue.colour, 2, cv2.LINE_AA)
            layer.put((centre[0] - text_size(str(remain), 52) // 2, centre[1] - 34),
                      str(remain), 52, _WHITE)
            return

        status = self.session.gauge_status
        bar_w, x0, y0 = int(w * 0.5), int(w * 0.25), h - 110
        _scrim(view, x0 - 12, y0 - 30, bar_w + 24, 80, 0.66)
        if self._cue_failed:
            reason = status.dominant_reason
            fix = (_GAUGE_KO.get(reason, reason or "") if ko else (status.hint or reason or ""))
            headline = self._t("이 지점 보정에 실패했습니다", "This look could not be calibrated")
            layer.put(((w - text_size(headline, 20)) // 2, y0 - 24), headline, 20, _RED)
            layer.put(((w - text_size(fix, 16)) // 2, y0 + 4), fix, 16, _WHITE)
            footer = self._t("R 이 지점 다시   C 무시하고 진행", "R retry this look   C continue anyway")
            layer.put(((w - text_size(footer, 15)) // 2, y0 + 28), footer, 15, _GREY)
            return
        cv2.rectangle(view, (x0, y0), (x0 + bar_w, y0 + 14), (70, 70, 70), -1)
        cv2.rectangle(view, (x0, y0), (x0 + int(bar_w * status.progress), y0 + 14), cue.colour, -1)
        label = (
            f"좋은 프레임 {status.good} / {status.target}"
            if ko
            else f"good frames {status.good} / {status.target}"
        )
        layer.put(((w - text_size(label, 17)) // 2, y0 - 26), label, 17, _WHITE)
        reason = status.last_reason
        if reason and reason not in ("SETTLING", "NOT_ACTIVE"):
            text = _GAUGE_KO.get(reason, reason) if ko else (status.hint or reason)
            layer.put(((w - text_size(text, 16)) // 2, y0 + 22), text, 16, _AMBER)
        elif cue.cue == "SCREEN":
            note = self._baseline_note(status)
            if note:
                layer.put(((w - text_size(note, 16)) // 2, y0 + 22), note, 16, _BLUE)

    def _baseline_note(self, status) -> Optional[str]:
        """The screen-centre look against the head circle's centre, while it runs."""
        check = getattr(self.session, "baseline_check", None)
        if check is not None and not check.confirmed:
            return self._t(f"고개 원 때와 자세가 {check.shift_deg:.0f}° 달라 화면 가운데를 다시 재는 중",
                           f"moved {check.shift_deg:.0f} deg since the head circle: measuring again")
        if check is None and status.target < int(self.session.cfg.calibration.target_good_frames):
            return self._t("고개 원에서 잰 정면 기준을 확인하는 중", "confirming the head circle's centre")
        return None

    def _draw_card(self, view, layer, w, h, headline, colour, rows, hint, footer) -> None:
        """Verdict panel: a readable card over a still-live camera view."""
        pad, x = 18, 14
        # Widen for whatever the headline actually needs rather than clipping it.
        card_w = min(w - 2 * x, max(int(w * 0.60), text_size(headline, 26) + 2 * pad + 16))
        y = 46
        footer_h, gap = 44, 8
        hint_size, hint_gap = 14, 20
        hint_width = max(1, card_w - 2 * pad)
        hint_lines = _wrap(hint, hint_width, hint_size) if hint else []

        # Keep the panel above the footer even for a 360p input.  Metrics are
        # the contract, so preserve every row and compact only the prose hint.
        base_h = pad * 2 + 40 + len(rows) * 25
        available_h = max(base_h, h - y - footer_h - gap)
        if hint_lines:
            max_hint_lines = max(0, (available_h - base_h - 12) // hint_gap)
            if len(hint_lines) > max_hint_lines:
                hint_lines = hint_lines[:max_hint_lines]
                if hint_lines:
                    hint_lines[-1] = _ellipsize(hint_lines[-1], hint_width, hint_size)
        card_h = base_h + (len(hint_lines) * hint_gap + 12 if hint_lines else 0)
        _scrim(view, x, y, card_w, card_h, 0.78)
        cv2.rectangle(view, (x, y), (x + card_w, y + card_h), colour, 1)

        ty = y + pad
        layer.put((x + pad, ty), headline, 26, colour)
        ty += 44
        for name, value in rows:
            layer.put((x + pad, ty), str(name), 16, _GREY)
            layer.put((x + pad + int(card_w * 0.52), ty), str(value), 16, _WHITE)
            ty += 25
        if hint_lines:
            ty += 10
            for line in hint_lines:
                layer.put((x + pad, ty), line, 14, _GREY)
                ty += 20

        _scrim(view, 0, h - 44, w, 44, 0.78)
        layer.put(((w - text_size(footer, 16)) // 2, h - 34), footer, 16, _WHITE)

    def _draw_face_status(self, view, layer, ko, w, h) -> None:
        """Say out loud whether the face is being tracked right now.

        Without this a user who is doing everything right but sitting in the
        dark sees a normal-looking cue and a gauge that never fills.  The reason
        code is already computed per frame; the only bug was never showing it.
        """
        obs = self.session.last_observation
        if obs is None:
            text, colour = (
                ("카메라 프레임 대기 중", _GREY) if ko else ("waiting for frames", _GREY)
            )
        elif obs.face_valid:
            text, colour = (("얼굴 인식됨", _GREEN) if ko else ("face tracked", _GREEN))
        else:
            reason = obs.invalid_reason or "NO_FACE"
            ko_reason = _GAUGE_KO.get(reason, reason)
            text, colour = ((ko_reason, _RED) if ko else (reason.replace("_", " ").lower(), _RED))

        cv2.circle(view, (22, h - 26), 7, colour, -1, cv2.LINE_AA)
        layer.put((38, h - 36), text, 18, colour)

    def _draw_placement_result(self, view, layer, ko, w, h) -> None:
        result = self.session.placement_result
        if result is None:
            headline = self._t("카메라 위치를 측정하지 못했습니다", "Camera position not measured")
            footer = self._t("R 처음부터 다시   C 무시하고 진행", "R start over   C continue anyway")
            self._draw_card(view, layer, w, h, headline, _RED, [], None, footer)
            return
        supported = result.supported
        colour = _GREEN if supported else (_RED if self.strict else _AMBER)
        placement = _PLACEMENT_KO.get(result.placement, result.placement) if ko else result.placement
        if supported:
            headline = self._t("카메라 위치 확인됨", "Camera position OK")
        elif self.strict:
            headline = self._t("카메라 위치를 고쳐 주세요", "Fix the camera position")
        else:
            headline = self._t("카메라 위치 주의 (참고)", "Camera position warning (advisory)")
        rows = [
            (self._t("위치 판정", "verdict"), placement),
            (self._t("시선 상하차 (중앙-렌즈)", "pitch delta (centre-lens)"), f"{result.delta_pitch_deg:+.1f}deg"),
            (self._t("시선 좌우차 (중앙-렌즈)", "yaw delta (centre-lens)"), f"{result.delta_yaw_deg:+.1f}deg"),
            (self._t("판별 축", "axis"), f"{result.axis} (x{result.axis_dominance:.1f})"),
            (self._t("표본 (렌즈/중앙)", "samples (lens/centre)"), f"{result.n_camera} / {result.n_screen}"),
        ]
        if self.flow == "placement":
            footer = self._t("측정 완료", "measured")
        elif supported or not self.strict:
            footer = self._t("SPACE 다음 지점으로   R 처음부터", "SPACE next look   R start over")
        else:
            footer = self._t("카메라를 옮긴 뒤 R   C 무시하고 진행", "move the camera, then R   C continue anyway")
        hint_key = result.reason if result.placement == "INCONCLUSIVE" else result.placement
        hint = _hint_text(self, hint_key, result.hint)
        self._draw_card(view, layer, w, h, headline, colour, rows, hint, footer)

    def _draw_calibration_result(self, view, layer, ko, w, h) -> None:
        quality = self.session.calibration_quality
        if quality is None:
            return
        usable = self.session.is_calibrated
        allowed = gate_calibration(quality, usable, self.strict).allowed
        if quality.ok:
            colour, headline = _GREEN, self._t("보정 품질 양호", "Calibration quality: good")
            grade = self._t("양호", "GOOD")
        elif usable:
            colour = _RED if self.strict else _AMBER
            headline = self._t("보정 품질 낮음 · 다시 보정 권장", "Calibration quality: low")
            grade = self._t("낮음", "LOW")
        else:
            colour, headline = _RED, self._t("보정 데이터가 부족합니다", "Not enough data to calibrate")
            grade = self._t("사용 불가", "UNUSABLE")
        sep = quality.pair_separation or {}
        rows = [
            (self._t("등급 / 방식", "grade / method"), f"{grade} / {quality.method}"),
            (self._t("LOO 정확도", "LOO accuracy"), f"{quality.loo_accuracy:.2f}"),
            (self._t("렌즈-대본 분리", "lens-script separation"),
             f"{sep.get('CAMERA-BOTTOM', quality.separability):.1f}"),
            (self._t("렌즈-화면 분리", "lens-screen separation"),
             f"{sep['CAMERA-SCREEN']:.1f}" if "CAMERA-SCREEN" in sep else "-"),
            (self._t("눈맞춤 판정 반경", "eye-contact radius"), f"{quality.camera_capture_radius_deg:.1f}deg"),
            (self._t("표본 (렌즈/중앙/하단)", "samples (lens/centre/bottom)"),
             f"{quality.n_camera} / {quality.n_screen} / {quality.n_bottom}"),
        ]
        if quality.reason:
            rows.append((self._t("사유", "reason"), quality.reason))
        if allowed:
            footer = self._t("SPACE 시작   R 다시 보정", "SPACE start   R recalibrate")
        elif usable:
            footer = self._t("R 다시 보정   C 무시하고 시작", "R recalibrate   C start anyway")
        else:
            footer = self._t("R 다시 보정", "R recalibrate")
        hint = _hint_text(self, quality.reason, quality.hint)
        self._draw_card(view, layer, w, h, headline, colour, rows, hint, footer)

    def _draw_live(self, view, layer, ko, w, h) -> None:
        event = self.session.last_event
        decision = self.session.last_decision
        if event is None or decision is None:
            return
        colour = _STATE_COLOURS.get(event.label, _GREY)
        label = _STATE_KO.get(event.label, event.label) if ko else event.label
        direction = self._other_direction(event.label, decision)
        if direction:
            label += f" · {_DIRECTION_KO.get(direction, direction)}" if ko else f" {direction}"

        cv2.rectangle(view, (0, h - 128), (w, h), _DARK, -1)
        layer.put((14, h - 120), label, 32, colour)
        secs = event.continuous_duration_ms / 1000.0
        held = f"{secs:.1f}초 지속" if ko else f"{secs:.1f}s"
        aim = _aim_text(decision.aim_deg, ko)
        layer.put((14, h - 78), held + (f"   {aim}" if aim else ""), 17, _GREY)

        # One bar per class the classifier decides between.
        probs = decision.class_probs()
        x0, bar_w = int(w * 0.40), int(w * 0.30)
        y = h - 122
        for cls in [c for c in _PROB_ORDER if c in probs]:
            value = float(probs[cls])
            cv2.rectangle(view, (x0, y), (x0 + bar_w, y + 12), (60, 60, 60), -1)
            cv2.rectangle(view, (x0, y), (x0 + int(bar_w * value), y + 12), _STATE_COLOURS.get(cls, _GREY), -1)
            name = _STATE_KO.get(cls, cls).split("(")[0] if ko else cls
            layer.put((x0 + bar_w + 8, y - 4), f"{name} {value:.2f}", 13, _GREY)
            y += 18
        coach = self._coach_issues()
        if decision.uncertain_reason:
            layer.put((14, h - 56), f"UNCERTAIN: {decision.uncertain_reason}", 14, _GREY)
        elif coach:
            top = coach[0]
            name = _COACH_KO.get(top["issue_type"], top["issue_type"]) if ko else top["issue_type"]
            layer.put((14, h - 56), self._t("코치 신호: ", "coach: ") + f"{name} ({top['persistence_sec']:.0f}s)",
                      14, _AMBER if top["actionable"] else _GREY)

        self._draw_condition(view, layer, ko, w)

        fps = self._fps_ema or 0.0
        stats = (
            f"{fps:4.1f} fps   p95 {self.session.latency_percentile(95):.0f} ms   "
            f"events {len(self.session.events)}   "
            + self._t("A 다시 맞추기  R 다시 보정  S 저장  Q 종료", "A re-anchor  R recalibrate  S save  Q quit")
        )
        layer.put((14, h - 30), stats, 14, _GREY)

        if self.debug and decision.gaze is not None:
            self._draw_debug(view, layer, w, decision)

    def _coach_issues(self) -> List[dict]:
        """The coach's gaze issues, recomputed only when a 1 s record lands (not every drawn frame)."""
        timeline = self.session.gaze_evidence.timeline
        key = (id(timeline), len(timeline.samples))
        if key != self._coach_key:
            self._coach_key, self._coach = key, self.session.gaze_evidence.issues()
        return self._coach

    def _other_direction(self, label: str, decision) -> Optional[str]:
        """Which way an OTHER look went: this frame's, else the last 1 s record's (the label lags the frame)."""
        if label != GazeState.OTHER.value:
            return None
        if decision.direction:
            return decision.direction
        samples = self.session.gaze_evidence.timeline.samples
        if samples and samples[-1].state == GazeState.OTHER.value:
            return samples[-1].direction
        return None

    def _draw_condition(self, view, layer, ko, w) -> None:
        """Measurement reliability (SESSION_CONDITION) and the re-anchor state."""
        condition = self.session.last_condition
        x, y = w - 300, 44
        lines: List[Tuple[str, Tuple[int, int, int]]] = []
        if condition is not None:
            value = condition.reliability
            colour = _GREEN if value >= 0.8 else (_AMBER if value >= 0.5 else _RED)
            lines.append((self._t(f"측정 신뢰도 {value:.2f}", f"reliability {value:.2f}"), colour))
            for issue in condition.issues[:3]:
                lines.append((_ISSUE_KO.get(issue, issue) if ko else issue, _GREY))
            jitter = getattr(condition, "jitter_deg", None)
            if jitter is not None:
                lines.append((self._t(f"고개 방향 흔들림 {jitter:.1f}°", f"head jitter {jitter:.1f} deg"), _GREY))
            for notice in getattr(condition, "notices", [])[:2]:
                lines.append((_ISSUE_KO.get(notice, notice) if ko else f"notice: {notice}", _BLUE))
            drift = getattr(condition, "drift", None)
            if drift:
                moved = drift["deg"]
                colour = _RED if moved >= drift["fail_deg"] else (_AMBER if moved > drift["warn_deg"] else _GREY)
                lines.append((self._t(
                    f"처음 위치: 오른쪽 {drift['right_cm']:+.1f} 위 {drift['up_cm']:+.1f} "
                    f"가까이 {drift['closer_cm']:+.1f}cm",
                    f"moved: right {drift['right_cm']:+.1f} up {drift['up_cm']:+.1f} "
                    f"closer {drift['closer_cm']:+.1f}cm"), colour))
                lines.append((self._t(f"판정 오차 {moved:.1f}° / 한계 {drift['fail_deg']:.1f}°",
                                      f"error {moved:.1f} / limit {drift['fail_deg']:.1f} deg"), colour))
        reanchor = self.session.reanchor_status
        if reanchor.state == "COLLECTING":
            lines.append((self._t(f"다시 맞추기: 렌즈를 보세요 {reanchor.collected}/{reanchor.target}",
                                  f"re-anchor: look at the lens {reanchor.collected}/{reanchor.target}"), _BLUE))
        elif reanchor.state == "DONE" and reanchor.shift_deg is not None:
            dy, dp = reanchor.shift_deg
            lines.append((self._t(f"다시 맞춤 완료 ({dy:+.1f}, {dp:+.1f}deg)",
                                  f"re-anchored ({dy:+.1f}, {dp:+.1f}deg)"), _GREEN))
        elif reanchor.state in ("REJECTED", "TIMED_OUT", "UNSUPPORTED"):
            lines.append((self._t(f"다시 맞추기 실패: {reanchor.reason}", f"re-anchor failed: {reanchor.reason}"), _RED))
        if not lines:
            return
        _scrim(view, x - 8, y - 6, 296, 24 * len(lines) + 8, 0.72)
        for text, colour in lines:
            layer.put((x, y), text, 15, colour)
            y += 24

    def _draw_debug(self, view, layer, w, decision) -> None:
        gaze = decision.gaze
        rows = [
            f"gaze  yaw {gaze.gaze_yaw_deg:+6.1f}  pitch {gaze.gaze_pitch_deg:+6.1f}  conf {gaze.confidence:.2f}",
            f"lat   {decision.latency_ms:5.1f} ms",
            f"state {self.session.smoother.state}",
        ]
        result = self.session.placement_result
        if result is not None:
            rows.append(f"place {result.placement} dpitch {result.delta_pitch_deg:+.1f}")
        y = 44
        for row in rows:
            layer.put((14, y), row, 14, _GREY)
            y += 18


def _dim(frame: np.ndarray, amount: float) -> None:
    frame[:] = (frame * (1.0 - amount)).astype(np.uint8)


def _scrim(frame: np.ndarray, x: int, y: int, w: int, h: int, alpha: float = 0.72) -> None:
    """Darken one rectangle so text on it stays readable.

    Used instead of dimming the whole frame: this is a camera app first, and a
    user who cannot see the live image cannot tell tracking from a frozen frame.
    """
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(frame.shape[1], x + w), min(frame.shape[0], y + h)
    if x1 <= x0 or y1 <= y0:
        return
    region = frame[y0:y1, x0:x1]
    region[:] = (region * (1.0 - alpha)).astype(np.uint8)


def _wrap(text: str, max_width: int, size: int) -> List[str]:
    """Wrap prose to an approximate pixel width, splitting long tokens safely."""
    lines: List[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and text_size(candidate, size) > max_width:
            lines.append(current)
            current = ""

        # URLs/codes can exceed the card without containing a single space.
        while text_size(word, size) > max_width:
            cut = 1
            while cut < len(word) and text_size(word[:cut + 1], size) <= max_width:
                cut += 1
            lines.append(word[:cut])
            word = word[cut:]

        current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def _ellipsize(text: str, max_width: int, size: int) -> str:
    suffix = "..."
    trimmed = text.rstrip()
    while trimmed and text_size(trimmed + suffix, size) > max_width:
        trimmed = trimmed[:-1].rstrip()
    return trimmed + suffix


# --------------------------------------------------------------------------
# Frame sources
# --------------------------------------------------------------------------


def open_camera(index: int, width: int, height: int) -> "cv2.VideoCapture":
    """Open a webcam and negotiate a format that actually streams at speed.

    The default UVC format is uncompressed YUY2, which a laptop webcam can only
    sustain at ~10 FPS above VGA -- enough to make the demo look frozen while
    the pipeline itself is fine.

    Order matters, and not in the obvious way.  Measured on this machine at
    1280x720: setting FOURCC before the resolution leaves the camera in YUY2 at
    10.2 FPS, while setting the resolution *first* and MJPG *after* gives MJPG
    at 30.3 FPS.  Setting the size re-negotiates the format and discards a
    FOURCC chosen beforehand, so MJPG has to come last.
    """
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(index, cv2.CAP_MSMF)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        raise RuntimeError(
            f"could not open camera index {index}. Close any other app using the "
            f"webcam (Zoom, Teams, the Camera app) and check Windows privacy "
            f"settings, then retry. Use --camera-index 1 if you have several."
        )
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def measure_fps(cap: "cv2.VideoCapture", frames: int = 24, timeout_s: float = 4.0) -> float:
    """Delivered frame rate, which is the only rate that matters.

    ``CAP_PROP_FPS`` reports -1 on plenty of Windows webcams, so the format
    negotiation above has to be checked by actually pulling frames.
    """
    for _ in range(4):
        cap.read()  # discard the first frames while exposure settles
    seen, start = 0, time.perf_counter()
    while seen < frames and time.perf_counter() - start < timeout_s:
        if cap.read()[0]:
            seen += 1
    elapsed = time.perf_counter() - start
    return seen / elapsed if elapsed > 0 else 0.0


def open_camera_checked(index: int, width: int, height: int,
                        *, min_fps: float = 15.0) -> Tuple["cv2.VideoCapture", float]:
    """Open the camera, and drop to VGA if the requested mode streams too slowly.

    A 10 FPS capture makes every cue feel unresponsive and starves the 8 FPS
    analysis rate, so a smaller frame that actually arrives beats a larger one
    that does not.
    """
    cap = open_camera(index, width, height)
    fps = measure_fps(cap)
    if fps >= min_fps or (width, height) == (640, 480):
        return cap, fps
    print(f"camera {index}: {camera_report(cap)} delivered only {fps:.1f} fps, "
          f"falling back to 640x480")
    cap.release()
    cap = open_camera(index, 640, 480)
    return cap, measure_fps(cap)


def camera_report(cap: "cv2.VideoCapture") -> str:
    fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
    tag = "".join(chr((fourcc >> (8 * i)) & 0xFF) for i in range(4)).strip() or "?"
    return (
        f"{int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))} "
        f"@ {cap.get(cv2.CAP_PROP_FPS):.0f} fps, fourcc={tag}"
    )


def _webcam_frames(index: int, width: int, height: int) -> Iterator[Tuple[int, np.ndarray]]:
    cap, fps = open_camera_checked(index, width, height)
    print(f"camera {index}: {camera_report(cap)}, measured {fps:.1f} fps")
    start = time.perf_counter()
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            # Wall-clock timestamps: the smoother's dwell times are in real
            # seconds, and a webcam's delivered rate is not its nominal one.
            yield int((time.perf_counter() - start) * 1000.0), frame
    finally:
        cap.release()


def _video_frames(path: Path) -> Iterator[Tuple[int, np.ndarray]]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"could not open video {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            # Source timestamps, so a recorded run replays identically.
            yield int(idx * 1000.0 / fps), frame
            idx += 1
    finally:
        cap.release()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def run_check(args) -> int:
    """Diagnose the setup without starting the flow: camera, then face tracking.

    Answers the only two questions that matter when the demo "does not work":
    is the webcam delivering frames at a usable rate, and does the landmarker
    find a face in them.
    """
    print("1) camera")
    try:
        cap, fps = open_camera_checked(args.camera_index, args.width, args.height)
    except RuntimeError as exc:
        print(f"   FAIL  {exc}")
        return 1
    print(f"   open  {camera_report(cap)}")

    frames = []
    t0 = time.perf_counter()
    while len(frames) < 24 and time.perf_counter() - t0 < 4.0:
        ok, frame = cap.read()
        if ok:
            frames.append(frame)
    cap.release()
    if not frames:
        print("   FAIL  camera opened but delivered no frames")
        return 1
    verdict = "ok" if fps >= 15 else "SLOW - the window will feel laggy"
    print(f"   rate  {fps:.1f} fps  ({verdict})")

    print("2) face tracking")
    cfg = load_config()
    if args.backbone:
        cfg.backbone.name = args.backbone
    with VisionSession(cfg) as session:
        tracked, reasons = 0, {}
        for i, frame in enumerate(frames[-20:]):
            session.add_placement_frame(frame, TARGET_CAMERA, i * 125)
            obs = session.last_observation
            if obs is not None and obs.face_valid:
                tracked += 1
            elif obs is not None:
                reasons[obs.invalid_reason or "NO_FACE"] = (
                    reasons.get(obs.invalid_reason or "NO_FACE", 0) + 1
                )
        n = min(20, len(frames))
        print(f"   face tracked in {tracked}/{n} frames")
        if reasons:
            print(f"   reject reasons : {reasons}")
        gaze = session.last_gaze
        if gaze is not None:
            print(f"   last gaze      : yaw {gaze.gaze_yaw_deg:+.1f} deg  "
                  f"pitch {gaze.gaze_pitch_deg:+.1f} deg  conf {gaze.confidence:.2f}")
        print(f"   backbone       : {cfg.backbone.name}")
        print(f"   korean text    : {'yes' if TextLayer().unicode_ready else 'no'}")
        if tracked == 0:
            print("\n   FAIL  no face found. Sit in front of the camera, add light on your "
                  "face, and make sure nothing covers the lens.")
            return 1

        print("3) set-up check (one person, centred, distance, light)")
        report = None
        for i, frame in enumerate(frames[-20:]):
            report = session.check_preconditions(frame, i * 125)
        print(f"   status         : {report.status} ({report.reason})")
        print(f"   measurements   : {report.measurements}")
        failing = [c.name for c in report.checks if not c.ok]
        if failing:
            print(f"   failing checks : {failing}")
            print(f"   fix            : {report.hint}")
    print("\nAll good - run the demo without --check.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Staged vision-pipeline demo: set-up check -> calibration -> live gaze.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--camera-index", type=int, default=0)
    p.add_argument("--video", type=Path, help="replay a recorded file instead of the webcam")
    p.add_argument("--backbone", default=None, help="override the configured gaze backbone")
    p.add_argument("--method", choices=("reference", "logistic"), default=None,
                   help="override calibration.method (reference anchors or the logistic ablation)")
    p.add_argument("--advisory", action="store_true",
                   help="report failed checks and verdicts without blocking (research mode)")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--countdown", type=float, default=1.5, help="seconds shown before each cue collects")
    p.add_argument("--no-window", action="store_true",
                   help="headless; auto-advances stages and stops where a strict UI would block")
    p.add_argument("--english", action="store_true", help="force English on-screen text")
    p.add_argument("--max-seconds", type=float, default=0.0, help="stop after N seconds (0 = no limit)")
    p.add_argument("--check", action="store_true",
                   help="diagnose camera and face tracking, then exit")
    p.add_argument("--no-overlay", action="store_true", help="hide the face overlay")
    p.add_argument("--no-mesh", action="store_true",
                   help="keep the full-face crosshair and iris dots but drop the contour mesh")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.check:
        return run_check(args)

    cfg = load_config()
    if args.backbone:
        cfg.backbone.name = args.backbone
    if args.method:
        cfg.calibration.method = args.method
    strict = False if args.advisory else bool(cfg.preconditions.strict)

    source = _video_frames(args.video) if args.video else _webcam_frames(
        args.camera_index, args.width, args.height
    )
    window = "Pitch Coach - vision pipeline"
    headless = args.no_window

    with VisionSession(cfg) as session:
        demo = PipelineDemo(cfg, session, countdown_s=args.countdown, korean=not args.english,
                            strict=strict)
        demo.show_overlay = not args.no_overlay
        demo.show_mesh = not args.no_mesh
        print(f"backbone={cfg.backbone.name}  method={cfg.calibration.method}  "
              f"mode={'strict' if strict else 'advisory'}  analysis_fps={cfg.preprocess.analysis_fps}  "
              f"korean_text={'yes' if TextLayer().unicode_ready else 'no (ascii fallback)'}")
        if headless:
            print("headless mode: stages auto-advance, no window")
        else:
            cv2.namedWindow(window, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(window, 1024, 768)
            # Keys only reach OpenCV while its window has focus, which is the
            # single most common reason SPACE "does nothing".
            print("\nA window has opened. CLICK IT FIRST, then press SPACE to start.")
            print("keys: SPACE next | R retry | C continue anyway | A re-anchor | D debug | "
                  "M mesh | S save events | Q quit\n")

        seen = 0
        for t_ms, frame in source:
            view = demo.step(frame, t_ms)
            seen += 1

            if headless:
                # Nobody is there to press SPACE, so apply the same gates the
                # keys apply -- the flow is identical, just unattended.
                if not demo.auto_advance(t_ms):
                    print(f"blocked at {demo.stage.value}: a strict verdict stopped the flow")
                    break
            else:
                cv2.imshow(window, view)
                if not demo.on_key(cv2.waitKey(1) & 0xFF, t_ms):
                    break

            if args.max_seconds and t_ms > args.max_seconds * 1000:
                break

        if not headless:
            cv2.destroyAllWindows()

        print(f"\nframes seen: {seen}   final stage: {demo.stage.value}")
        print("--- stage log ---")
        for line in demo.log:
            print(line)

        placement = session.placement_result
        if placement is not None:
            print("\n--- placement ---")
            print(json.dumps(placement.to_dict(), ensure_ascii=False, indent=2))
        report = session.precondition_report
        if report is not None:
            print("\n--- set-up check ---")
            print(f"{report.status} ({report.reason}) {report.measurements}")
        quality = session.calibration_quality
        if quality is not None:
            print("\n--- calibration (doc 5-2) ---")
            print(json.dumps(quality.to_dict(), ensure_ascii=False, indent=2))
        if session.rejected_frames:
            print("\nrejected frames by reason:", dict(session.rejected_frames))
        if session.is_calibrated:
            print(f"\nsmoother classes: {session.smoother.classes}")
            print(f"GAZE_STATE events: {len(session.events)}   "
                  f"SESSION_CONDITION events: {len(session.condition_events)}")
            labels = [event.label for event in session.events]
            counts = {label: labels.count(label) for label in sorted(set(labels))}
            print(f"labels emitted: {counts}")
            condition = session.last_condition
            if condition is not None:
                print(f"last condition: reliability {condition.reliability:.2f} issues {condition.issues}")
            print(f"latency p95: {session.latency_percentile(95):.1f} ms "
                  f"(budget {cfg.release_gate.max_p95_latency_ms:.0f} ms)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
