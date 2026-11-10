# 대화 에이전트 풀스택 E2E

진짜 FE + 진짜 BE + PostgreSQL + 진짜 AI 서비스 앱(모델만 시나리오별 대본으로 정해 둔 가짜)을 로컬에서 띄워, 브라우저로 시나리오 6개를 끝까지 확인한다.
`frontend/` · `backend/` · `infra/` · `.github/` · `ai/service/` 파일은 하나도 고치지 않는다(읽기만, 새 파일은 이 폴더에만). 실제 LLM은 부르지 않는다.

## 파일
| 파일 | 하는 일 |
|---|---|
| `compose.yml` | postgres:17 하나(호스트 55432, 프로젝트 `pc-e2e`, 데이터는 tmpfs) |
| `stack.py` | DB → 마이그레이션 → AI(18001) → BE(18000) → FE(3100) 기동, 명령 실행 뒤 모두 내림(`down -v`). AI에 모델 주소를 닿지 않는 곳으로 넘기고, 시나리오 통제 파일(`.stack/scenario`)을 만든다 |
| `seed.py` | 테스트마다 새 사용자 · 피치 · Take를 BE 엔티티로 넣고 쿠키 값 발급. `new base｜review｜goal`, `sweep`(자동 목표 확인을 지금 한 번 실행) |
| `ai_server.py` | `create_app()` + `app.state.chat_agent_runtime`에 가짜 모델(`ScriptedLlm`) 주입. 통제 파일의 시나리오 이름으로 `scripts/<이름>.json`을 읽어 응답을 낸다. `/takes/review`는 모델 없이(기본 결과) 돈다 |
| `scripts/*.json` | 시나리오별 대본 데이터: 사용자 말, 호출 순서(계획 · 도구 · 실패), 답 문장, **기대 문구(`expect.plan_steps` 등)** |
| `tests/helpers.ts` | 시드 · 시나리오 고르기 · 로그인 · 대화 열기/보내기 · BE API(`Api`) · DB 조회(`sql`) |
| `tests/*.e2e.ts` | 시나리오 테스트(아래 표). 시험마다 화면 · BE API · DB 세 층을 본다 |
| `playwright.config.ts` · `package.json` | chromium 한 개, 서버는 띄우지 않음, 순차 실행 |

## 시나리오
| # | 파일 · 대본 | 확인하는 것 (화면 / BE API / DB) |
|---|---|---|
| 1 | `turn.e2e.ts` · `plan` | 질문 → 할 일 목록 `data-plan-status` · 답 문장 `data-claim`(28%) / 실행 조회와 메시지 목록 DONE, 저장된 계획 단계 글자 / `chat_runs` DONE · 사용자/답 메시지 각 1행 · DB 계획 |
| 2 | `turn.e2e.ts` · `plan-update` | 모델이 `update_plan`을 불러 3단계로 바꿈 → 화면 목록이 새 단계 / API · DB의 계획도 새 단계 |
| 3 | `approval.e2e.ts` · `approve-goal` | 목표 저장(ASK) 확인 카드 → 새로고침 뒤에도 카드 → [승인] → "목표를 저장했어요." · GOAL_SAVED 카드 / 실행 WAITING_APPROVAL → DONE / 행동 PENDING → SUCCEEDED 1건, 목표 행 정확히 1개, 답 메시지 1개(중복 실행 없음) |
| 4 | `reassess.e2e.ts` · `reassess` | 이의 + 다시 평가 → 확인 카드 → [승인] → "참고 결과 — 공식 리뷰는 그대로예요" 카드 / 실행 DONE · 카드 REASSESS_RESULT / BE가 AI `/takes/review`를 모델 없이 호출(결과 `fallback_reason=LLM_UNAVAILABLE`), 공식 리뷰 행(내용 · revision) 그대로 |
| 5 | `partial.e2e.ts` · `partial-continue` | 조사 중 모델 시간 초과 → "여기까지 확인했어요" 머리 + [이어서 확인] → 누르면 새 메시지 → 끝까지 답 / 첫 실행 PARTIAL → 둘째 DONE, 메시지 4개 / DB 실행 순서 |
| 6 | `goal-check.e2e.ts` | 열린 대화 목표 + 목표 뒤 Take 2(리뷰 DONE)를 시드 → `sweep`(BE `goal_sweep.sweep_once()` 직접 호출) → TAKE_REVIEWED 실행이 모델 없이 DONE / 대화 목록에서 열면 GOAL_RESULT 카드("이룸") / 목표 행 OPEN → ACHIEVED, 임대 DONE |

## 실행 (저장소 루트에서)
```bash
(cd ai/research/chat-agent/e2e && npm ci && npx playwright install chromium)   # 처음 한 번
python3 ai/research/chat-agent/e2e/stack.py run -- npm --prefix ai/research/chat-agent/e2e test
```
전제: Docker 실행 중, `uv`, `backend/`에서 `uv sync`, `frontend/`에서 `npm ci`, `ai/research/chat-agent/.venv`.
`stack.py up`은 띄운 채 유지(로그 `.stack/*.log`). 그동안 `E2E_STATE=$PWD/ai/research/chat-agent/e2e/.stack/state.json npm --prefix ai/research/chat-agent/e2e test -- <파일 이름 일부>`로 한 파일만 돌릴 수 있다. `ai_server.py`나 `scripts/*.json`의 구조를 바꾸면 스택을 다시 띄운다(대본 내용은 시나리오를 다시 고를 때마다 다시 읽힌다).

## 대본 고치기 (계획 스키마가 바뀔 때)
- 계획 단계 글자 · 단계별 칸(예: 단계마다 쓸 도구 힌트 `tool`, `tools-5`)은 `scripts/*.json`의 `plan.steps`(문자열 또는 `{"step", "status", "note", ...}` 객체 — 늘어난 칸은 그대로 실린다)와 `update_plan` 인자에 쓴다. 기대 글자는 같은 파일 `expect.plan_steps`.
- 계획 문구를 확인하는 코드는 `tests/turn.e2e.ts`의 `// 계획 문구 확인` 주석 세 곳(화면 · API · DB)뿐이다. 문구 말고 `tool` 같은 새 칸을 확인하려면 거기에 줄을 더한다.
- 계획 단계 글자에 숫자(예: `3장`)를 쓰면 AI가 근거 없는 숫자로 보고 `update_plan`을 거절한다(그래서 갱신 대본은 "해당 장"이라 쓴다).
- 측정 항목 낱말이 든 질문은 계획에 판단 초안(`assessments`)이 있어야 한다(없으면 계획을 다시 요구해 대본 순서가 어긋난다). `{"draft": {...}}`는 `F.draft(**)` 인자다.
- 호출 순서는 서비스 시험의 `FakeLlm`과 같다: `script`(계획 · 조사)·`answers`·`fixes`를 호출 순서대로 하나씩 꺼낸다. 승인 뒤 재개와 이어서 확인도 같은 줄에서 이어진다.

## 한계
- 로그인은 쿠키 주입이라 Google OAuth는 거치지 않는다. S3 · Deepgram · 코치 호출은 쓰지 않는다(더미 값).
- 모델 호출은 한 시나리오 한 대본이다. 호출 순서를 어기면(대본이 모자라면) 가짜 모델이 실패해 실행이 FAILED로 끝난다.
- 시나리오 6은 60초 주기 대신 `sweep_once()`를 같은 DB에서 따로 프로세스로 불러 실행한다(BE 앱의 주기 작업 자체는 시험하지 않는다).
- 리뷰는 기본 결과(문장 · 미션이 기본값)라 REASSESS 결과의 문장 품질은 보지 않는다.
- `ai/.env`가 있으면 설정을 읽지만 환경변수가 앞서고 모델 주소는 닿지 않는 곳이다. 문서에 쓴 포트가 이미 쓰이면 실패한다.
