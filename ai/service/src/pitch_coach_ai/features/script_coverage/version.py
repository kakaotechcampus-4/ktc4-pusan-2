# 응답 모양(계약) 버전. 응답 meta.schema_version 에 들어간다 (architecture.md 5절).
SCHEMA_VERSION = "1.0"

# 기능(모델) 버전. 출력의 의미가 바뀔 때만 올린다 (ai/README.md 규칙 5). 응답 meta.feature_version 에 들어간다.
FEATURE_VERSION = "script_coverage-1.0"

# 평가 기준(EvaluationRubric) 스키마 버전. 평가 기준 메타와 rubric_id 에 들어간다.
# 1.1: 전달 단위(content_units) 추가
# 1.2: meta.created_at 뺌 (코어는 시계를 쓰지 않는다)
RUBRIC_SCHEMA_VERSION = "1.2"
