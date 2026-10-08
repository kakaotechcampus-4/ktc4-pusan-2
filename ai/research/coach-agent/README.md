# coach-agent (실시간 코치)

발표 중 1초마다 시선 · 말 속도 · 음량 · 침묵 · 군더더기 · 시간(장별 계획 대비)을 보고
**지금 발표자에게 말을 걸지, 건다면 무엇 하나를 말할지** 정하는 기능입니다.
말한 뒤에는 실제로 행동이 바뀌었는지 보고 같은 방법을 유지할지, 다른 방법을 쓸지, 그만둘지를 고릅니다.
Take가 끝나면 판단 기록을 묶어 리뷰 에이전트가 쓸 근거(문제 순위 · 미션 판정 · 이전 Take 비교 · 다음 미션 후보)를 만듭니다.

| | |
|---|---|
| 담당 | jewon-kim |
| 판단 방식 | 규칙 + 되돌아보기. LLM을 쓰지 않습니다 |
| 상태 | v1. **가상 발표자로만 확인했습니다.** 기준값은 실제 발표 데이터로 검증하지 않았습니다 |
| 배포 | 판단 코어(`src/coach/`)는 나중에 AI 서버(`ai/service`)의 `features/coach/`로 그대로 옮깁니다 |

> 이 프로젝트는 `ai/archive/workspaces/jewon-kim/coach-agent/v1/local/`을 구조만 바꿔 옮기는 것입니다.
> 옮기면서 판단 로직과 기준값은 바꾸지 않습니다.

---

## 코드 구조 한눈에 보기

코드는 두 층이고, import는 위에서 아래로만 합니다.

```
src/coach_lab/     재생 · 실험: 가상 발표자 · 정답 리뷰 · 채점    → research 에만 남음
      │ import
src/coach/         판단 코어: 1초 판단 · Take 종료 · 리뷰 근거      → 나중에 AI 서버로 폴더째 복사
```

| 층 | 하는 일 | 배포 때 |
|---|---|---|
| 코어 `coach` | 1초마다 판단(`decide`), Take 종료 정리(`finalize`), 리뷰 근거 만들기(`build_review_evidence`) | AI 서버로 그대로 옮김 |
| 재생 · 실험 `coach_lab` | 가상 발표 시나리오 재생, 정답과 비교한 리뷰 근거 채점, 결과 파일 쓰기 | 옮기지 않음 |

### 코어의 규칙

코어만 떼어 서버로 옮겨도 코드를 고칠 필요가 없고, 같은 요청에는 언제나 같은 응답이 나오도록 지키는 규칙입니다.

| 규칙 | 이유 |
|---|---|
| 패키지 안에서는 **상대 import만** (`from .state import …`) | 폴더 이름이 `pitch_coach_ai.features.coach`로 바뀌어도 고칠 import가 없다 |
| **파일 · DB · 네트워크 · 환경변수를 직접 쓰지 않음** | 서버는 상태를 두지 않는다. 저장은 BE, 설정 파일 읽기는 코어 밖이 맡는다 |
| **시계 · 난수를 쓰지 않음.** 시간은 요청의 `t_ms`뿐 | 같은 요청에 같은 응답이 나와야 재생 결과와 배포 결과가 같다 |
| **기억은 `coach_state`로 주고받음** | 코치는 지난 기억을 응답에 담아 돌려주고, BE가 보관했다가 다음 요청에 그대로 붙인다. AI 서버를 재시작하거나 늘려도 판단이 같다 |
| 의존성은 **pydantic 하나** | 서버로 옮길 때 가져갈 라이브러리가 하나뿐이다 |

---

## 폴더와 파일

```
coach-agent/
├── README.md                이 문서
├── pyproject.toml · uv.lock 라이브러리 목록 · 버전 고정 (Python 3.12)
├── .python-version          uv 가 쓸 Python (3.12)
├── .gitignore               outputs/ 제외
│
├── src/coach/               ── 판단 코어 ──
├── src/coach_lab/           ── 재생 · 실험 도구 ──
│   └── paths.py             폴더 위치 모음 (시나리오 · 출력 · 결과)
│
├── scenarios/               재생 시나리오: 가상 발표 하나 + 기대 결과 (JSON)
├── reports/results/         리뷰 근거 실험 결과 JSON (버전별로 커밋)
├── outputs/                 재생 결과 · 임시 실험 결과 (git 제외)
└── tests/
    ├── unit/                코어 테스트. 코어와 함께 서버로 간다
    └── lab/                 재생 · 실험 도구 테스트. research 에만 남는다
```

| 폴더 | 왜 따로 두나 |
|---|---|
| `tests/unit/` · `tests/lab/` | `unit`은 `coach`만 import합니다. 코어를 옮길 때 이 폴더만 가져가 import 접두어(`coach.` → `pitch_coach_ai.features.coach.`)만 바꾸면 됩니다 |
| `reports/results/` · `outputs/` | 실험 결과는 같은 코드면 같은 값이 나오므로 커밋해 두고, 다음 변경 때 `git diff`로 비교합니다. 재생 결과에는 실행할 때마다 달라지는 판단 시간이 들어 있어 커밋하지 않습니다 |

---

## 준비

```bash
cd ai/research/coach-agent
uv sync                          # Python 3.12, 라이브러리 + 개발 도구(pytest · ruff) 설치
uv run python -m pytest          # 테스트
uv run ruff check . && uv run ruff format --check .   # 린트 (backend 와 같은 규칙, uv.lock 의 ruff)
```

- `.venv`는 git에 없습니다. 클론하거나 폴더를 옮긴 뒤에는 `uv sync`로 다시 만듭니다.
- 코치는 LLM을 쓰지 않으므로 `ai/.env`가 필요 없습니다.
- **Windows**: 이 PC에서는 `pytest.exe` 같은 실행 파일이 앱 제어로 막혀 있어 `uv run python -m …` 형태로 부릅니다.
  콘솔 한글이 깨지면 `PYTHONIOENCODING=utf-8`을 설정하세요.

---

## 원본

- 구조를 바꾸기 전 코드: `ai/archive/workspaces/jewon-kim/coach-agent/v1/local/` (동결본, 수정 · import 금지)
- 개편 직전 상태 그대로의 원본: git 태그 `ai-workspaces-final`
- 폴더 규칙: [../README.md](../README.md), [../../README.md](../../README.md)
