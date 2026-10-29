"""구분자 규칙 · 정제 · 프롬프트 · 출력의 뜻이 바뀌면 FEATURE_VERSION을,
필드 이름 변경 · 삭제 · 뜻 변경이면 SCHEMA_VERSION도 올립니다."""

SCHEMA_VERSION = "1.0"
#: script_parser-1.0: 첫 판
#: script_parser-1.1: 장 번호 4자리까지만 구분자, 5자리 이상은 본문 줄 (#589)
FEATURE_VERSION = "script_parser-1.1"
