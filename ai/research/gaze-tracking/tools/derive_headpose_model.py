"""정면 얼굴 사진 한 장에서 고개 방향 PnP 모델 점 9개를 유도한다.

gaze_lab/preprocess/headpose.py 의 PNP_MODEL_POINTS 는 MediaPipe 자체 얼굴 기하에서 나온 값이다.
그 유도 방법을 코드로 남긴 것이다 (원래는 주석에만 있었다).

  1. MediaPipe 로 랜드마크(x, y 는 이미지 비율, z 는 이미지 너비 단위 · 머리 중심 0)와 변환 행렬을 얻는다
  2. 변환 행렬의 이동량(t, cm)으로 각 점의 깊이를 정한다: Z = t_z + z · W · t_z / f
  3. 핀홀 모델로 카메라 좌표로 올린다: X = (u − c_x) · Z / f, Y = (v − c_y) · Z / f
  4. 얼굴 좌표로 되돌린다: R_cvᵀ (P − t)    (정면 얼굴이면 R 이 단위 행렬에 가깝다)

f 는 headpose.focal_length_px (세로 화각 63°) 를 그대로 쓴다. 두 경로가 같은 가정을 써야
행렬 경로와 PnP 경로의 고개 각도 · 거리가 같아진다.

archive 의 테스트 사진에 돌리면 지금 상수와 소수 4자리까지 같다 (그 사진에서 유도했기 때문).
다른 얼굴에 돌린 결과는 지금 상수와의 차이를 보는 용도다. 상수를 바꾸면 판정이 바뀌므로
여러 얼굴로 유도하고 평가한 뒤 기능 버전을 올리는 별도 변경으로 다룬다.

usage: uv run python tools/derive_headpose_model.py IMAGE [--model artifacts/face_landmarker.task]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision

from gaze_lab.config import load_config, resolve_path
from gaze_lab.preprocess.headpose import (
    GL_TO_CV,
    PNP_LANDMARK_IDS,
    PNP_MODEL_POINTS,
    focal_length_px,
)


def detect(image_bgr: np.ndarray, model_path: Path) -> tuple[np.ndarray, np.ndarray]:
    """(랜드마크 (N, 3) — x, y 는 이미지 비율, z 는 너비 단위) 와 4×4 변환 행렬. 얼굴은 하나여야 한다."""
    options = vision.FaceLandmarkerOptions(
        base_options=mp_tasks.BaseOptions(model_asset_path=str(model_path)),
        running_mode=vision.RunningMode.IMAGE,
        num_faces=2,
        output_facial_transformation_matrixes=True,
    )
    rgb = np.ascontiguousarray(cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB))
    with vision.FaceLandmarker.create_from_options(options) as landmarker:
        result = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    if len(result.face_landmarks) != 1 or not result.facial_transformation_matrixes:
        raise ValueError(
            f"expected exactly one face with a matrix, got {len(result.face_landmarks)}"
        )
    landmarks = np.array([[p.x, p.y, p.z] for p in result.face_landmarks[0]], dtype=np.float64)
    return landmarks, np.asarray(result.facial_transformation_matrixes[0], dtype=np.float64)


def back_project(
    landmarks: np.ndarray, matrix: np.ndarray, image_size: tuple[int, int]
) -> np.ndarray:
    """PNP_LANDMARK_IDS 순서의 얼굴 좌표 (9, 3), 단위 cm."""
    width, height = image_size
    rotation = GL_TO_CV @ matrix[:3, :3] @ GL_TO_CV
    translation = GL_TO_CV @ matrix[:3, 3]
    focal = focal_length_px(image_size)
    cx, cy = width / 2.0, height / 2.0

    points = []
    for index in PNP_LANDMARK_IDS:
        x, y, z = landmarks[index]
        depth = translation[2] + z * width * translation[2] / focal
        camera = np.array(
            [(x * width - cx) * depth / focal, (y * height - cy) * depth / focal, depth]
        )
        points.append(rotation.T @ (camera - translation))
    return np.asarray(points)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("image", type=Path, help="정면을 보는 얼굴이 하나인 사진")
    parser.add_argument("--model", type=Path, default=None, help="기본: configs 의 모델 경로")
    args = parser.parse_args(argv)

    image = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    if image is None:
        print(f"cannot read {args.image}", file=sys.stderr)
        return 1
    model = args.model or resolve_path(load_config().preprocess.landmarker_model_path)
    landmarks, matrix = detect(image, model)
    height, width = image.shape[:2]
    derived = np.round(back_project(landmarks, matrix, (width, height)), 4)

    print(
        f"image {width}x{height}, matrix translation (GL, cm) {np.round(matrix[:3, 3], 2).tolist()}"
    )
    print("PNP_MODEL_POINTS = np.asarray(\n    [")
    for row in derived:
        print("        [{:.4f}, {:.4f}, {:.4f}],".format(*row))
    print("    ],\n    dtype=np.float64,\n)")
    diff = np.abs(derived - PNP_MODEL_POINTS)
    print(
        f"max |derived - current| = {diff.max():.4f} cm (per point: {np.round(diff.max(axis=1), 4).tolist()})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
