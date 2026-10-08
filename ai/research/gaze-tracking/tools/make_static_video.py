"""테스트 얼굴 원본(정사각 얼굴 사진)을 노트북 웹캠 구도의 사진 · 정지 영상으로 만든다.

SFHQ 원본은 얼굴이 꽉 찬 1024×1024 사진이다. 그대로 쓰면 준비 점검이 "너무 가까움"으로 보고,
웹캠으로 찍은 장면과도 다르다. 그래서 원본을 줄여 4:3 화면 가운데에 두고, 남는 곳은 원본의
가장자리 픽셀을 늘린 뒤 흐려서 채운다 (원본 전체를 확대해 흐리면 뒤에 흐린 얼굴이 하나 더 생겨
두 번째 얼굴로 잡힐 수 있다). 경계는 부드럽게 섞어 가짜 윤곽이 생기지 않게 한다.

  face.png                 4:3 사진 (기본 1280×960, 무손실). 정지 사진 테스트가 쓴다
  static_face_30fps.mp4    그 사진을 640×480 · 30fps · 14초(420프레임)로 늘인 영상. 영상 테스트 · smoke 가 쓴다

같은 원본 · 같은 인자 · 같은 OpenCV 버전(uv.lock)이면 face.png 는 바이트 단위로 같다.

usage: uv run python tools/make_static_video.py SOURCE.jpg OUT_DIR
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

#: 원본 정사각 사진이 화면 높이에서 차지하는 비율. SFHQ 얼굴(이마~턱)은 원본 높이의 약 0.59 라,
#: 0.64 면 얼굴 높이가 화면의 약 0.38 이 된다 (55 cm 앞 노트북 웹캠 구도)
SQUARE_HEIGHT_RATIO = 0.64
#: 경계를 섞는 폭 (원본 한 변 대비)
FEATHER_RATIO = 0.12
BACKGROUND_BLUR_SIGMA = 25.0

PHOTO_SIZE = (1280, 960)
VIDEO_SIZE = (640, 480)
VIDEO_FPS = 30
VIDEO_FRAMES = 420


def _feather_mask(side: int, feather: int) -> np.ndarray:
    """가운데는 1, 가장자리 feather 픽셀 동안 0 까지 선형으로 줄어드는 정사각 마스크."""
    ramp = np.minimum(np.arange(side, dtype=np.float32) + 0.5, np.float32(feather)) / np.float32(
        feather
    )
    ramp = np.minimum(ramp, ramp[::-1])
    return np.minimum.outer(ramp, ramp)


def compose(source_bgr: np.ndarray, size: tuple[int, int] = PHOTO_SIZE) -> np.ndarray:
    """정사각 얼굴 사진 → 웹캠 구도의 BGR 화면 (width, height = size)."""
    width, height = size
    src_h, src_w = source_bgr.shape[:2]
    if src_h != src_w:
        raise ValueError(f"source must be square, got {src_w}x{src_h}")

    side = round(height * SQUARE_HEIGHT_RATIO)
    face = cv2.resize(source_bgr, (side, side), interpolation=cv2.INTER_AREA)
    y0, x0 = (height - side) // 2, (width - side) // 2

    padded = cv2.copyMakeBorder(
        face, y0, height - side - y0, x0, width - side - x0, cv2.BORDER_REPLICATE
    )
    background = cv2.GaussianBlur(padded, (0, 0), BACKGROUND_BLUR_SIGMA)

    mask = _feather_mask(side, max(1, round(side * FEATHER_RATIO)))[..., None]
    region = background[y0 : y0 + side, x0 : x0 + side].astype(np.float32)
    blended = face.astype(np.float32) * mask + region * (1.0 - mask)
    out = background.copy()
    out[y0 : y0 + side, x0 : x0 + side] = np.clip(np.rint(blended), 0, 255).astype(np.uint8)
    return out


def write_photo(source_bgr: np.ndarray, path: Path) -> None:
    if not cv2.imwrite(str(path), compose(source_bgr, PHOTO_SIZE)):
        raise OSError(f"could not write {path}")


def write_video(source_bgr: np.ndarray, path: Path) -> None:
    frame = compose(source_bgr, VIDEO_SIZE)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), VIDEO_FPS, VIDEO_SIZE)
    if not writer.isOpened():
        raise OSError(f"could not open a video writer for {path}")
    try:
        for _ in range(VIDEO_FRAMES):
            writer.write(frame)
    finally:
        writer.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("source", type=Path, help="정사각 얼굴 원본 (예: SFHQ jpg)")
    parser.add_argument("out_dir", type=Path)
    args = parser.parse_args(argv)

    source = cv2.imread(str(args.source), cv2.IMREAD_COLOR)
    if source is None:
        print(f"cannot read {args.source}", file=sys.stderr)
        return 1
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_photo(source, args.out_dir / "face.png")
    write_video(source, args.out_dir / "static_face_30fps.mp4")
    print(f"wrote {args.out_dir / 'face.png'} and {args.out_dir / 'static_face_30fps.mp4'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
