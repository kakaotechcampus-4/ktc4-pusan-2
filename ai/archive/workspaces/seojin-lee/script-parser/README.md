# script-parser

발표 연습 코칭 서비스 **Pitch Coach**의 대본 분리 프로젝트입니다.

발표자가 입력한 대본 전체 텍스트를 받아, 명시적으로 구분된 **슬라이드 단위**로 나누고
슬라이드별 **키워드**를 추출합니다. 슬라이드 구분자가 없거나 불완전하면 억지로 나누지 않고
`fail`로 처리해, 전체 대본에서 키워드만 추출합니다.

> 위치: `workspaces/seojin-lee/script-parser/`
> 워크스페이스 규칙(버전 축, `local`/`deploy`, 문서 범위)은 [../README.md](../README.md)에 있습니다.

---

## 폴더 구조

```
script-parser/
├── README.md          ← 이 문서: 프로젝트가 무엇이고 어디서 시작하나
│
└── v1/
    ├── README.md      # v1 모델의 전체 기술 레퍼런스 (local/deploy 공유)
    ├── local/         # 실험·평가 리그  ← 지금 코드는 전부 여기
    │   ├── README.md
    │   ├── script_slide_analysis.ipynb
    │   └── 대본/       # 테스트용 발표 대본 15개 (.txt)
    └── deploy/        # 서비스 투입용 패키징 (아직 없음)
```

`v1`은 **모델 버전**이지 릴리스 버전이 아닙니다. 제품으로 나갈 때는
`releases/vX.Y.Z/script-parser/`에 묶이며, 두 축은 독립적으로 움직입니다
([워크스페이스 README](../README.md) 참고).

---

## 어디서 시작하나

| 하려는 일 | 위치 |
|---|---|
| **코드 돌려보기 · 개발하기** | [v1/local/README.md](v1/local/README.md) |
| 모델을 이해하기 · v1의 범위와 현재 상태 | [v1/README.md](v1/README.md) — 전체 기술 레퍼런스 |

---

## 다른 프로젝트가 이 프로젝트를 쓰는 법

소비자가 받는 것은 **`status`와 `slides` 배열을 가진 `SeperatedSlides` JSON**입니다.
이 모양이 이 프로젝트의 계약이고, 모양이 바뀌면 모델 버전이 올라갑니다.

```json
{
  "status": "success",
  "slides": [
    {
      "slide_number": 1,
      "script": "안녕하세요. 오늘은 멸종위기청년에 대해 발표하겠습니다.",
      "keywords": ["멸종위기청년", "발표", "인사"]
    }
  ]
}
```

- `status: "success"` — "Slide", "슬라이드", 숫자 같은 **명시적 구분자**로 대본 전체가 빠짐없이
  나뉜 경우. `slide_number`는 원문 표기를 그대로 쓰고, `script`에는 구분 기호와 소제목을 뺀
  본문만, `keywords`는 슬라이드당 3~7개
- `status: "fail"` — 구분자가 하나도 없거나 일부 구간에만 있어 전체를 나눌 수 없는 경우.
  `slide_number: null`에 대본 전체를 하나의 `script`로 담고, `keywords`는 대본 전체에서
  편향 없이 15개 정도 추출
- `keywords`는 항상 대본에 있는 단어·어절 그대로이며, 요약이나 의미 추출이 아닙니다

응답 후처리로 마크다운 강조·헤더·링크 문법을 제거하는 `clean_script` 함수가 함께 있습니다
([v1/local](v1/local/) 참고).

> ⚠️ **아직 전송 계층이 없습니다.** 지금은 노트북에서 `structured_output.invoke(...)`를
> 직접 호출하는 형태뿐이며, 서비스에 붙이는 형태는 `v1/deploy/`에서 정해질 예정입니다.

각 필드의 의미와 판단 기준은 [v1/README.md](v1/README.md)를 보세요.

---

## 현재 상태

**v1 / local만 존재합니다.**

| | |
|---|---|
| ✅ | LLM structured output 기반 슬라이드 분리·키워드 추출 파이프라인 동작 · 테스트 대본 15개로 수동 검증 |
| ⚠️ | **정량 평가셋·정확도 지표가 없어 분리 정확도는 측정되지 않았습니다** |
| ⬜ | `v1/deploy` 미작성 |

가장 큰 공백은 평가셋입니다 — 그전까지 모든 분리 정확도 수치는 미측정입니다.
"v1을 완료라고 부르려면 무엇이 필요한가"는 [v1/README.md](v1/README.md)에 있습니다.
