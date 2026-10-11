"""research 프로젝트의 폴더 위치. 어디서 실행해도 같은 곳을 가리키도록 이 파일 위치에서 구한다."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = PROJECT_ROOT / "data" / "virtual"
SCRIPT_DIR = DATA_DIR / "scripts"
STT_DIR = DATA_DIR / "stt"
# 정답 라벨. 성능 측정에서만 읽고, 평가 파이프라인은 보지 않는다
LABEL_DIR = DATA_DIR / "stt_labels"

OUTPUTS_DIR = PROJECT_ROOT / "outputs"
DB_PATH = OUTPUTS_DIR / "rubrics.sqlite"  # LLM 응답 캐시 + 평가 기준 + 평가 결과

# 실험이 남기는 핵심 지표(JSON). 보고서 노트북이 읽는다
RESULTS_DIR = PROJECT_ROOT / "reports" / "results"
