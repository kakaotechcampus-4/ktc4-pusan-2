# volume-analysis

발표 연습 코칭 서비스 **Pitch Coach**의 발표 음량 분석 프로젝트입니다.

사전 캘리브레이션에서 얻은 **주변 소음(`noise_db`)**과 **평소 목소리 음량(`baseline_voice_db`)**,
발표 중 측정된 **현재 음량(`current_db`)**을 입력받아, 음량이 적절한지 판단하기 위한
지표를 계산하고 **작음/보통/큼**으로 판정합니다.

> 위치: `workspaces/seojin-lee/volume-analysis/`
> 워크스페이스 규칙(버전 축, `local`/`deploy`, 문서 범위)은 [../README.md](../README.md)에 있습니다.

---

## 폴더 구조

```
volume-analysis/
├── README.md          ← 이 문서: 프로젝트가 무엇이고 어디서 시작하나
│
└── v1/
    ├── README.md      # v1 모델의 전체 기술 레퍼런스 (local/deploy 공유)
    ├── local/         # 실험·평가 리그  ← 지금 코드는 전부 여기
    │   └── README.md
    └── deploy/        # 서비스 투입용 패키징 (아직 없음)
```

`v1`은 **모델 버전**이지 릴리스 버전이 아닙니다. 제품으로 나갈 때는
`releases/vX.Y.Z/volume-analysis/`에 묶이며, 두 축은 독립적으로 움직입니다
([워크스페이스 README](../README.md) 참고).

---

## 어디서 시작하나

| 하려는 일 | 위치 |
|---|---|
| **코드 돌려보기 · 개발하기** | [v1/local/README.md](v1/local/README.md) |
| 모델을 이해하기 · v1의 범위와 현재 상태 | [v1/README.md](v1/README.md) — 전체 기술 레퍼런스 |

---

## 다른 프로젝트가 이 프로젝트를 쓰는 법

지금 정의된 출력은 **세 가지 음량 지표를 가진 `VolumeMeasurement` JSON**입니다.

```json
{
  "current_db": 58.0,
  "relative_db": 13.0,
  "baseline_difference_db": -7.0
}
```

- `current_db` — 발표 중 현재 측정된 음량(dB)
- `relative_db` — `current_db - noise_db`. 주변 소음 대비 얼마나 크게 말하고 있는가
- `baseline_difference_db` — `current_db - baseline_voice_db`. 평소 목소리 대비 얼마나 커졌/작아졌는가

이 지표로 두 가지 판정을 따로 냅니다.

- **절대 음량 판정** — `current_db`를 `ABSOLUTE_LOW=60`, `ABSOLUTE_HIGH=75`와 비교해 `"low" | "normal" | "high"`
- **상대 음량 판정** — `relative_db`를 `RELATIVE_LOW=10`과 비교해 `"low" | "normal"`

> ⚠️ **최종 판정 계약은 아직 확정되지 않았습니다.** 어떤 지표를 쓰고 두 판정을 어떻게 합칠지,
> 임계값을 얼마로 할지는 **FE 측과 dB 수치(측정 방식·스케일)를 논의한 뒤** 정할 예정입니다.
> 그전까지 위 모양은 실험용이며 바뀔 수 있습니다.
>
> ⚠️ **입력은 이미 측정된 dB 값입니다.** 이 프로젝트는 오디오에서 dB를 뽑지 않고,
> 클라이언트(FE)가 측정한 값이 주어졌다고 가정합니다.
>
> ⚠️ **아직 전송 계층이 없습니다.** 지금은 노트북에서 예시 값으로 셀을 직접 실행하는 형태뿐이며,
> 서비스에 붙이는 형태는 `v1/deploy/`에서 정해질 예정입니다.

각 필드의 의미와 계산 방식은 [v1/README.md](v1/README.md)를 보세요.

---

## 현재 상태

**v1 / local만 존재합니다.**

| | |
|---|---|
| ✅ | 캘리브레이션·측정 스키마 정의 · 지표 계산 · 절대/상대 임계값 판정 함수 동작 · 예시 값으로 수동 확인 |
| ⚠️ | **임계값과 최종 판단 로직이 확정되지 않았습니다** — 여러 지표를 비교하며 평가 방법을 찾는 단계 |
| ⚠️ | **평가셋이 없어 판정 정확도는 측정되지 않았습니다** |
| ⬜ | FE와 dB 수치 합의 · `v1/deploy` 미작성 |

가장 큰 공백은 **FE와의 dB 수치 합의**와 **평가셋**입니다 — 그전까지 임계값과 판정 정확도는 모두 미정입니다.
"v1을 완료라고 부르려면 무엇이 필요한가"는 [v1/README.md](v1/README.md)에 있습니다.
