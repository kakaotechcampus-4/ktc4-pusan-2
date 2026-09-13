# evaluation-criteria

발표 연습 코칭 서비스 **Pitch Coach**의 평가 기준 분석 프로젝트입니다.

사용자가 자유 문장으로 작성한 발표 평가 기준을 입력받아, 현재 발표 분석 시스템이
**판단할 수 있는 기준(evaluable)**, **정보가 더 필요한 기준(need_more_info)**,
**판단하지 않는 기준(not_evaluable)** 세 가지로 분류한 구조화된 결과로 내보냅니다.

> 위치: `workspaces/seojin-lee/evaluation-criteria/`
> 워크스페이스 규칙(버전 축, `local`/`deploy`, 문서 범위)은 [../README.md](../README.md)에 있습니다.

---

## 폴더 구조

```
evaluation-criteria/
├── README.md          ← 이 문서: 프로젝트가 무엇이고 어디서 시작하나
│
└── v1/
    ├── README.md      # v1 모델의 전체 기술 레퍼런스 (local/deploy 공유)
    ├── local/         # 실험·평가 리그  ← 지금 코드는 전부 여기
    │   └── README.md
    └── deploy/        # 서비스 투입용 패키징 (아직 없음)
```

`v1`은 **모델 버전**이지 릴리스 버전이 아닙니다. 제품으로 나갈 때는
`releases/vX.Y.Z/evaluation-criteria/`에 묶이며, 두 축은 독립적으로 움직입니다
([워크스페이스 README](../README.md) 참고).

---

## 어디서 시작하나

| 하려는 일 | 위치 |
|---|---|
| **코드 돌려보기 · 개발하기** | [v1/local/README.md](v1/local/README.md) |
| 모델을 이해하기 · v1의 범위와 현재 상태 | [v1/README.md](v1/README.md) — 전체 기술 레퍼런스 |

---

## 다른 프로젝트가 이 프로젝트를 쓰는 법

소비자가 받는 것은 **세 개의 리스트를 가진 `EvaluationCriteriaAnalysis` JSON**입니다.
이 모양이 이 프로젝트의 계약이고, 모양이 바뀌면 모델 버전이 올라갑니다.

```json
{
  "evaluable": [
    { "key": "발표 시간", "value": "10분 이내" },
    { "key": "대본 사용", "value": true }
  ],
  "need_more_info": [
    { "key": "말하기 속도", "value": "너무 빠르지 않도록 해야 함" }
  ],
  "not_evaluable": ["발표 주제의 창의성", "질의응답"]
}
```

- `evaluable` — 현재 시스템이 실제로 측정·판단하는 요소(발표 시간, 말하기 속도, 발음, 억양,
  음량, 멈춤/침묵, 필러, 시선 처리, 대본 사용 여부·의존도, 대본-발화 의미적 유사도 등)이며
  구체적인 기준값까지 함께 주어진 경우
- `need_more_info` — 판단 가능한 요소이지만 입력에 구체적인 기준이 없는 경우. 임의로 수치를
  만들어내지 않고, 지금까지 확인된 값만 그대로 담습니다
- `not_evaluable` — 시스템이 애초에 판단하지 않는 요소 (발표 주제, 자료 신뢰도, 용모, 질의응답 등)

> ⚠️ **아직 전송 계층이 없습니다.** 지금은 노트북에서 `structured_output.invoke(...)`를
> 직접 호출하는 형태뿐이며, 서비스에 붙이는 형태는 `v1/deploy/`에서 정해질 예정입니다.

각 필드의 의미와 분류 규칙은 [v1/README.md](v1/README.md)를 보세요.

---

## 현재 상태

**v1 / local만 존재합니다.**

| | |
|---|---|
| ✅ | LLM structured output 기반 분류 파이프라인 동작 · 예시 입력 5건으로 수동 검증 |
| ⚠️ | **정량 평가셋·정확도 지표가 없어 분류 품질은 측정되지 않았습니다** |
| ⬜ | `v1/deploy` 미작성 |

가장 큰 공백은 평가셋입니다 — 그전까지 모든 분류 품질 수치는 미측정입니다.
"v1을 완료라고 부르려면 무엇이 필요한가"는 [v1/README.md](v1/README.md)에 있습니다.
