# coach-agent

발표 연습 코칭 서비스 **Pitch Coach** 의 **실시간 코치 에이전트**입니다.

발표 중 1초마다 시선 · 말 속도 · 음량 · 침묵 · 군더더기 · 시간(장별 계획 대비)을 보고
**지금 말을 걸지, 건다면 무엇 하나를 말할지** 정합니다. 말한 뒤에는 실제로 행동이 바뀌었는지 보고
같은 방법을 유지할지, 다른 방법을 쓸지, 그만둘지를 고릅니다. 판단 기록은 Take 가 끝나면 리뷰 에이전트의 근거가 됩니다 —
장별 표 · 문제 순위 · 이전 미션 판정 · 이전 Take 비교 · 다음 미션 후보를 코드로 계산해 넘기고, 그 품질을 정답이 있는
가상 발표로 채점해 규칙을 골랐습니다.

> 위치: `workspaces/jewon-kim/coach-agent/`
> 워크스페이스 규칙(버전 축, `local`/`deploy`, 문서 범위)은 [../README.md](../README.md) 에 있습니다.
> 설계 배경은 팀 문서 「피치코치 — Agent 아키텍처 설계 (Coach / Review / Dialogue)」의 Coach Agent 입니다.

---

## 폴더 구조

```
coach-agent/
├── README.md          ← 이 문서: 프로젝트가 무엇이고 어디서 시작하나
└── v1/
    ├── README.md      # v1 전체 기술 레퍼런스 — 입출력 계약, 판단 규칙, 리뷰 연동, 배포
    └── local/         # 개발 · 재생 리그  ← 지금 코드는 전부 여기
        └── README.md
```

배포는 `v1/deploy/` 대신 코어 폴더(`local/ai/src/coach/`)를 `ai/releases/` 로 옮겨서 합니다 —
Docker 가이드상 배포 단위가 `ai/releases/` 하나이기 때문입니다 ([v1/README.md §8](v1/README.md#8-배포--aireleases-로-승격)).

---

## 어디서 시작하나

| 하려는 일 | 위치 |
|---|---|
| **코드 돌려보기 · 시나리오 재생 · 테스트** | [v1/local/README.md](v1/local/README.md) |
| 무엇을 받고 무엇을 주는지, 어떤 규칙으로 판단하는지 | [v1/README.md](v1/README.md) |
| 리뷰 에이전트에 무엇을 넘기는지, 실험 결과 | [v1/README.md §6](v1/README.md#6-리뷰-에이전트로-넘기는-것) · [§9](v1/README.md#9-현재-상태와-실험) |
| BE · FE · 리뷰가 맞춰야 할 것 | [v1/README.md §13](v1/README.md#13-팀과-정해야-할-것) |

---

## 다른 프로젝트가 이 프로젝트를 쓰는 법

| 누가 | 언제 | 무엇을 |
|---|---|---|
| BE | 발표 중 1초마다 | `POST /v1/coach/evaluate` — 지금 상황 + 지난 `coach_state` 를 보내고, `feedback`(있으면 FE 로) · `events`(쌓기) · `coach_state`(보관)를 받는다 |
| BE | Take 종료 직후 | `POST /v1/coach/finalize` — 남은 이벤트를 받아 쌓는다 |
| 리뷰 에이전트 | Take 종료 분석 | 쌓인 이벤트 + 이 Take 의 계획 · 미션 · 기억으로 만든 `CoachReviewEvidence` — 영역 상태, 순위 매긴 문제, 다음 미션 후보(목표값 포함), 이전 미션 판정, 이전 Take 비교, 강점, 장별 표, 문제 구간, 코칭 효과. 프롬프트 규칙은 [v1/README.md §6-5](v1/README.md#6-5-리뷰-에이전트-프롬프트에-넣을-규칙) |
| FE | 발표 중 | BE 가 전달하는 `feedback`(지시 1개)과 `indicators`(상태 표시) |

```json
{
  "action": "INTERVENE",
  "feedback": {"type": "TIME", "instruction": "CONDENSE",
               "message": "핵심만 말하고 넘어가세요 — 남은 2장, 1분 5초",
               "priority": 100, "confidence": 0.9, "evidence": {"start_ms": 115000, "end_ms": 115000, "…": "…"}},
  "indicators": {"schedule": "BEHIND", "pace": "FAST", "gaze": "SCRIPT", "volume": "OK"},
  "events": ["…"],
  "coach_state": {"v": 1, "…": "다음 요청에 그대로"}
}
```

모양의 **의미**가 바뀌면 모델 버전이 올라갑니다.

---

## 현재 상태

**v1 / local 만 존재합니다.**

| | |
|---|---|
| ✅ | 판단 파이프라인 전 구간 동작 · 테스트 · 재생 시나리오 모두 통과 · 판단 1회 p95 1.3ms 이하 ([v1/README.md §9-1](v1/README.md#9-1-상태)) |
| ✅ | 리뷰 근거 실험 (176 Take): 첫 규칙 대비 근거 없는 지적 0.50 → 0.01/Take, 구간 정밀도 0.62 → 0.98, 1순위 일치 0.71 → 0.91 (noisy) |
| ⚠️ | **실제 발표 데이터가 없어 기준값은 검증되지 않았습니다** — 가상 발표자와 잡음 모델로만 확인 |
| ⬜ | `ai/releases/` 승격 · BE 연동 · v1.1 코칭 계획(LLM) |

"v1 을 완료라고 부르려면 무엇이 필요한가"는 [v1/README.md §9](v1/README.md#9-현재-상태와-실험) 에 있습니다.
