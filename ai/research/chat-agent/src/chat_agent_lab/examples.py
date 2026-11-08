"""`/agent/turn` 입출력 예시 JSON 다시 만들기 명령 (기능 문서 5-10).

- 실행: 연구 폴더에서 `.\\.venv\\Scripts\\python.exe -m chat_agent_lab.examples` (macOS · Linux 는 `.venv/bin/python`).
  서비스 코어의 예시 폴더(`features/chat_agent/examples/`)를 다시 쓴다.
- 만드는 코드는 서비스 시험 도우미 `ai/service/tests/unit/chat_agent/example_builder.py` 하나다(서비스 CI 가 예시 파일이
  그 결과와 같은지 보므로 거기에 둔다). 이 모듈은 그것을 불러 쓰기만 한다.
"""

from __future__ import annotations

from .service_tests import example_builder

build = example_builder.build
write = example_builder.write
EXAMPLES = example_builder.EXAMPLES
EXAMPLE_STATE_KEY = example_builder.EXAMPLE_STATE_KEY
EXAMPLE_ISSUED_MS = example_builder.EXAMPLE_ISSUED_MS


if __name__ == "__main__":
    for p in write():
        print(p.name)
