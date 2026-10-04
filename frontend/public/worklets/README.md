# AudioWorklet 은 평범한 JS로 여기 둡니다

번들러를 태우지 않습니다. `new AudioWorkletNode` 는 실행 시점에 URL로 파일을 받고,
그 파일이 모듈 그래프에 들어가면 경로가 해시로 바뀌어 런타임에 못 찾습니다.

서버로 가는 음성 경로 (로컬 녹음은 두지 않습니다 — CLAUDE.md 4번):

```
getUserMedia → AudioWorklet → Int16 16kHz 다운샘플 → WS
```

`MediaRecorder` 를 쓰지 않는 이유는 그 webm/opus 가 스트리밍 STT에 쓸 수 없어서입니다.
