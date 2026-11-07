"""구분자 규칙 · 정제 · 프롬프트 · 출력의 뜻이 바뀌면 FEATURE_VERSION을,
필드 이름 변경 · 삭제 · 뜻 변경이면 SCHEMA_VERSION도 올립니다."""

SCHEMA_VERSION = "1.0"
#: script_parser-1.0: 첫 판
#: script_parser-1.1: 장 번호 4자리까지만 구분자, 5자리 이상은 본문 줄 (#589)
#: script_parser-1.2: 공백 · 탭만 있는 줄도 빈 줄로 줄임, 정제 뒤 0자 오류 문구 (#589 1-4 · 1-8)
#: script_parser-1.3: 구분자 확장 — [Slide N] · Slide N: · 줄 전체 슬라이드 N/Slide N ·
#:   앞의 # 제목 표시, 번호 앞 공백 생략 · 전각 콜론. 번호 목록(1. · 1))은 여전히 아님
FEATURE_VERSION = "script_parser-1.3"
