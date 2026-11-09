# 대화 에이전트 풀스택 E2E (스파이크)

진짜 FE + 진짜 BE + PostgreSQL + 진짜 AI 서비스 앱(모델만 정해 둔 가짜)을 로컬에서 띄워, 브라우저로 한 턴을 끝까지 확인한다.
`frontend/` · `backend/` · `infra/` · `.github/` · `ai/service/` 파일은 하나도 고치지 않는다(읽기만, 새 파일은 이 폴더에만).

## 파일
| 파일 | 하는 일 |
|---|---|
| `compose.yml` | postgres:17 하나(호스트 55432, 프로젝트 `pc-e2e`, 데이터는 tmpfs) |
| `stack.py` | DB → 마이그레이션 → 시드 → AI(18001) → BE(18000) → FE(3100) 기동, 명령 실행 뒤 모두 내림(`down -v`) |
| `seed.py` | 사용자(동의 기록 포함) · 피치 · 발표자료/대본 · Take 1(3장 시선 28%) · 코치 결과를 BE 엔티티로 넣고 리프레시 쿠키 값 발급(`token`은 테스트마다 새 값) |
| `ai_server.py` | `create_app()` + `app.state.chat_agent_runtime` 에 가짜 모델(`ScriptedLlm`) 주입. 계획 → `get_take_report`(3장) → 답(RECORD, 근거 id는 도구 결과에서 찾음) |
| `playwright.config.ts` · `package.json` · `tests/turn.e2e.ts` | chromium 한 개, 서버는 띄우지 않음. 시험 1개 |

## 실행 (저장소 루트에서)
```bash
(cd ai/research/chat-agent/e2e && npm ci && npx playwright install chromium)   # 처음 한 번
python3 ai/research/chat-agent/e2e/stack.py run -- npm --prefix ai/research/chat-agent/e2e test
```
전제: Docker 실행 중, `uv`, `backend/`에서 `uv sync`, `frontend/`에서 `npm ci`, `ai/research/chat-agent/.venv`.
`stack.py up`은 띄운 채 유지(로그 `.stack/*.log`).

## 확인하는 것
쿠키로 로그인 → 시드한 Take 리포트에서 대화 열기 → 질문 → (a) 할 일 목록과 상태 `data-plan-status` (b) 답 문장 `data-claim`(28%)
(c) 화면이 부른 `GET /api/chat/runs/{id}` 가 `DONE` (d) DB `chat_runs.status='DONE'` · `chat_messages` 사용자/답 각 1행.

## 한계
- 가짜 모델 대본이 한 가지(항상 같은 계획 · 같은 도구 · 같은 답)다. 시나리오별 분기는 아직 없다.
- 로그인은 쿠키 주입이라 Google OAuth는 거치지 않는다. S3 · Deepgram · 코치 호출은 쓰지 않는다(더미 값).
- `ai/.env`가 있으면 설정을 읽지만 모델은 부르지 않는다. 문서에 쓴 포트가 이미 쓰이면 실패한다.
