"""Frame decimation to the analysis rate (doc 3-1).

Sampling is driven by timestamps, never by a frame counter.  Webcam capture
drops frames under load and video containers carry variable frame durations, so
"take every Nth frame" would silently change the effective analysis FPS and with
it every duration the temporal layer reports (doc 6).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional, Tuple, Union

import cv2
import numpy as np

#: Timestamps arrive as integer milliseconds from OpenCV and as floats from
#: capture clocks; a deadline landing 0.4 ms late must not cost a whole frame.
_DEADLINE_TOLERANCE_MS = 0.5

#: Used only when a container reports no usable FPS.
_FALLBACK_FPS = 30.0


class FrameSampler:
    """Admit at most ``target_fps`` frames per second of stream time.

    Stateful on purpose: the caller feeds every captured frame and we answer
    yes/no, so the same object works for a webcam loop and for a file reader.
    """

    def __init__(self, target_fps: float) -> None:
        self.target_fps = float(target_fps)
        #: A non-positive rate disables decimation (every frame is analysed).
        self.interval_ms = 1000.0 / self.target_fps if self.target_fps > 0 else 0.0
        self._deadline_ms: Optional[float] = None
        self._last_accepted_ms: Optional[float] = None

    @property
    def last_accepted_ms(self) -> Optional[float]:
        return self._last_accepted_ms

    def should_process(self, t_ms: float) -> bool:
        """Return True when the frame at ``t_ms`` belongs to the analysis stream."""
        t = float(t_ms)
        if self.interval_ms <= 0.0:
            self._last_accepted_ms = t
            return True

        # A backwards jump means a new take (or a seek), not a late frame.
        if self._last_accepted_ms is not None and t < self._last_accepted_ms:
            self.reset()

        if self._deadline_ms is None:
            self._accept(t)
            return True

        if t + _DEADLINE_TOLERANCE_MS < self._deadline_ms:
            return False

        self._accept(t)
        return True

    def reset(self) -> None:
        self._deadline_ms = None
        self._last_accepted_ms = None

    def _accept(self, t: float) -> None:
        self._last_accepted_ms = t
        if self._deadline_ms is None:
            self._deadline_ms = t + self.interval_ms
            return
        # Advance on the ideal grid so rounding does not accumulate drift, but
        # resynchronise after a stall instead of firing a catch-up burst.
        self._deadline_ms += self.interval_ms
        if self._deadline_ms <= t:
            self._deadline_ms = t + self.interval_ms


def iter_video_frames(
    path: Union[str, Path],
    target_fps: float,
) -> Iterator[Tuple[int, int, np.ndarray]]:
    """Yield ``(frame_id, t_ms, bgr)`` for a recorded take, decimated by time.

    ``frame_id`` stays the *source* frame index so a sampled observation can be
    traced back to the raw video (doc 17 manifests); ``t_ms`` is the container
    timestamp, which is what the labels are expressed in (doc 4-2).
    """
    video_path = Path(path)
    if not video_path.exists():
        raise FileNotFoundError(f"video not found: {video_path}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV could not open video: {video_path}")

    sampler = FrameSampler(target_fps)
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    if not np.isfinite(fps) or fps <= 0:
        fps = _FALLBACK_FPS
    use_container_ts = True
    last_pos_ms = -1.0

    try:
        index = 0
        while True:
            # Read the position *before* grabbing: backends disagree on whether
            # POS_MSEC refers to the frame just read or the next one.
            pos_ms = float(cap.get(cv2.CAP_PROP_POS_MSEC))
            ok, frame = cap.read()
            if not ok or frame is None:
                break

            if use_container_ts and (
                not np.isfinite(pos_ms) or (index > 0 and pos_ms <= last_pos_ms)
            ):
                # Some codecs report a frozen or absent PTS; index timing is then
                # the only monotonic clock available.
                use_container_ts = False
            t_ms = pos_ms if use_container_ts else index * 1000.0 / fps
            last_pos_ms = pos_ms

            if sampler.should_process(t_ms):
                yield index, int(round(t_ms)), frame
            index += 1
    finally:
        cap.release()
