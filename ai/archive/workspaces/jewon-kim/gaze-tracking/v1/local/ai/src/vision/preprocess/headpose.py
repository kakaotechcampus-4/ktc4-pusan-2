"""Head orientation in the ``schemas.py`` sign convention (doc 3-1).

Two independent estimators, one output convention:

* ``head_pose_from_matrix`` -- MediaPipe's facial transformation matrix, which
  is what we use whenever the task produces one.
* ``head_pose_from_landmarks`` -- a ``solvePnP`` fallback on nine rigid mesh
  points, for the rare frames where the matrix is missing.

Frames involved
---------------
MediaPipe's metric space is OpenGL-style: ``+x`` image right, ``+y`` UP,
``+z`` toward the viewer, camera at the origin.  ``schemas.py`` (and OpenCV) use
``+y`` DOWN and ``+z`` into the scene.  The change of basis is therefore
``S = diag(1, -1, -1)`` applied on both sides: ``R_cv = S @ R_gl @ S``.  On
``tests/fixtures/face.jpg`` the raw translation is ``(-0.41, +22.46, -65.45)``,
i.e. a face 65 cm in front of the camera and above its axis -- consistent with
that reading of the axes, and with the nose landmark sitting in the upper part
of the portrait.

Sign derivation (why the extraction below is not the textbook one)
------------------------------------------------------------------
Columns of ``R_cv`` are the face model's axes in camera coordinates, and for a
frontal face ``R_cv`` is the identity, so model ``+X`` = image right, ``+Y`` =
image down, ``+Z`` = away from the camera (out the back of the head).  Hence the
nose direction is ``f = -R_cv[:, 2]`` and the head's up direction is
``u = -R_cv[:, 1]``.

* Chin up means the nose tilts up, i.e. ``f_y < 0`` (image y grows downward).
  A positive rotation about the camera's ``+x`` axis gives ``f_y = +sin(a)``,
  so chin up is a NEGATIVE ``a`` and ``head_pitch = -a``.
* Face turned toward the image right means ``f_x > 0``; a positive rotation
  about ``+y`` gives ``f_x = -sin(b)``, so ``head_yaw = -b``.
* A positive rotation about ``+z`` sends the head's up vector toward the image
  right (``u_x = +sin(c)``), which is the clockwise in-image tilt that
  ``schemas.py`` calls a positive roll, so ``head_roll = +c``.
"""

from __future__ import annotations

import math
from typing import Optional, Tuple

import cv2
import numpy as np

from ..schemas import HeadPose
from .crops import (FACE_SIDE_LEFT, FACE_SIDE_RIGHT, FOREHEAD, LEFT_EYE_INNER, LEFT_EYE_OUTER,
                    NOSE_BRIDGE, NOSE_TIP, RIGHT_EYE_INNER, RIGHT_EYE_OUTER)

#: OpenGL(Y up, Z to viewer) <-> OpenCV(Y down, Z into scene).  Self-inverse.
GL_TO_CV = np.diag([1.0, -1.0, -1.0])

#: Vertical field of view MediaPipe's face-geometry module assumes when it puts
#: the metric landmarks in front of the camera.  Verified on the fixture:
#: projecting the matrix translation with this focal length lands within 0.3 px
#: of the landmark bounding-box centre, which 60 deg misses by ~17 px.
#: Used by BOTH estimators so their ``depth_proxy`` values are comparable.
DEFAULT_VERTICAL_FOV_DEG = 63.0

#: Below this the rotation is near gimbal lock (head turned ~90 deg) and roll
#: cannot be separated from pitch.
_GIMBAL_EPS = 1e-6

#: Rigid head model in centimetres, expressed in the OpenCV-aligned face frame
#: (``+x`` image right, ``+y`` down, ``+z`` away from the camera) with the
#: origin on MediaPipe's own metric-space head centre, so a frontal face solves
#: to the identity rotation and ``tvec[2]`` means the same distance the matrix
#: path reports.
#:
#: The points are MediaPipe's canonical geometry, not a textbook anthropometric
#: table: they were back-projected from ``tests/fixtures/face.jpg`` as
#: ``R_cv^T (P_cv - t_cv)``, taking each landmark's depth from the mesh's own
#: ``z`` channel (width-normalised, head centre at 0) and the metric scale from
#: the transformation matrix.  ``ai/tools`` has no need to re-derive them, but
#: the recipe is: solve the two paths on one near-frontal face and require them
#: to agree.  The classic OpenCV 6-point model was tried first and rejected --
#: its chin sits 7.0 cm below the nose where MediaPipe puts 9.1 cm, which shows
#: up as a constant -19 deg pitch bias and an 11 px reprojection residual.
#:
#: Only expression-stable points are used: eye corners, nose bridge and tip,
#: forehead and the temples.  Mouth and chin points move several centimetres
#: while a person speaks, which is most of doc 4-1's protocol.
PNP_LANDMARK_IDS: Tuple[int, ...] = (
    NOSE_TIP,
    NOSE_BRIDGE,
    FOREHEAD,
    RIGHT_EYE_OUTER,
    LEFT_EYE_OUTER,
    RIGHT_EYE_INNER,
    LEFT_EYE_INNER,
    FACE_SIDE_RIGHT,
    FACE_SIDE_LEFT,
)
PNP_MODEL_POINTS = np.asarray(
    [
        [0.0512, 1.5818, -4.2221],
        [0.1589, -3.7952, -1.1034],
        [0.2138, -9.4695, 0.2987],
        [-4.4244, -4.1455, 1.5249],
        [4.8945, -4.2610, 1.5852],
        [-1.6582, -3.8961, 0.9662],
        [2.0551, -3.9470, 1.0084],
        [-8.0902, -3.6247, 7.2830],
        [8.3375, -3.7099, 7.3076],
    ],
    dtype=np.float64,
)


def focal_length_px(image_size: Optional[Tuple[int, int]]) -> float:
    """Assumed pinhole focal length in pixels for a ``(width, height)`` frame.

    We never know the real intrinsics of a user's webcam, so both estimators
    share this one assumption; a wrong focal length scales the angles slightly
    but does not flip any sign.  With ``image_size=None`` the focal is expressed
    in image heights, i.e. it is ``height`` times SMALLER than the pixel-based
    value -- so the ``depth_proxy`` divided by it comes out ``height`` times
    LARGER, not smaller.  (The docstring used to state that backwards.)  Two
    ``depth_proxy`` values are therefore only comparable when both were taken
    with an ``image_size``, which the pipeline always passes.
    """
    height = float(image_size[1]) if image_size else 1.0
    return (height / 2.0) / math.tan(math.radians(DEFAULT_VERTICAL_FOV_DEG) / 2.0)


def camera_matrix(image_size: Tuple[int, int]) -> np.ndarray:
    """Pinhole intrinsics with the principal point at the image centre."""
    width, height = float(image_size[0]), float(image_size[1])
    f = focal_length_px(image_size)
    return np.asarray(
        [[f, 0.0, width / 2.0], [0.0, f, height / 2.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )


def euler_from_camera_rotation(rotation: np.ndarray) -> Tuple[float, float, float]:
    """``(yaw, pitch, roll)`` radians from a face->camera OpenCV rotation.

    Decomposes ``R = Rz(c) Ry(b) Rx(a)`` and applies the sign mapping derived in
    the module docstring: ``yaw = -b``, ``pitch = -a``, ``roll = c``.
    """
    r = np.asarray(rotation, dtype=np.float64).reshape(3, 3)
    cos_b = math.sqrt(float(r[0, 0]) ** 2 + float(r[1, 0]) ** 2)
    if cos_b > _GIMBAL_EPS:
        a = math.atan2(float(r[2, 1]), float(r[2, 2]))
        c = math.atan2(float(r[1, 0]), float(r[0, 0]))
    else:
        # Gimbal lock.  At b = +90 deg the Rx and Rz rotations act in the same
        # plane and only (a - c) survives; at b = -90 deg only (a + c) does.
        # Either way one combination is observable, and atan2 below recovers it
        # and attributes it entirely to pitch, with roll pinned to zero, rather
        # than splitting it between the two arbitrarily.  (An earlier comment
        # here claimed (a + c) for both branches; measure it before believing
        # that -- at b = +90 the extractor returns pitch = -(a - c).)
        a = math.atan2(-float(r[1, 2]), float(r[1, 1]))
        c = 0.0
    b = math.atan2(-float(r[2, 0]), cos_b)
    return -b, -a, c


def head_pose_from_matrix(
    matrix: np.ndarray,
    image_size: Optional[Tuple[int, int]] = None,
) -> HeadPose:
    """Head pose from MediaPipe's 4x4 facial transformation matrix (doc 3-1).

    ``reprojection_error`` stays 0.0: this path never solves a projection, so
    there is no residual to report.  ``image_size`` is optional only so the
    function can be unit-tested on a bare matrix; the pipeline always passes it.
    """
    m = np.asarray(matrix, dtype=np.float64).reshape(4, 4)
    rotation_cv = GL_TO_CV @ m[:3, :3] @ GL_TO_CV
    yaw, pitch, roll = euler_from_camera_rotation(rotation_cv)

    # Translation is in the same GL frame: the camera looks down -z, so the
    # distance to the face is -t_z (positive in front of the camera).
    depth_cm = -float(m[2, 3])
    return HeadPose(
        yaw=yaw,
        pitch=pitch,
        roll=roll,
        reprojection_error=0.0,
        depth_proxy=depth_cm / focal_length_px(image_size),
    )


def head_pose_from_landmarks(
    landmarks_px: np.ndarray,
    image_size: Tuple[int, int],
) -> HeadPose:
    """solvePnP fallback for frames without a transformation matrix (doc 3-1).

    ``landmarks_px`` are pixel coordinates, ``(N, 2)`` or ``(N, 3)``; only the
    ``PNP_LANDMARK_IDS`` subset is used.  A failed solve returns a HeadPose
    rather than raising, so one bad frame cannot kill a take -- but it returns
    ``reprojection_error=inf`` and ``depth_proxy=nan``, NOT the dataclass
    defaults.  A zeroed HeadPose is byte-identical to an ideal frontal face at
    zero distance, so it landed in the feature table as if it were a
    measurement; inf/nan say "no estimate" in the two columns that carry the
    solve's own quality, and the angles stay at 0.0 because there is no
    orientation to report either way.  Downstream already copes: doc 19's
    lean-back bucket keeps only ``depth_proxy > 0`` and the evaluation reader
    substitutes its default for any non-finite cell.
    """
    points = np.asarray(landmarks_px, dtype=np.float64)
    if points.ndim != 2 or points.shape[0] <= max(PNP_LANDMARK_IDS):
        raise ValueError(
            "expected at least {} landmarks, got shape {}".format(
                max(PNP_LANDMARK_IDS) + 1, points.shape
            )
        )
    image_points = np.ascontiguousarray(points[list(PNP_LANDMARK_IDS), :2])

    intrinsics = camera_matrix(image_size)
    no_distortion = np.zeros((4, 1), dtype=np.float64)
    # SQPNP finds the global optimum directly; ITERATIVE would need an
    # extrinsic guess to avoid the mirrored local minimum on a near-frontal face.
    ok, rvec, tvec = cv2.solvePnP(
        PNP_MODEL_POINTS,
        image_points,
        intrinsics,
        no_distortion,
        flags=cv2.SOLVEPNP_SQPNP,
    )
    if not ok:
        return HeadPose(reprojection_error=math.inf, depth_proxy=math.nan)

    rotation, _ = cv2.Rodrigues(rvec)
    yaw, pitch, roll = euler_from_camera_rotation(rotation)

    projected, _ = cv2.projectPoints(
        PNP_MODEL_POINTS, rvec, tvec, intrinsics, no_distortion
    )
    error = float(
        np.mean(np.linalg.norm(projected.reshape(-1, 2) - image_points, axis=1))
    )
    return HeadPose(
        yaw=yaw,
        pitch=pitch,
        roll=roll,
        reprojection_error=error,
        depth_proxy=float(tvec[2, 0]) / focal_length_px(image_size),
    )
