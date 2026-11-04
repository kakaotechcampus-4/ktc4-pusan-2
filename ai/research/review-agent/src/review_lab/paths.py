"""research 프로젝트의 폴더 위치. 어디서 실행해도 같은 곳을 가리키도록 이 파일 위치에서 구한다."""

from pathlib import Path

from pitch_coach_ai.features.review import core as _review_core

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 공통 기준 Take 의 저장 결과 묶음 (리뷰 요청 모양)과 정답 리뷰
DATA_DIR = PROJECT_ROOT / "data"
TAKES_DIR = DATA_DIR / "takes"
ANSWERS_PATH = DATA_DIR / "answers.json"

# 다시 만들 수 있는 출력 (LLM 응답 캐시). git 에 올리지 않는다
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

# 버전별로 커밋하는 평가 지표(JSON)
RESULTS_DIR = PROJECT_ROOT / "reports" / "results"

# 코어의 요청 · 응답 예시 (서비스 계약 테스트가 읽는다). 원본은 서비스 코어 패키지 안 한 곳이고
# (연구 폴더 src/review/ 에서 코어와 함께 옮김), examples.py 가 여기에 다시 쓴다
EXAMPLES_DIR = Path(_review_core.__file__).resolve().parent / "examples" / "review"
