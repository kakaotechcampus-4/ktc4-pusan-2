"""tests/fixtures/static_face_30fps.mp4 -> .cache/static_face.y4m (Chrome's fake camera format)."""
from pathlib import Path

import cv2

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[1] / "tests" / "fixtures" / "static_face_30fps.mp4"
OUT = HERE.parent / ".cache" / "static_face.y4m"

cap = cv2.VideoCapture(str(SRC))
frames = []
while True:
    ok, frame = cap.read()
    if not ok:
        break
    frames.append(frame)
h, w = frames[0].shape[:2]
w2, h2 = w - (w % 2), h - (h % 2)
OUT.parent.mkdir(parents=True, exist_ok=True)
with OUT.open("wb") as fh:
    fh.write(f"YUV4MPEG2 W{w2} H{h2} F30:1 Ip A1:1 C420jpeg\n".encode())
    for frame in frames:
        fh.write(b"FRAME\n")
        fh.write(cv2.cvtColor(frame[:h2, :w2], cv2.COLOR_BGR2YUV_I420).tobytes())
print(f"wrote {OUT} ({len(frames)} frames, {w2}x{h2})")
