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

FEATURE_VERSION = "chat_agent-0.11.0"
SCHEMA_VERSION = "1.0"
TOOL_SCHEMA_VERSION = "tools-4"
STATE_V = 1
#: 목표 판정 계산식(6-4 판정 순서 · 비교식)의 판. 계산식을 바꾸면 올린다. 목표 규칙 판(goal_rule_version)에 들어가므로
#: 옛 판으로 저장한 목표는 판이 달라 새 목표를 정하자고 한다(설정 해시만으로는 계산식 변경을 알 수 없다)
GOAL_ALGORITHM_VERSION = "goal-algo-1"
#: 판정할 수 있는 목표 계산식 판. 저장된 목표는 자기 min_delta · 범위 · operator 를 고정해 두므로, 계산식만 지원하면
#: 설정이 바뀌어도 같은 결과로 판정한다(6-4 · L4-12). 여기 없는 판의 목표는 새로 정하자고 한다
SUPPORTED_GOAL_ALGORITHMS: frozenset[str] = frozenset({GOAL_ALGORITHM_VERSION})

#: 자료 묶음 판 → 묶음 해시. 한 번 정한 판의 해시는 바꾸지 않는다: 카드 본문을 고치면 판을 올리고 여기에 줄을 더한다
#: (같은 판에 다른 본문이 들어가면 그 판을 인용한 옛 답 · 진행 중 실행과 어긋난다, L4-30). 첫 배포 전에도 평가 결과 표(README)에
#: 남은 판은 고쳐 쓰지 않는다(그 판으로 잰 결과와 본문이 어긋난다). guides-2: 동의어 사전 · 카드 g-13 범위 밖 문장 빼기.
#: guides-3: 사용법 카드 u-07 을 기준이 판정되는 방식(사실)만 남김 — 평가 기준을 어떻게 쓰라는 조언은 범위 밖(1-3).
#: guides-4: u-08 의 기준 고쳐 쓰기 안내 문장 빼기, 동의어 사전에서 오탐 낱말(불안 · 급하게 · 포즈 · 결론 …) 빼고
#: 목소리 · 억양 묶음 대표어를 카드 표현으로, q&a 더함. guides-5: 사용법 카드 u-03 을 대본 파서 규칙에 맞춤(번호 조건 ·
#: 첫 구분 표시 앞에는 제목만, #885 리뷰). guides-6: u-07 셋째 문장에서 u-08 과 겹치는 '측정되지 않는 항목' 부분을 뺌
#: (u-07 은 판정, u-08 은 빠지는 항목으로 역할을 나눔, #921 리뷰). guides-7: 카드 단위 검수 — 발표법 카드 21장 검수 완료(g-19 만
#: CC BY 참고 문헌이 없어 검수 전), 발표법 카드에 CC BY 4.0 교재 참고 문헌, 교재와 어긋난 문장 다듬기(g-01 · g-07 · g-12 · g-14 ·
#: g-15 · g-18), g-13 을 질의응답 시간의 전달만으로, 새 카드 g-21(카메라 앞) · g-22(실수 뒤 이어 가기), u-25 를 1-3 에 맞춤(P-0008)
#: guides-8: 내용 구성 · 자료 수정 · 제한 시간 추천으로 읽힐 문장을 빼고 전달만 남김(g-08 · g-09 · g-10 · g-11 · g-18 · g-20, 1-3,
#: 토론으로 고른 문장), 그에 맞춰 g-09 · g-10 · g-11 참고 문헌을 본문이 기대는 장으로
CORPUS_VERSION_HASHES: dict[str, str] = {
    "guides-1": "48aa04e52f8f16abacb6c87d37d5536daf39e4d3c4aac05bf1550cad9dd6ad89",
    "guides-2": "a2b1f11aaf653da307afce2ec0bfe8d5a141fe08d3faed1e803c955ffe619823",
    "guides-3": "470a7eb607869ce4598e956d9c6d958b2cde5678d792062cc12b4342f85776bc",
    "guides-4": "ed5d3b2dd2a3aae8acd8ec5bc71518b64863d1ba7323db889140f20200af16d7",
    "guides-5": "a8c7f133a4549c27c1e22ea4530b91ff19834551e96e59fd9d8d2c822a6cc30a",
    "guides-6": "a74cb7cc6790fcf607edbc7d69d42593dc0a1fa89330e089c83d16208ecfb48d",
    "guides-7": "421b5e210d49f540960b512675dc118fd90321d6cb631184d460512597a8e9fd",
    "guides-8": "f4ace2bfa2f4ca32dfee2367c649dac72255e2a961c75814cdb12498b0de3946",
}

#: 이어 부를 때 받아 주는 실행 판(feature · tool_schema). 진행 중 실행은 판을 고정하므로(5-6),
#: 새 판을 배포해도 아직 지원하는 옛 판이면 여기에 남겨 둔다. 지원하지 않는 판은 409.
SUPPORTED_RUN_VERSIONS: frozenset[tuple[str, str]] = frozenset(
    {(FEATURE_VERSION, TOOL_SCHEMA_VERSION)}
)
