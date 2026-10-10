"""git 에 올리지 않는 로컬 자산을 받는다. 처음 한 번, 그리고 manifest 가 바뀌었을 때 실행한다.

  artifacts/face_landmarker.task                MediaPipe 얼굴 랜드마크 모델   (artifacts/face_landmarker.task.json)
  tests/fixtures/local/<원본>.jpg               테스트 얼굴 원본: SFHQ 합성 얼굴 (tests/fixtures/manifest.json)
  tests/fixtures/local/face.png                 원본을 웹캠 구도로 맞춘 사진   (tools/make_static_video.py)
  tests/fixtures/local/static_face_30fps.mp4    그 사진의 정지 영상

- 이미 있고 sha256 이 manifest 와 같으면 다시 받지 않는다.
- 받은 파일의 크기 · sha256 이 다르면 지우고 실패한다 (다른 모델 · 다른 사진으로 테스트가 도는 것을 막는다).
- SFHQ 는 공개 CC0 데이터셋이라 Kaggle 토큰 없이 받을 수 있다. 환경변수 KAGGLE_API_TOKEN 이 있으면 함께 보낸다.
- 네트워크를 쓸 수 없으면 직접 받은 파일을 --model-file / --face-file 로 넘긴다 (해시는 똑같이 확인한다).

usage: uv run python tools/fetch_assets.py [--model-file PATH] [--face-file PATH] [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

import cv2

import make_static_video

PROJECT = Path(__file__).resolve().parents[1]
MODEL_MANIFEST = PROJECT / "artifacts" / "face_landmarker.task.json"
FIXTURE_MANIFEST = PROJECT / "tests" / "fixtures" / "manifest.json"
FIXTURE_DIR = PROJECT / "tests" / "fixtures" / "local"
TIMEOUT_S = 60


class AssetError(RuntimeError):
    pass


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_valid(path: Path, sha256: str) -> bool:
    return path.is_file() and sha256_of(path) == sha256


def _video_ok(path: Path) -> bool:
    """정지 영상이 끝까지 만들어졌는지: 프레임 수 · 크기 · fps 와 첫 프레임 디코딩."""
    if not path.is_file():
        return False
    capture = cv2.VideoCapture(str(path))
    try:
        size = (
            int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        )
        return (
            int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) == make_static_video.VIDEO_FRAMES
            and size == make_static_video.VIDEO_SIZE
            and round(capture.get(cv2.CAP_PROP_FPS)) == make_static_video.VIDEO_FPS
            and capture.read()[0]
        )
    finally:
        capture.release()


def _temp_path(dest: Path, suffix: str = ".part") -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.stem}.", suffix=suffix)
    os.close(fd)
    return Path(name)


def _install(tmp: Path, dest: Path, size: int, sha256: str) -> None:
    """크기 · 해시를 확인한 뒤에만 제자리에 둔다. 다르면 실패한다 (임시 파일은 호출한 쪽이 지운다)."""
    actual_size, actual_sha = tmp.stat().st_size, sha256_of(tmp)
    if actual_size != size or actual_sha != sha256:
        raise AssetError(
            f"{dest.name}: expected {size} B sha256 {sha256[:12]}…, "
            f"got {actual_size} B sha256 {actual_sha[:12]}…"
        )
    os.replace(tmp, dest)


def _download(url: str, dest: Path, size: int, sha256: str, headers: dict[str, str]) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "gaze-tracking-fetch", **headers})
    tmp = _temp_path(dest)
    try:
        try:
            with tmp.open("wb") as out, urllib.request.urlopen(request, timeout=TIMEOUT_S) as resp:
                shutil.copyfileobj(resp, out)
        except (OSError, http.client.HTTPException) as exc:
            raise AssetError(
                f"{dest.name}: download failed ({exc}). Use --model-file / --face-file."
            ) from exc
        _install(tmp, dest, size, sha256)
    finally:
        tmp.unlink(missing_ok=True)


def _copy(src: Path, dest: Path, size: int, sha256: str) -> None:
    tmp = _temp_path(dest)
    try:
        try:
            shutil.copyfile(src, tmp)
        except OSError as exc:
            raise AssetError(f"{dest.name}: cannot copy {src} ({exc})") from exc
        _install(tmp, dest, size, sha256)
    finally:
        tmp.unlink(missing_ok=True)


def fetch(
    entry: dict, dest: Path, local_file: Path | None, force: bool, headers: dict[str, str]
) -> str:
    if not force and local_file is None and _is_valid(dest, entry["sha256"]):
        return "ok"
    if local_file is not None:
        _copy(local_file, dest, entry["size_bytes"], entry["sha256"])
        return "copied"
    _download(entry["url"], dest, entry["size_bytes"], entry["sha256"], headers)
    return "downloaded"


def build_fixtures(source: Path, manifest: dict, force: bool) -> str:
    """원본 → face.png · 정지 영상.

    둘 다 임시 파일에 만들고 확인한 뒤에만 제자리에 둔다. face.png 는 무손실이라 해시로 같은 사진인지,
    영상은 프레임 수 · 크기 · fps 로 끝까지 만들어졌는지 본다. 중간에 끊겨도 반쯤 만든 파일이 남지 않는다.
    """
    photo = FIXTURE_DIR / manifest["photo"]["file"]
    video = FIXTURE_DIR / manifest["video"]["file"]
    if not force and _is_valid(photo, manifest["photo"]["sha256"]) and _video_ok(video):
        return "ok"
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None:
        raise AssetError(f"cannot decode {source}")

    tmp_photo, tmp_video = _temp_path(photo, ".png"), _temp_path(video, ".mp4")
    try:
        make_static_video.write_photo(image, tmp_photo)
        make_static_video.write_video(image, tmp_video)
        actual = sha256_of(tmp_photo)
        if actual != manifest["photo"]["sha256"]:
            raise AssetError(
                f"{photo.name}: built sha256 {actual[:12]}… differs from the manifest "
                f"{manifest['photo']['sha256'][:12]}… — tools/make_static_video.py or OpenCV changed. "
                "If that is intended, update tests/fixtures/manifest.json and re-measure the tests that read it."
            )
        if not _video_ok(tmp_video):
            raise AssetError(f"{video.name}: the built video is incomplete or unreadable")
        os.replace(tmp_photo, photo)
        os.replace(tmp_video, video)
    except OSError as exc:
        raise AssetError(f"cannot build the face fixtures ({exc})") from exc
    finally:
        tmp_photo.unlink(missing_ok=True)
        tmp_video.unlink(missing_ok=True)
    return "built"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model-file", type=Path, help="직접 받은 face_landmarker.task")
    parser.add_argument("--face-file", type=Path, help="직접 받은 SFHQ 원본 jpg")
    parser.add_argument("--force", action="store_true", help="있어도 다시 받고 다시 만든다")
    args = parser.parse_args(argv)

    headers = {}
    if token := os.environ.get("KAGGLE_API_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"

    model = json.loads(MODEL_MANIFEST.read_text(encoding="utf-8"))
    fixtures = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    source = FIXTURE_DIR / fixtures["source"]["file"]
    try:
        steps = [
            (
                "model",
                fetch(
                    model, PROJECT / "artifacts" / model["name"], args.model_file, args.force, {}
                ),
            ),
            ("face source", fetch(fixtures["source"], source, args.face_file, args.force, headers)),
            ("face photo + video", build_fixtures(source, fixtures, args.force)),
        ]
    except AssetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for name, status in steps:
        print(f"{name:20s} {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
