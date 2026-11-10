"""E2E 스택 실행기 — DB → 마이그레이션 → AI → BE → FE 를 띄우고, 끝나면 모두 내린다.

- 쓰는 법 (저장소 루트에서):
    python3 ai/research/chat-agent/e2e/stack.py up             # 띄우고 상태를 출력, Ctrl-C 까지 유지
    python3 ai/research/chat-agent/e2e/stack.py run -- npm --prefix ai/research/chat-agent/e2e test
      → 띄우고, 뒤의 명령을 돌리고, 종료 코드를 그대로 돌려주며 모두 내린다(db 는 `down -v`).
- 하는 일: 포트는 DB 55432 · BE 18000 · AI 18001 · FE 3100. 로그는 `.stack/*.log`, 상태(쿠키 값 포함)는
  `.stack/state.json`(git 무시). 시드는 테스트마다 `seed.py new ...`(여기서 하지 않는다). FE 에 필요한 환경변수만 넘기고 `frontend/` 파일은 고치지 않는다.
- 고칠 때: 환경변수 이름은 `backend/src/pitch_coach_backend/core/config.py` · `ai/service/.../settings.py`.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
STATE_DIR = HERE / ".stack"
PROJECT = "pc-e2e"
BE_PORT, AI_PORT, FE_PORT, DB_PORT = 18000, 18001, 3100, 55432
AGENT_SECRET = "e2e-agent-state-secret-0123456789abcdef"

BE_ENV = {
    "DATABASE_URL": f"postgresql+psycopg://pc:pc@localhost:{DB_PORT}/pc_e2e",
    "JWT_SECRET_KEY": "e2e-jwt-secret-key-at-least-32-bytes-long-0123456789",
    "DEEPGRAM_API_KEYS": "dummy",
    "S3_BUCKET_NAME": "dummy",
    "GOOGLE_CLIENT_ID": "",
    "GOOGLE_CLIENT_SECRET": "",
    "FRONTEND_BASE_URL": f"http://localhost:{FE_PORT}",
    "AI_BASE_URL": f"http://127.0.0.1:{AI_PORT}",
    "AGENT_ENABLED": "true",
    "COOKIE_SECURE": "false",
}
# 모델 설정은 닿지 않는 주소(9번 포트)다 — 어디서든 실제 모델을 부르려 하면 연결 실패가 나서 바로 드러난다.
# 대화 모델은 ai_server.py 가 가짜로 끼우고, 리뷰 엔드포인트는 모델 없이 돈다(ai_server.py 머리 설명).
UNREACHABLE = "http://127.0.0.1:9/v1"
AI_ENV = {
    "AGENT_ENABLED": "true",
    "AGENT_STATE_SECRET": AGENT_SECRET,
    "AI_PORT": str(AI_PORT),
    "OPENAI_BASE_URL": UNREACHABLE,
    "OPENAI_API_KEY": "e2e-no-real-key",
    "OPENAI_MODEL": "e2e-unreachable-model",
    "E2E_SCENARIO_FILE": str(STATE_DIR / "scenario"),
}
#: 실제 모델 시연(`E2E_REAL_MODEL=1 python3 …/stack.py run -- npm --prefix … run demo`): 모델 주소를 막지 않고
#: `ai/.env`의 실제 묶음을 쓴다(요금이 듦). 시나리오 통제 파일 · 가짜 모델은 쓰지 않는다
if os.environ.get("E2E_REAL_MODEL") == "1":
    AI_ENV = {k: v for k, v in AI_ENV.items() if not k.startswith("OPENAI_")} | {"E2E_REAL_MODEL": "1"}
FE_ENV = {
    "VITE_USE_MOCK": "false",
    "VITE_MOCK_SESSION": "false",
    "VITE_API_BASE": f"http://localhost:{BE_PORT}",
}

procs: list[subprocess.Popen] = []


def sh(cmd: list[str], env: dict[str, str] | None = None, cwd: Path = ROOT, **kw) -> str:
    full = {**os.environ, **(env or {})}
    r = subprocess.run(cmd, cwd=cwd, env=full, capture_output=True, text=True, **kw)
    if r.returncode:
        sys.stderr.write(f"실패: {' '.join(cmd)}\n{r.stderr[-2000:]}\n")
        raise SystemExit(r.returncode)
    return r.stdout


def spawn(name: str, cmd: list[str], env: dict[str, str], cwd: Path = ROOT) -> None:
    log = open(STATE_DIR / f"{name}.log", "w")  # noqa: SIM115
    procs.append(
        subprocess.Popen(
            cmd,
            cwd=cwd,
            env={**os.environ, **env},
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    )


def wait_http(url: str, timeout: float = 90) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)  # noqa: S310
            return
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    raise SystemExit(f"기다리다 시간 초과: {url} (로그: {STATE_DIR})")


def down() -> None:
    for p in reversed(procs):
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except OSError:
            pass
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
    subprocess.run(
        ["docker", "compose", "-p", PROJECT, "-f", str(HERE / "compose.yml"), "down", "-v"],
        check=False,
    )


def up() -> dict:
    STATE_DIR.mkdir(exist_ok=True)
    sh(["docker", "compose", "-p", PROJECT, "-f", str(HERE / "compose.yml"), "up", "-d", "--wait"])
    be = ["uv", "run", "--project", str(ROOT / "backend")]
    sh([*be, "alembic", "upgrade", "head"], BE_ENV, cwd=ROOT / "backend")
    (STATE_DIR / "scenario").write_text("plan")
    spawn("ai", [str(ROOT / "ai/research/chat-agent/.venv/bin/python"), str(HERE / "ai_server.py")], AI_ENV)
    spawn(
        "be",
        [*be, "uvicorn", "pitch_coach_backend.main:app", "--port", str(BE_PORT)],
        BE_ENV,
        cwd=ROOT / "backend",
    )
    spawn("fe", ["npm", "run", "dev", "--", "--port", str(FE_PORT), "--strictPort"], FE_ENV, ROOT / "frontend")
    wait_http(f"http://127.0.0.1:{AI_PORT}/health")
    wait_http(f"http://127.0.0.1:{BE_PORT}/api/health")
    wait_http(f"http://localhost:{FE_PORT}/")
    state = {"be": f"http://localhost:{BE_PORT}", "fe": f"http://localhost:{FE_PORT}",
             "db_url": BE_ENV["DATABASE_URL"], "be_env": BE_ENV,
             "scenario_file": str(STATE_DIR / "scenario")}
    (STATE_DIR / "state.json").write_text(json.dumps(state, indent=1))
    return state


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] not in ("up", "run"):
        print(__doc__)
        return 2
    try:
        state = up()
        print(json.dumps({k: v for k, v in state.items() if k != "be_env"}, ensure_ascii=False))
        if args[0] == "up":
            signal.pause()
            return 0
        cmd = args[args.index("--") + 1 :]
        return subprocess.run(cmd, cwd=ROOT, env={**os.environ, "E2E_STATE": str(STATE_DIR / "state.json")}).returncode
    except KeyboardInterrupt:
        return 130
    finally:
        down()


if __name__ == "__main__":
    sys.exit(main())
