"""대본 분석의 경고 문구와 전달 단위 검증 기준."""

WARNING_TEXT = {
    "too_many_claims": "claim 문장이 여러 개",
    "uncovered_sentences": "어느 Key Point 에도 들어가지 않은 문장",
    "missing_sentence_roles": "역할이 빠진 문장",
    "invalid_sentence_id": "없는 문장 번호",
    "too_many_critical": "critical Key Point 가 여러 개",
    "no_critical_key_point": "critical Key Point 없음 (나열형 슬라이드면 정상)",
    "no_key_points": "Key Point 없음",
    "too_many_key_points": "Key Point 가 6개 초과",
    "key_point_without_sentence": "근거 문장이 없는 Key Point",
    "unsupported_number_in_key_point": "Key Point 내용에 대본에 없는 수치",
    "key_term_not_found": "대본에 없는 key_term",
    "key_term_not_fact": "사실로 올리지 않은 key_term",
    "unlinked_facts": "어느 Key Point 에도 연결되지 않은 사실",
    "unit_quote_not_found": "대본 문장에 없는 전달 단위 (버림)",
    "unit_quote_loose": "표현을 고쳐 옮겨 내용 형태소로 찾은 전달 단위",
    "unit_missing_fact": "어느 전달 단위에도 안 들어가 따로 보탠 수치·이름",
    "units_not_split": "전달 단위를 나누지 못해 문장 전체를 단위로 둔 문장",
    "too_many_units": "전달 단위가 6개를 넘는 문장",
}
REJECTION_TEXT = {
    "phrase": "4어절 이상의 구절",
    "common_in_deck": "이 발표의 여러 슬라이드에 나오는 일반 명사",
}

QUOTE_TAGS = {
    "NNG",
    "NNP",
    "NR",
    "SN",
    "SL",
    "SH",
    "VV",
    "VA",
    "XR",
    "MAG",
}  # quote 를 느슨하게 찾을 때 보는 내용 형태소
QUOTE_MATCH = 0.6  # quote 가 그대로 없을 때, 내용 형태소가 문장에 이 비율 이상 있으면 받는다
