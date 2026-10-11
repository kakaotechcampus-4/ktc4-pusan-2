"""research 프로젝트의 폴더 위치. 어디서 실행해도 같은 곳을 가리키도록 이 파일 위치에서 구한다."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 재생 시나리오 (가상 발표 하나 + 기대 결과)
SCENARIOS_DIR = PROJECT_ROOT / "scenarios"

# 다시 만들 수 있는 출력. git 에 올리지 않는다
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
REPLAY_DIR = OUTPUTS_DIR / "replay"

# 버전별로 커밋하는 실험 지표(JSON)
RESULTS_DIR = PROJECT_ROOT / "reports" / "results"
