"""Staged walkthrough of the vision pipeline -- the thing to run first.

Three stages, in the order the model actually needs them:

    1. SETUP   camera placement check   (is the lens above the screen?)
    2. CALIB   2-point calibration      (doc 5, CAMERA vs BOTTOM for this person)
    3. LIVE    smoothed GAZE_STATE      (doc 6)

Stage 1 is an advisory setup estimate.  It asks for two reference looks to
estimate whether the webcam sits above the display like a laptop camera, but a
missed cue can skew that estimate, so it never blocks calibration.  Stage 2
learns this user's actual CAMERA versus VIDEO-BOTTOM looks and reports its
quality as another advisory; only a calibration with no fitted model is blocked.

Run it::

    ./.venv/Scripts/python.exe -m ai.tools.pipeline_demo                 # webcam
    ./.venv/Scripts/python.exe -m ai.tools.pipeline_demo --video x.mp4   # recorded
    ./.venv/Scripts/python.exe -m ai.tools.pipeline_demo --video x.mp4 --no-window

Keys: SPACE continue, R retry stage, D toggle debug overlay,
S dump events to JSONL, Q quit.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

import cv2
import numpy as np

# Allow running as a plain script as well as with -m.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "ai" / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "ai" / "src"))

from vision.config import VisionConfig, load_config  # noqa: E402
from vision.preprocess.sampler import FrameSampler  # noqa: E402
from vision.runtime.placement import TARGET_CAMERA, TARGET_SCREEN  # noqa: E402
from vision.runtime.session import VisionSession  # noqa: E402
from vision.schemas import (  # noqa: E402
    INVERTED_PITCH_HINT_PREFIX,
    CalibrationFailReason,
    GazeLabel,
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

_STATE_COLOURS = {
    GazeState.CAMERA.value: _GREEN,
    GazeState.BOTTOM.value: _AMBER,
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
    PLACE_CAMERA = "PLACE_CAMERA"
    PLACE_SCREEN = "PLACE_SCREEN"
    PLACE_RESULT = "PLACE_RESULT"
    CALIB_CAMERA = "CALIB_CAMERA"
    CALIB_BOTTOM = "CALIB_BOTTOM"
    CALIB_RESULT = "CALIB_RESULT"
    LIVE = "LIVE"
    DONE = "DONE"


@dataclass
class CueSpec:
    """A timed collection cue: what to tell the user, and for how long."""

    title_ko: str
    title_en: str
    detail_ko: str
    detail_en: str
    seconds: float
    colour: Tuple[int, int, int]


_CUES: Dict[str, CueSpec] = {
    Stage.PLACE_CAMERA.value: CueSpec(
        "카메라 렌즈를 보세요", "LOOK AT THE LENS",
        "웹캠의 렌즈를 똑바로 응시하세요", "Stare straight into the webcam lens",
        2.0, _BLUE,
    ),
    Stage.PLACE_SCREEN.value: CueSpec(
        "화면 중앙을 보세요", "LOOK AT SCREEN CENTRE",
        "모니터 화면 한가운데를 보세요", "Look at the middle of the display",
        2.0, _BLUE,
    ),
    Stage.CALIB_CAMERA.value: CueSpec(
        "카메라를 보세요", "LOOK AT THE CAMERA",
        "발표하듯 카메라를 응시하세요", "Look at the camera as if presenting",
        2.0, _GREEN,
    ),
    Stage.CALIB_BOTTOM.value: CueSpec(
        "영상 하단을 보세요", "LOOK AT VIDEO BOTTOM",
        "현재 영상의 아래쪽 중앙을 응시하세요", "Look at the bottom centre of this video",
        2.0, _AMBER,
    ),
}

#: Korean renderings of the library hints. The library stays English because it
#: is not a UI, but a Korean-speaking presenter should not hit an English wall
#: at the exact moment something went wrong.
_HINT_KO: Dict[str, str] = {
    # placement verdicts
    "TOP": "노트북 내장 카메라처럼 화면 위에 있는 형태로 추정됩니다.",
    "BOTTOM": "카메라가 화면 아래에 있는 형태로 추정됩니다. 권장 위치와 달라도"
              " 2단계에서 실제 시선을 보정한 뒤 계속 사용할 수 있습니다.",
    "SIDE_LEFT": "카메라가 화면 왼쪽에 있는 형태로 추정됩니다. 가능하면 화면 위"
                 " 중앙에 두되, 2단계 실제 시선 보정은 계속할 수 있습니다.",
    "SIDE_RIGHT": "카메라가 화면 오른쪽에 있는 형태로 추정됩니다. 가능하면 화면 위"
                  " 중앙에 두되, 2단계 실제 시선 보정은 계속할 수 있습니다.",
    # placement failures
    "NOT_ENOUGH_SAMPLES": "쓸 수 있는 프레임이 부족합니다. 얼굴이 화면에 잘 보이고"
                          " 조명이 충분한지 확인한 뒤 다시 시도하세요.",
    "TARGETS_NOT_SEPARATED": "두 지시가 같게 측정됐습니다. 첫 번째는 렌즈를, 두 번째는"
                             " 화면 한가운데를 보세요. 고개가 아니라 눈을 움직여야 합니다.",
    "DISPLACEMENT_TOO_SMALL": "렌즈와 화면 중앙의 방향이 거의 같습니다. 카메라가 화면"
                              " 중앙에 있거나, 너무 멀리 앉아 계실 수 있습니다.",
    "AMBIGUOUS_AXIS": "카메라가 화면 중앙에서 대각선으로 치우쳐 있어 '아래'인지 '옆'인지"
                      " 구분되지 않습니다. 웹캠을 화면 위 중앙에 두고 다시 시도하세요.",
    # calibration failures
    "CLASS_NOT_SEPARABLE": "카메라를 볼 때와 영상 하단을 볼 때의 시선이 충분히 다르지 않습니다."
                           " 필요하면 영상 하단을 더 확실히 본 뒤 다시 보정하세요.",
    "LOW_LOO_ACCURACY": "보정 표본이 서로 섞여 있습니다. 각 지시 동안 시선을 한 곳에"
                        " 고정하고 다시 시도하세요.",
    "CENTROIDS_TOO_CLOSE": "두 시선 방향이 너무 가깝습니다. 영상 하단을 더 확실히 본 뒤"
                           " 다시 보정해 보세요.",
    "DEGENERATE_FEATURES": "보정 표본의 변화가 거의 없습니다. 조명과 얼굴 인식 상태를"
                           " 확인한 뒤 다시 시도하세요.",
    "INVERTED_PITCH": "영상 하단을 볼 때의 시선이 카메라를 볼 때보다 위쪽으로 측정됐습니다."
                      " 안내 지점을 반대로 보지 않았는지 확인하세요.",
}

_HINT_EN: Dict[str, str] = {
    "TOP": "The camera is estimated to be above the display, like a laptop webcam.",
    "BOTTOM": "The camera is estimated to be below the display. You can still continue "
              "after calibrating your actual looks in step 2.",
    "SIDE_LEFT": "The camera is estimated to be left of the display. Centre it above the "
                 "screen when practical; step 2 can still calibrate your actual looks.",
    "SIDE_RIGHT": "The camera is estimated to be right of the display. Centre it above the "
                  "screen when practical; step 2 can still calibrate your actual looks.",
    "NOT_ENOUGH_SAMPLES": "Too few usable frames were collected. Keep your face visible "
                          "and well lit, then retry if needed.",
    "TARGETS_NOT_SEPARATED": "The two reference looks measured alike. Look at the lens first "
                             "and the display centre second if you want to retry.",
    "DISPLACEMENT_TOO_SMALL": "The lens and display-centre directions measured almost alike.",
    "AMBIGUOUS_AXIS": "The estimated camera direction is diagonal and therefore uncertain.",
    "CLASS_NOT_SEPARABLE": "Your camera and video-bottom looks were not clearly separated. "
                           "Retry if the live result is unstable.",
    "LOW_LOO_ACCURACY": "The calibration samples overlap. Hold each instructed look steady "
                        "if you choose to retry.",
    "CENTROIDS_TOO_CLOSE": "The two looks are close together. Look more clearly toward the "
                           "video bottom if you choose to retry.",
    "DEGENERATE_FEATURES": "The calibration samples barely changed. Check face tracking and "
                           "lighting before retrying.",
    "INVERTED_PITCH": "The video-bottom look measured above the camera look. Check that the "
                      "two targets were not followed in reverse.",
}


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

    if translated:
        return " ".join(translated)
    return fallback


_STAGE_STEP = {
    Stage.PLACE_CAMERA.value: (1, "카메라 위치 참고 측정", "SETUP ESTIMATE"),
    Stage.PLACE_SCREEN.value: (1, "카메라 위치 참고 측정", "SETUP ESTIMATE"),
    Stage.PLACE_RESULT.value: (1, "카메라 위치 참고 측정", "SETUP ESTIMATE"),
    Stage.CALIB_CAMERA.value: (2, "카메라·영상 하단 보정", "GAZE CALIBRATION"),
    Stage.CALIB_BOTTOM.value: (2, "카메라·영상 하단 보정", "GAZE CALIBRATION"),
    Stage.CALIB_RESULT.value: (2, "카메라·영상 하단 보정", "GAZE CALIBRATION"),
    Stage.LIVE.value: (3, "실시간 시선 판별", "LIVE GAZE"),
}


class PipelineDemo:
    """Drives the three stages over an arbitrary frame source."""

    def __init__(self, cfg: VisionConfig, session: VisionSession, *, countdown_s: float = 1.5,
                 korean: bool = True) -> None:
        self.cfg = cfg
        self.session = session
        self.countdown_s = countdown_s
        self.korean = korean
        self.sampler = FrameSampler(cfg.preprocess.analysis_fps)
        self.stage = Stage.INTRO
        self.stage_started_ms: Optional[int] = None
        self.collected = 0
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
        self._last_wall: Optional[float] = None
        self._forced = False

    # -- helpers ----------------------------------------------------------
    def _t(self, ko: str, en: str) -> str:
        return ko if (self.korean and self._unicode_ok) else en

    def _enter(self, stage: Stage, t_ms: int) -> None:
        self.stage = stage
        self.stage_started_ms = t_ms
        self.collected = 0
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

        if self.is_collecting:
            self._step_collect(bgr, t_ms, analyse)
        elif self.stage is Stage.LIVE and analyse:
            self.session.process_frame(bgr, t_ms)
        elif analyse:
            # Intro and verdict screens still track, so the preview never freezes
            # on a face that is no longer in front of the lens.
            self.session.preview(bgr, t_ms)

        return self._render(bgr, t_ms)

    def _step_collect(self, bgr: np.ndarray, t_ms: int, analyse: bool) -> None:
        cue = _CUES[self.stage.value]
        elapsed = self._elapsed(t_ms)
        if elapsed >= self.countdown_s + cue.seconds:
            self._finish_collect(t_ms)
            return
        if not analyse:
            return
        if elapsed < self.countdown_s:
            # Track during the countdown too: the point of those seconds is to
            # let the user see they are being tracked before it starts counting.
            self.session.preview(bgr, t_ms)
            return

        if self.stage is Stage.PLACE_CAMERA:
            ok = self.session.add_placement_frame(bgr, TARGET_CAMERA, t_ms)
        elif self.stage is Stage.PLACE_SCREEN:
            ok = self.session.add_placement_frame(bgr, TARGET_SCREEN, t_ms)
        elif self.stage is Stage.CALIB_CAMERA:
            ok = self.session.add_calibration_frame(bgr, GazeLabel.CAMERA, t_ms)
        else:
            ok = self.session.add_calibration_frame(bgr, GazeLabel.BOTTOM, t_ms)
        self.collected += int(bool(ok))

    def _finish_collect(self, t_ms: int) -> None:
        if self.stage is Stage.PLACE_CAMERA:
            self._enter(Stage.PLACE_SCREEN, t_ms)
        elif self.stage is Stage.PLACE_SCREEN:
            result = self.session.finish_placement()
            self.log.append(f"          placement: {result.summary()}")
            self._enter(Stage.PLACE_RESULT, t_ms)
        elif self.stage is Stage.CALIB_CAMERA:
            self._enter(Stage.CALIB_BOTTOM, t_ms)
        else:
            quality = self.session.finish_calibration()
            self.log.append(
                f"          calibration: {quality.status} loo={quality.loo_accuracy:.2f} "
                f"sep={quality.separability:.2f} n=({quality.n_camera},{quality.n_bottom}) "
                f"{quality.reason or ''}"
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
        return True

    def _retry(self, t_ms: int) -> None:
        if self.stage in (Stage.PLACE_RESULT, Stage.PLACE_CAMERA, Stage.PLACE_SCREEN):
            self.session.reset_placement()
            self._enter(Stage.PLACE_CAMERA, t_ms)
        elif self.stage in (Stage.CALIB_RESULT, Stage.CALIB_CAMERA, Stage.CALIB_BOTTOM, Stage.LIVE):
            self.session.reset_calibration()
            self._enter(Stage.CALIB_CAMERA, t_ms)

    def _advance(self, t_ms: int) -> None:
        if self.stage is Stage.INTRO:
            self._enter(Stage.PLACE_CAMERA, t_ms)
        elif self.stage is Stage.PLACE_RESULT:
            result = self.session.placement_result
            # This is a user-cued estimate, not ground truth.  Record it as a
            # setup metric but never let a missed cue block actual calibration.
            if result is not None:
                self._enter(Stage.CALIB_CAMERA, t_ms)
        elif self.stage is Stage.CALIB_RESULT:
            quality = self.session.calibration_quality
            # Quality controls the warning colour, not whether a fitted model
            # may run.  Only an absent classifier is a real technical blocker.
            if quality is not None and self.session.is_calibrated:
                self._enter(Stage.LIVE, t_ms)

    def _force(self, t_ms: int) -> None:
        """Continue past a failed verdict, and remember that we did."""
        if self.stage is Stage.PLACE_RESULT:
            self._forced = True
            self.log.append("          placement verdict overridden by operator")
            self._enter(Stage.CALIB_CAMERA, t_ms)
        elif self.stage is Stage.CALIB_RESULT and self.session.is_calibrated:
            self._forced = True
            self._enter(Stage.LIVE, t_ms)

    def _dump(self) -> None:
        out = _REPO_ROOT / "ai" / "reports" / "demo_events.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        self.session.dump_events(out, debug=True)
        self.log.append(f"          events -> {out}")

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
        layer.put((10, 7), f"STEP {n}/3  {name}", 18, _WHITE)
        for i in range(3):
            colour = _GREEN if i + 1 < n else (_BLUE if i + 1 == n else (70, 70, 70))
            cv2.rectangle(view, (w - 130 + i * 40, 12), (w - 100 + i * 40, 22), colour, -1)

    def _draw_intro(self, view, layer, ko, w, h) -> None:
        _dim(view, 0.55)
        lines = (
            [
                ("피치코치 시선 인식", 34, _WHITE),
                ("", 10, _WHITE),
                ("1단계  카메라가 화면 위인지 참고 측정", 20, _GREY),
                ("2단계  카메라 / 영상 하단 시선 보정", 20, _GREY),
                ("3단계  실시간 카메라 · 영상 하단 판별", 20, _GREY),
                ("", 10, _WHITE),
                ("이 창을 한 번 클릭한 뒤", 16, _GREY),
                ("SPACE 를 눌러 시작", 22, _GREEN),
            ]
            if ko
            else [
                ("Pitch Coach gaze demo", 34, _WHITE),
                ("", 10, _WHITE),
                ("Step 1  estimate whether the camera is above the screen", 20, _GREY),
                ("Step 2  calibrate camera / video-bottom looks", 20, _GREY),
                ("Step 3  live CAMERA / BOTTOM state", 20, _GREY),
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

    def _draw_cue(self, view, layer, ko, w, h, t_ms) -> None:
        cue = _CUES[self.stage.value]
        elapsed = self._elapsed(t_ms)
        counting = elapsed < self.countdown_s

        # The cue lives in a band at the top and the progress at the bottom, so
        # the middle of the frame -- where the face is -- stays unobstructed.
        _scrim(view, 0, 34, w, 92, 0.66)
        title = cue.title_ko if ko else cue.title_en
        detail = cue.detail_ko if ko else cue.detail_en
        layer.put(((w - text_size(title, 34)) // 2, 44), title, 34, cue.colour)
        layer.put(((w - text_size(detail, 17)) // 2, 92), detail, 17, _GREY)
        self._draw_face_status(view, layer, ko, w, h)

        if counting:
            remain = int(np.ceil(self.countdown_s - elapsed))
            radius = 46
            centre = (w // 2, h - 128)
            _scrim(view, centre[0] - radius, centre[1] - radius, radius * 2, radius * 2, 0.6)
            cv2.circle(view, centre, radius, cue.colour, 2, cv2.LINE_AA)
            layer.put((centre[0] - text_size(str(remain), 52) // 2, centre[1] - 34),
                      str(remain), 52, _WHITE)
            return

        # Collection progress: the bar tracks time, the count tracks usable frames.
        frac = float(np.clip((elapsed - self.countdown_s) / cue.seconds, 0.0, 1.0))
        bar_w, x0, y0 = int(w * 0.5), int(w * 0.25), h - 96
        _scrim(view, x0 - 12, y0 - 30, bar_w + 24, 74, 0.66)
        cv2.rectangle(view, (x0, y0), (x0 + bar_w, y0 + 14), (70, 70, 70), -1)
        cv2.rectangle(view, (x0, y0), (x0 + int(bar_w * frac), y0 + 14), cue.colour, -1)
        need = (
            self.cfg.placement.min_samples_per_target
            if self.stage.value.startswith("PLACE")
            else self.cfg.calibration.min_samples_per_class
        )
        label = (
            f"수집 {self.collected} / 최소 {need} 프레임"
            if ko
            else f"collected {self.collected} / min {need} frames"
        )
        layer.put(((w - text_size(label, 17)) // 2, y0 + 22), label, 17, _WHITE)

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
        dark sees a normal-looking cue, then an unexplained NOT_ENOUGH_SAMPLES
        four seconds later.  The reason code is already computed per frame; the
        only bug was never showing it.
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
            ko_reason = {
                "NO_FACE": "얼굴이 보이지 않습니다",
                "LOW_FACE_CONFIDENCE": "얼굴 신뢰도가 낮습니다",
                "FACE_TOO_SMALL": "얼굴이 너무 작습니다 - 가까이 오세요",
                "OUT_OF_FRAME": "얼굴이 화면 밖으로 나갔습니다",
                "EYES_CLOSED": "눈이 감겨 있습니다",
                "CROP_FAILED": "눈 영역을 잘라내지 못했습니다",
                "BACKBONE_FAILED": "시선 추정에 실패했습니다",
            }.get(reason, reason)
            text, colour = ((ko_reason, _RED) if ko else (reason.replace("_", " ").lower(), _RED))

        cv2.circle(view, (22, h - 26), 7, colour, -1, cv2.LINE_AA)
        layer.put((38, h - 36), text, 18, colour)

    def _draw_placement_result(self, view, layer, ko, w, h) -> None:
        result = self.session.placement_result
        if result is None:
            return
        colour = _BLUE if result.supported else _AMBER
        placement_ko = {
            "TOP": "화면 위",
            "BOTTOM": "화면 아래",
            "SIDE_LEFT": "화면 왼쪽",
            "SIDE_RIGHT": "화면 오른쪽",
            "INCONCLUSIVE": "판별 어려움",
        }[result.placement]
        headline = self._t("카메라 위치 참고 결과", "Camera position estimate")

        rows = [
            (self._t("위치 추정(참고)", "estimate (advisory)"), placement_ko if ko else result.placement),
            (self._t("시선 상하차", "pitch delta"), f"{result.delta_pitch_deg:+.1f}deg"),
            (self._t("시선 좌우차", "yaw delta"), f"{result.delta_yaw_deg:+.1f}deg"),
            (self._t("응시 분리 정확도", "cue separation (LOO)"), f"{result.loo_accuracy:.2f}"),
            (self._t("판별 축", "axis"), f"{result.axis} (x{result.axis_dominance:.1f})"),
            (self._t("표본", "samples"), f"{result.n_camera} / {result.n_screen}"),
        ]
        footer = self._t(
            "SPACE 2단계로 계속   R 다시 측정",
            "SPACE continue to step 2   R retry",
        )
        hint_key = result.reason if result.placement == "INCONCLUSIVE" else result.placement
        hint = _hint_text(self, hint_key, result.hint)
        advisory = self._t(
            "사용자의 응시 동작에 따라 달라질 수 있는 참고 지표입니다.",
            "This is an advisory estimate and can change if a cue was followed inaccurately.",
        )
        hint = f"{advisory} {hint}" if hint else advisory
        self._draw_card(view, layer, w, h, headline, colour, rows, hint, footer)

    def _draw_calibration_result(self, view, layer, ko, w, h) -> None:
        quality = self.session.calibration_quality
        if quality is None:
            return
        usable = self.session.is_calibrated
        if quality.ok:
            colour = _GREEN
            headline = self._t("보정 품질 양호", "Calibration quality: good")
            grade = self._t("양호", "GOOD")
        elif usable:
            colour = _AMBER
            headline = self._t("보정 품질 낮음 · 참고용", "Calibration quality: low (advisory)")
            grade = self._t("낮음 · 재측정 권장", "LOW - retry recommended")
        else:
            colour = _RED
            headline = self._t("보정 데이터가 부족합니다", "Not enough data to calibrate")
            grade = self._t("사용 불가", "UNUSABLE")
        rows = [
            (self._t("참고 등급", "advisory grade"), grade),
            (self._t("LOO 정확도", "LOO accuracy"), f"{quality.loo_accuracy:.2f}"),
            (self._t("중심 거리", "centroid dist"), f"{quality.centroid_distance:.2f}"),
            (self._t("분리도", "separability"), f"{quality.separability:.2f}"),
            (self._t("표본 (카메라/영상 하단)", "samples (camera/bottom)"),
             f"{quality.n_camera} / {quality.n_bottom}"),
        ]
        if quality.reason:
            rows.append((self._t("참고 사유", "advisory reason"), quality.reason))
        footer = (
            self._t("SPACE 이 보정값으로 시작   R 다시 측정",
                    "SPACE use this calibration   R recalibrate")
            if usable
            else self._t("R 다시 측정 (두 지점 표본 필요)",
                         "R recalibrate (both targets need samples)")
        )
        hint = _hint_text(self, quality.reason, quality.hint)
        advisory = self._t(
            "품질 수치는 안내 지점을 얼마나 일관되게 봤는지 나타내는 참고값입니다.",
            "Quality is advisory and reflects how consistently the two cues were followed.",
        )
        hint = f"{advisory} {hint}" if hint else advisory
        self._draw_card(view, layer, w, h, headline, colour, rows, hint, footer)

    def _draw_live(self, view, layer, ko, w, h) -> None:
        event = self.session.last_event
        decision = self.session.last_decision
        if event is None or decision is None:
            return
        colour = _STATE_COLOURS.get(event.label, _GREY)
        label_ko = {
            "CAMERA": "카메라 응시",
            "BOTTOM": "영상 하단 응시",
            "UNCERTAIN": "판단 보류",
        }[event.label]

        cv2.rectangle(view, (0, h - 108), (w, h), _DARK, -1)
        layer.put((14, h - 100), label_ko if ko else event.label, 34, colour)
        secs = event.continuous_duration_ms / 1000.0
        layer.put((14, h - 56), (f"{secs:.1f}초 지속" if ko else f"{secs:.1f}s"), 17, _GREY)

        # Probability split: one bar, CAMERA growing from the left.
        x0, y0, bar_w = int(w * 0.42), h - 92, int(w * 0.54)
        cv2.rectangle(view, (x0, y0), (x0 + bar_w, y0 + 18), _AMBER, -1)
        cv2.rectangle(view, (x0, y0), (x0 + int(bar_w * decision.p_camera), y0 + 18), _GREEN, -1)
        layer.put((x0, y0 - 21),
                  (f"카메라 {decision.p_camera:.2f}" if ko else f"CAMERA {decision.p_camera:.2f}"),
                  15, _GREEN)
        right = f"영상 하단 {decision.p_bottom:.2f}" if ko else f"BOTTOM {decision.p_bottom:.2f}"
        layer.put((x0 + bar_w - text_size(right, 15), y0 - 21), right, 15, _AMBER)

        if decision.uncertain_reason:
            layer.put((x0, y0 + 24), f"UNCERTAIN: {decision.uncertain_reason}", 14, _GREY)

        fps = self._fps_ema or 0.0
        stats = (
            f"{fps:4.1f} fps   p95 {self.session.latency_percentile(95):.0f} ms   "
            f"frames {self.session.frames_processed}   events {len(self.session.events)}"
        )
        layer.put((14, h - 30), stats, 14, _GREY)

        if self.debug and decision.gaze is not None:
            self._draw_debug(view, layer, w, decision)

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
    print("\nAll good - run the demo without --check.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Staged vision-pipeline demo: placement check -> calibration -> live gaze.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--camera-index", type=int, default=0)
    p.add_argument("--video", type=Path, help="replay a recorded file instead of the webcam")
    p.add_argument("--backbone", default=None, help="override the configured gaze backbone")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--countdown", type=float, default=1.5, help="seconds shown before each cue collects")
    p.add_argument("--no-window", action="store_true", help="headless; auto-advances stages")
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

    source = _video_frames(args.video) if args.video else _webcam_frames(
        args.camera_index, args.width, args.height
    )
    window = "Pitch Coach - vision pipeline"
    headless = args.no_window

    with VisionSession(cfg) as session:
        demo = PipelineDemo(cfg, session, countdown_s=args.countdown, korean=not args.english)
        demo.show_overlay = not args.no_overlay
        demo.show_mesh = not args.no_mesh
        print(f"backbone={cfg.backbone.name}  analysis_fps={cfg.preprocess.analysis_fps}  "
              f"korean_text={'yes' if TextLayer().unicode_ready else 'no (ascii fallback)'}")
        if headless:
            print("headless mode: stages auto-advance, no window")
        else:
            cv2.namedWindow(window, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(window, 1024, 768)
            # Keys only reach OpenCV while its window has focus, which is the
            # single most common reason SPACE "does nothing".
            print("\nA window has opened. CLICK IT FIRST, then press SPACE to start.")
            print("keys: SPACE next | R retry | D debug | M mesh | "
                  "S save events | Q quit\n")

        seen = 0
        for t_ms, frame in source:
            view = demo.step(frame, t_ms)
            seen += 1

            if headless:
                # Nobody is there to press SPACE, so gate on the same conditions
                # the key handler checks -- the flow is identical, just unattended.
                if demo.stage in (Stage.INTRO, Stage.PLACE_RESULT, Stage.CALIB_RESULT):
                    before = demo.stage
                    demo._advance(t_ms)
                    if demo.stage is before and demo.stage is not Stage.INTRO:
                        break  # a verdict blocked the flow; stop and report it
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
        quality = session.calibration_quality
        if quality is not None:
            print("\n--- calibration (doc 5-2) ---")
            print(json.dumps(quality.to_dict(), ensure_ascii=False, indent=2))
        if session.rejected_frames:
            print("\nrejected frames by reason:", dict(session.rejected_frames))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
