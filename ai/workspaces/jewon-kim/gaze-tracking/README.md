# gaze-tracking

발표 연습 코칭 서비스 **Pitch Coach**의 시선 추적 컴퓨터비전 프로젝트입니다.

웹캠 영상에서 발표자의 시선이 **카메라(CAMERA)**, **화면(SCREEN)**, **화면 아래 대본(BOTTOM)**,
**그 밖(OTHER)** 중 어디를 향하는지 프레임 단위로 판정하고, 안정화된 `GAZE_STATE` 이벤트 스트림으로 내보냅니다.
촬영 조건(다른 사람 등장, 거리·자세 변화 등)이 얼마나 믿을 만한지는 `SESSION_CONDITION` 스트림으로 따로 알립니다.
그 밖(OTHER)을 보면 어느 쪽인지(발표자 기준 8방향)도 붙이고, 판정을 1초 기록으로 모아 코치·리뷰 에이전트가 읽는
값(시선 이슈, 테이크 요약, 피드백 효과)으로 냅니다.

카메라 영상만 쓰고 아무것도 저장하지 않습니다. 매 세션 시작 때 준비 점검(한 명, 가운데, 적당한 거리)을 하고,
고개를 천천히 돌려 얼굴 주위 원을 채우는 확인(어느 방향에서도 얼굴을 따라가는지)을 거쳐,
편한 자세로 렌즈 → 화면 중앙 → 대본을 보는 짧은 보정을 거칩니다. 판정은 **고개가 향한 방향**으로 합니다
(눈 기반 시선 추정은 노트북 웹캠에서 세로 눈 움직임을 거의 읽지 못해 비교 실험용으로 보관 중입니다).

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
  "model_version": "gaze_v1.1.0",
  "t_ms": 12480,
  "label": "BOTTOM",
  "confidence": 0.8134,
  "continuous_duration_ms": 1750,
  "face_valid": true
}
```

`label`은 `CAMERA` / `SCREEN` / `BOTTOM` / `OTHER` / `UNCERTAIN`(판정 보류) 중 하나입니다.
v1.0(`gaze_v1.0.0`)은 `CAMERA` / `BOTTOM` / `UNCERTAIN`만 내보냈습니다 — 키는 그대로이고 값만 늘었습니다.

신뢰도는 별도 이벤트입니다. 조건이 바뀔 때와 주기적으로 나옵니다.

```json
{
  "type": "SESSION_CONDITION",
  "t_ms": 12500,
  "reliability": 0.62,
  "issues": ["TOO_FAR", "SECOND_FACE"]
}
```

`reliability`는 0~1이고, `issues`는 왜 낮아졌는지입니다.

> ⚠️ **아직 전송 계층이 없습니다.** 이벤트를 프로세스 밖으로 내보내는 서버 코드가 없어서
> 지금은 소비자가 `VisionSession`을 인프로세스로 임베드하는 방법뿐입니다.
> 서비스에 붙이는 형태는 `v1/deploy/`에서 정해질 예정입니다.

코치·리뷰 에이전트에는 이벤트 대신 **에이전트 증거**를 줍니다. 1초 기록과, 그것을 모은 공통 평가기 형식의 이슈입니다.

```json
{"evaluator": "gaze", "issue_type": "GAZE_AWAY", "t_ms": 17000, "severity": 0.375, "confidence": 0.9472,
 "persistence_sec": 3.0, "evidence": {"continuous_ms": 3000, "direction": "UP_LEFT", "...": "..."}, "actionable": true}
```

각 필드의 의미는 [v1/README.md](v1/README.md)의 §2, `issues` 값의 목록과 기준은 §7-8, 에이전트 증거의 규칙은 §7-11을 보세요.

---

## 현재 상태

**v1 / local만 존재합니다.**

| | |
|---|---|
| ✅ | v1.1 파이프라인 전 구간 동작 (준비 점검 → 3점 보정 → 고개 방향으로 5라벨 + 신뢰도) · 1752개 테스트 통과 · 지연 예산 충족 (p95 20 ms / 125 ms) |
| ✅ | OTHER 방향(8방향)과 코치·리뷰 에이전트용 시선 증거 (Python · 브라우저 같은 값) |
| ✅ | 브라우저 엔진 준비 — 프론트엔드 Worker 계약(`GazeClassifier`)에 맞춘 TS 모듈, 프론트엔드가 준 상자 안을 그리는 카메라 화면 모듈(`src/camera`), 로컬 웹 데모 (`v1/local/web`) |
| ⬜ | 프론트엔드 연결 — 붙이는 절차와 FE 팀과 정할 것은 [v1/local/web/README.md](v1/local/web/README.md) |
| ⚠️ | **실제 참가자 녹화본이 없어 모델 정확도는 측정되지 않았습니다** |
| ⚠️ | 준비 점검·고개 원·신뢰도·보정·에이전트 증거의 수치는 실측 전 초기값입니다 |
| ⬜ | `v1/deploy` 미작성 |

가장 큰 공백은 녹화입니다 — 그전까지 모든 정확도 수치는 미측정입니다.
"v1을 완료라고 부르려면 무엇이 필요한가"는 [v1/README.md](v1/README.md)에 있습니다.
