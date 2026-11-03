# chat-agent (대화형 코치 에이전트)

로그인한 사용자가 화면 오른쪽 아래 대화창에서 자기 발표 기록 · 사용법을 묻고, 자기 발표에 대한 주장을 기록으로 판정받고,
다시 평가 · 목표 저장 · 연습 이동을 요청하는 기능의 **AI 쪽 연구 모듈**입니다. BE가 `POST /agent/turn`을 부르면
작은 루프가 할 일 목록을 세우고 도구로 조사하며, 서버 밖 일은 행동으로 BE에 맡기고 서명한 실행 상태로 이어 받습니다.
사용자 주장은 코드가 기록으로 먼저 판정하고, 코드가 검사한 문장만 답으로 나갑니다.

| | |
|---|---|
| 담당 | JWK0395 |
| 설계 | 목적 · 계약 · 처리 · 실패 처리 · 시험 → [`docs/ai/features/chat-agent.md`](../../../docs/ai/features/chat-agent.md) |
| 단계 | 연구 모듈에서 먼저 만들고 확인한 뒤 서비스에 탑재한다(기능 문서 4-1). **탑재 전에는 BE가 부르지 않는다** |
| 버전 | `chat_agent-0.1.0`(`src/chat_agent/version.py`), 응답 스키마 `1.0`, 도구 스키마 `tools-1`, 실행 상태 `v` 1 |

## 1. 코드 구조

코드는 두 층이고 import 는 위에서 아래로만 합니다.

```
src/chat_agent_lab/   연구 전용: 도구 호출 LLM · 서비스 계층 대역 · 가짜 BE · 평가 · 예시 만들기   → 옮기지 않음(llm.py만 공통 모듈로)
      │ import
src/chat_agent/       코어: 계약 · 판정 · 검사 · 도구 · 행동 · 루프                              → 서비스 features/chat_agent/로 폴더째 옮김
```

코어 규칙(서비스 코어와 같음, `docs/ai/architecture.md` 8절): 패키지 안은 상대 import 만, 파일 · DB · 네트워크 · 환경변수 ·
시계 · 난수를 직접 쓰지 않음(시계 · 서명 키 · 자료 묶음 · LLM 은 인자로 받음), 의존성은 pydantic 하나.

## 2. 폴더와 파일

```
chat-agent/
├─ README.md · pyproject.toml · .python-version(3.12) · .gitignore(outputs/)
├─ src/chat_agent/          코어
├─ src/chat_agent_lab/      연구 전용 도구
├─ tests/unit/              코어 단위 · 계약 시험 (서비스 탑재 때 ai/service/tests/unit/chat_agent/ 로)
├─ tests/lab/ · tests/live/ 연구 도구 시험 · 실제 모델 평가(live)
├─ evals/scenarios/         평가셋 · 안전 세트 · 객관성 세트
└─ reports/results/         커밋하는 평가 결과
```

### 2-1. 코어 `src/chat_agent/`

| 파일 | 하는 일 | 입력 → 출력 | 부르는 곳 | 고칠 때 |
|---|---|---|---|---|
| `schemas.py` | `/agent/turn` 요청 · 응답 · 스트림 줄 · 진단 기록 · 행동 모양 = 계약(5절) | JSON → pydantic 모델 | 코어 전체 · 계약 시험 · 가짜 BE | 기능 문서 5절 · `examples/` · 계약 시험을 같이 고친다. 이름 변경 · 삭제는 `SCHEMA_VERSION` |
| `config.py` | 비교 방향 표 · 차원 표(리뷰와 같은 사본) · 목표 지표 · 낱말 목록 · 한도 · 크기 | `load_config(**덮어쓸 값)` → 깊게 불변 설정 | 코어 전체 | 낱말 목록을 바꾸면 `FEATURE_VERSION` 을 올린다. 리뷰 표가 바뀌면 `test_contract_tables.py` 가 깨진다 |
| `version.py` | feature · schema · tool_schema · state 판 | — | 응답 `meta` · 실행 상태 | 9절 '언제 오르나' |
| `templates.py` | 사용자 안내 문구 · 판단 첫 문장 · 결론 문장(7-9 · 7-10) | 판정 · 근거 문구 → 문장 | 판단 · 답 검사 · 루프 | 문구는 기능 문서 7-9와 글자까지 같게 둔다(`test_config_templates.py`) |
| `examples/` | 요청 · 스트림 · 오류 · 행동 결과 예시 JSON(5-10) | — | 계약 시험 · BE 계약 시험 | `python -m chat_agent_lab.examples` 로 다시 만든다 |

### 2-2. 연구 전용 `src/chat_agent_lab/`

| 파일 | 하는 일 | 입력 → 출력 | 부르는 곳 | 고칠 때 |
|---|---|---|---|---|
| `fixtures.py` | 가상 피치 · Take · 코치 Take 결과 · 리뷰 · '끝까지 한 발표' 계산(12-3) | 매개변수 → 저장 payload 모양 dict | 예시 만들기 · 가짜 BE · 평가 | 코치 · 리뷰 결과 모양이 바뀌면 서비스 예시 파일과 맞춘다 |
| `examples.py` | 코어 `examples/` 파일 만들기 | 가상 기록 → JSON · NDJSON 파일 | 사람이 실행 | 5절 모양을 바꾸면 다시 만든다 |

## 3. 실행

Windows 기준이고, 실행 파일 대신 `python -m` 으로 부릅니다.

```powershell
cd ai/research/chat-agent
uv sync                                             # .venv 를 만들고 서비스 패키지(pitch-coach-ai)를 편집 설치
.\.venv\Scripts\python.exe -m pytest                # 기본 실행: live · slow 는 빠진다(pyproject addopts)
.\.venv\Scripts\python.exe -m chat_agent_lab.examples   # 예시 JSON 다시 만들기
```

- 새 `.venv`에서 첫 `pytest` 수집이 앱 제어 정책 때문에 한 번 실패할 수 있습니다. 다시 돌리면 통과합니다.
- 기본 실행은 LLM 키 · 서명 키 없이 통과합니다(가짜 모델). 실제 모델 평가(`live`)는 요금이 들고 요청할 때만 돌립니다.
