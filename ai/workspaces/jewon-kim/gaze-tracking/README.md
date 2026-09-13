# gaze-tracking

발표 연습 코칭 서비스 **Pitch Coach**의 시선 추적 컴퓨터비전 프로젝트입니다.

웹캠 영상에서 발표자의 시선이 **카메라(CAMERA)** 를 향하는지 **화면 아래 대본(BOTTOM)** 을 향하는지
프레임 단위로 판정하고, 안정화된 `GAZE_STATE` 이벤트 스트림으로 내보냅니다.

> 위치: `workspaces/jewon-kim/gaze-tracking/`
> 워크스페이스 규칙(버전 축, `local`/`deploy`, 문서 범위)은 [../README.md](../README.md)에 있습니다.

---

## 폴더 구조

```
gaze-tracking/
├── README.md          ← 이 문서: 프로젝트가 무엇이고 어디서 시작하나
├── .gitattributes     ← 줄바꿈 정규화 (루트에 같은 설정이 있으면 지워도 됩니다)
│
└── v1/
    ├── README.md      # v1 모델의 전체 기술 레퍼런스 (local/deploy 공유)
    ├── local/         # 실험·평가 리그  ← 지금 코드는 전부 여기
    │   └── README.md
    └── deploy/        # 서비스 투입용 패키징 (아직 없음)
```

`v1`은 **모델 버전**이지 릴리스 버전이 아닙니다. 제품으로 나갈 때는
`releases/vX.Y.Z/gaze-tracking/`에 묶이며, 두 축은 독립적으로 움직입니다
([워크스페이스 README](../README.md) 참고).

---

## 어디서 시작하나

| 하려는 일 | 위치 |
|---|---|
| **코드 돌려보기 · 개발하기** | [v1/local/README.md](v1/local/README.md) |
| 모델을 이해하기 · v1의 범위와 현재 상태 | [v1/README.md](v1/README.md) — 전체 기술 레퍼런스 |

---

## 다른 프로젝트가 이 프로젝트를 쓰는 법

소비자가 받는 것은 **정확히 7개 키를 가진 `GAZE_STATE` JSON**입니다.
이 모양이 이 프로젝트의 계약이고, 모양이 바뀌면 모델 버전이 올라갑니다.

```json
{
  "type": "GAZE_STATE",
  "model_version": "gaze_v1.0.0",
  "t_ms": 12480,
  "label": "BOTTOM",
  "confidence": 0.8134,
  "continuous_duration_ms": 1750,
  "face_valid": true
}
```

> ⚠️ **아직 전송 계층이 없습니다.** 이벤트를 프로세스 밖으로 내보내는 서버 코드가 없어서
> 지금은 소비자가 `VisionSession`을 인프로세스로 임베드하는 방법뿐입니다.
> 서비스에 붙이는 형태는 `v1/deploy/`에서 정해질 예정입니다.

각 필드의 의미는 [v1/README.md](v1/README.md)의 §2를 보세요.

---

## 현재 상태

**v1 / local만 존재합니다.**

| | |
|---|---|
| ✅ | 파이프라인 전 구간 동작 · 1421개 테스트 통과 · 지연 예산 충족 |
| ⚠️ | **실제 참가자 녹화본이 없어 모델 정확도는 측정되지 않았습니다** |
| ⬜ | `v1/deploy` 미작성 |

가장 큰 공백은 녹화입니다 — 그전까지 모든 정확도 수치는 미측정입니다.
"v1을 완료라고 부르려면 무엇이 필요한가"는 [v1/README.md](v1/README.md)에 있습니다.
