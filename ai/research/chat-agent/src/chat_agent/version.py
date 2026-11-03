"""대화 에이전트의 버전 값 (기능 문서 9절).

- 누가 쓰나: 응답 `meta`(turn.py) · 실행 상태 payload(state.py) · 진단 기록(diagnostics.py).
- 고칠 때
  - `FEATURE_VERSION`: 프롬프트 · 도구 정의 · 검사 규칙 · 자율 등급 · 허용 근거 낱말 목록이 바뀌면 올린다.
    (config.py 의 낱말 목록 · 검사 ID 를 바꿔도 올린다 — 9절)
  - `TOOL_SCHEMA_VERSION`: 도구 · 행동 모양(인자 · 결과)이 바뀌면 올린다. 진행 중 실행은 서명한 판을 고정하므로
    판이 다르면 이어 부르기가 409 `AGENT_STATE_INVALID` 다(5-6).
  - `SCHEMA_VERSION`: 요청 · 응답 필드의 이름 변경 · 삭제 · 뜻 변경 때만 올린다(`architecture.md` 5절).
  - `STATE_V`: 서명 형식(외곽 `v`)이 바뀔 때만 올린다.
- prompt_version(프롬프트 해시) · corpus_version(자료 묶음 판)은 각각 prompts/ · corpus.py 가 계산한다.
"""

FEATURE_VERSION = "chat_agent-0.1.0"
SCHEMA_VERSION = "1.0"
TOOL_SCHEMA_VERSION = "tools-1"
STATE_V = 1

#: 이어 부를 때 받아 주는 실행 판(feature · tool_schema). 진행 중 실행은 판을 고정하므로(5-6),
#: 새 판을 배포해도 아직 지원하는 옛 판이면 여기에 남겨 둔다. 지원하지 않는 판은 409.
SUPPORTED_RUN_VERSIONS: frozenset[tuple[str, str]] = frozenset(
    {(FEATURE_VERSION, TOOL_SCHEMA_VERSION)}
)
