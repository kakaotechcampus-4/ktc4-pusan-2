# 모델 가중치는 여기에 둡니다

`public/models/` 에 두고 Cache API로 캐싱합니다. **CDN에서 받지 않습니다.**
시연장 와이파이가 느리면 발표가 안 됩니다.

들어올 파일 (AI팀에서 받음, W7 예정):

| 파일 | 무엇 |
| --- | --- |
| `face_landmarker.task` | MediaPipe Face Landmarker |
| `vision_wasm_internal.wasm` · `.js` | MediaPipe WASM 런타임 |
| `gaze-*.onnx` | 시선 백본 (I-03 미정) |

가중치는 커밋하지 않습니다 (`.gitignore`). 새로 세팅하는 사람은 팀 드라이브에서 받습니다.
