"""STT 평가의 임계값과 점수표. 값은 가상 STT 와 정답 라벨로 맞춘 것이라 바꾸면 채점 결과가 달라진다."""

# 충돌로 볼 기준. 가상 STT 에서 빠뜨린 문장의 단어 비율은 최대 0.5, 말한 문장(그대로·의역)은 한 문장을 빼면 0.2 이상이었다
LEXICAL_PRESENT = 0.6  # LLM 이 missing 이라는데 대본 단어가 이만큼 나왔으면 의심
LEXICAL_ABSENT = 0.2  # LLM 이 said 라는데 대본 단어가 이만큼도 안 나왔으면 의심. LLM 근거 문장에도 같은 기준을 쓴다
# 수치·이름을 빠뜨리거나 어림하지 않은 partial 문장(정답 라벨)의 단어 비율은 최대 0.63, 말한 문장은 81% 가 0.8 이상이었다
LEXICAL_COMPLETE = (
    0.8  # LLM 이 partial 이라는데 빠진 수치·이름이 없고 대본 단어가 이만큼 나왔으면 의심
)


WORD_DISTANCE = (
    0.34  # 발음 거리가 이 값 이하면 비슷한 단어 (2음절 단어에서 한 음절의 자모 두 개까지)
)
SIMILAR_SOUNDS = {"similar", "near"}  # 비슷한 수치로 보는 발음 관계 (`facts.mismatch_signals`)


# 어림 표현: 앞 값이 실제보다 작다(넘게) / 크다(가까이) / 방향 없음(약)
APPROX_LOWER = r"넘게|넘는|넘었|넘어|넘습|이상|남짓|조금 넘"
APPROX_UPPER = r"가까이|가까운|거의|안 되는|안되는|안 돼|못 미치|조금 안"
APPROX_ANY = r"약|대략|정도|쯤|가량|내외|안팎|얼추|대충"
PARTICLE = (
    r"\s?(?:이|가|을|를|은|는|도|으로|로)?\s?"  # "천만 건이 넘는" 처럼 수와 어림 표현 사이의 조사
)
# 수 파서가 수치에 붙여 읽은 한정어 ("약 40%", "85% 이상")
QUALIFIER_LOWER, QUALIFIER_UPPER, QUALIFIER_ANY = (
    {"이상", "초과", "남짓", "최소"},
    {"이하", "미만", "이내", "거의", "최대"},
    {"약", "대략", "가량", "정도", "내외"},
)


STATUS_SCORE = {"covered": 1.0, "partial": 0.5, "missing": 0.0, "contradicted": -0.5}
FACT_SCORE = {
    "matched": 1.0,
    "approximate": 0.5,
}  # 나머지(missing / mismatched)는 0, sound_alike 는 계산에서 뺀다
# 중요도 가중치 SCORE_WEIGHT (critical 3 / high 2 / normal 1) 는 `shared/rubric.py` 에 정의된 값을 그대로 쓴다
