"""리뷰의 버전 값. 응답 meta 에 실려 나가고, BE 가 결과와 함께 저장한다.

- `SCHEMA_VERSION`: 요청 · 응답 모양이 바뀔 때 올린다
- `FEATURE_VERSION`: 리뷰 기능 버전. 같은 입력에 다른 결론이 나오게 규칙을 바꾸면 올린다

기준값 · 가중치 같은 설정 조정은 버전을 올리지 않는다.
응답 `meta.criteria_versions.review` 의 해시가 어떤 설정으로 판단했는지 남긴다.
"""

#: 1.0: 명세 5-2 · 5-3 의 요청 · 응답.
#: recurring_issues 에 dimension 을 더했다 (README 의 팀 논의 참고)
SCHEMA_VERSION = "1.0"
#: review-1.0: 규칙 단계 + 기본 문장. LLM 단계는 아직 없다
#: review-1.1: LLM 단계 (문장 · 다음 미션 고르기, 1번). LLM 이 없으면 review-1.0 과 같은 결과
#: review-1.2: 판단 가능한 차원이 모두 STABLE 이면 기본 요약을 '전반적으로 보통'으로,
#: LLM 비교 입력에 script_mode 와 해석 규칙, 출력 area · slide_number 계약을 좁힘 (#589)
FEATURE_VERSION = "review-1.2"
