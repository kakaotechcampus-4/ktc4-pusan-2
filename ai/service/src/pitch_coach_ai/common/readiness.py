"""시작할 때 한 번 불러오는 서버 안 모델과 준비 상태 (`docs/ai/ai-service.md` 6-3).

- 부르는 곳: `main.py` 의 lifespan 이 `start(...)` 로 불러오기를 시작하고, 모델을 쓰는 라우터가
  `Depends(require_ready)` 로 준비 전 요청을 503 `NOT_READY` 로 돌려보낸다.
  `GET /ready` 도 이것을 본다
- 불러오기는 별도 스레드에서 한다. 그동안에도 서버는 요청을 받아 `/health` 는 200, 모델을 쓰는
  기능은 503 이다(BE 는 다시 부른다). 불러오기가 실패하면 `on_failure` 를 부른다.
  uvicorn 이 쓰는 앱(`main.app`)은 여기서 프로세스를 끝낸다. 워커가 하나면 compose 재시작
  정책(`restart: unless-stopped`)이 컨테이너를, 여러 워커면 uvicorn 이 그 워커를 다시 띄운다.
  헬스체크가 unhealthy 를 표시하는 것만으로는 다시 시작되지 않는다(`ai-service.md` 7절).
  테스트가 만드는 앱은 끝내지 않는다
- `require_ready` 와 `/health` · `/ready` 는 `async def` 다. 긴 LLM 요청이 동기 라우터의 스레드 풀을
  채워도 헬스체크와 준비 확인은 기다리지 않는다
- 로그: `warmup_done name=… duration_ms=…` · `warmup_failed name=… error=…`(예외 종류와 위치만)
- 고칠 때: 새 모델은 기능 라우터 모듈의 `WARMUP` 목록에 불러오는 함수를 더한다(함수는 결과를
  스스로 보관해야 한다. 예: `functools.cache` 를 단 `get_kiwi`)
"""

import logging
import threading
import time
from collections.abc import Callable, Sequence

from fastapi import Request

from pitch_coach_ai.common.errors import NotReady, exception_summary

logger = logging.getLogger("pitch_coach_ai.readiness")

Loader = Callable[[], object]


class Readiness:
    """불러올 함수 목록을 한 스레드에서 차례로 부르고, 다 끝나면 준비됨으로 둔다."""

    def __init__(
        self, loaders: Sequence[Loader], on_failure: Callable[[], None] | None = None
    ) -> None:
        self._loaders = list(loaders)
        self._on_failure = on_failure
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """불러오기를 시작한다. 불러올 것이 없으면 바로 준비됨이다."""
        if not self._loaders:
            self._ready.set()
            return
        self._thread = threading.Thread(target=self._run, name="warmup", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        for load in self._loaders:
            name = getattr(load, "__qualname__", repr(load))
            started = time.monotonic()
            try:
                load()
            except Exception as exc:  # noqa: BLE001  # 준비되지 않은 채로 남긴다
                logger.error("warmup_failed name=%s error=%s", name, exception_summary(exc))
                if self._on_failure is not None:
                    self._on_failure()
                return
            logger.info(
                "warmup_done name=%s duration_ms=%.1f", name, (time.monotonic() - started) * 1000
            )
        self._ready.set()

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        """준비될 때까지 기다린다(테스트용). 준비됐으면 True."""
        return self._ready.wait(timeout)


async def require_ready(request: Request) -> None:
    """라우터 의존성: 시작할 때 불러오는 모델이 아직 준비되지 않았으면 503 `NOT_READY`."""
    if not request.app.state.readiness.ready:
        raise NotReady()
