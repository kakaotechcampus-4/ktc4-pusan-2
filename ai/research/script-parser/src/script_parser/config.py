"""기준값. 바꾸면 version.py의 feature_version을 올립니다."""

# target_ms가 없을 때 예상 시간의 기준 말 속도(분당 글자 수).
# 잠정값: FE 생성 화면 EST_CHARS_PER_MIN(264)과 맞췄다. 실제 발표 기록으로 다시 정한다
BASE_CPM = 264

# 키워드 개수 상한 (success는 장마다, fail은 대본 전체에서)
MAX_KEYWORDS = 7
MAX_KEYWORDS_FAIL = 15

# 용어 개수 상한 (BE와 맞춘 통신 값)
MAX_TERMS = 50
