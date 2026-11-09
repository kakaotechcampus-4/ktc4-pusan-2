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
# 중요도 가중치 SCORE_WEIGHT (critical 3 / high 2 / normal 1) 는 최상위 `config.py` 에 정의된 값을 그대로 쓴다


# ── 결과 상세 (detail) — 기존 점수와 따로 내는 지표. 값은 연구 실험(12)으로 정한다 ─────────────
# 핵심 내용 전달률(상쇄 없음): 다르게 말한 것을 음수로 두지 않아 다른 내용을 깎지 않는다
DELIVERY_SCORE = {"covered": 1.0, "partial": 0.5, "missing": 0.0, "contradicted": 0.0}
CRITICAL_GAP_IMPORTANCE = {"critical", "high"}  # 놓친 중요 내용 목록에 넣는 중요도
TAKE_CRITICAL_GAPS_MAX = 10  # Take 요약에 싣는 놓친 중요 내용 수 (중요도 · 장 순)
# 표현 방식: said 문장의 lexical_coverage 가 이 값 이상이면 대본 표현에 가까움, 미만이면 바꿔 말함, 사이는 구분 어려움
EXPRESSION_VERBATIM_MIN = 0.9
EXPRESSION_PARAPHRASE_MAX = 0.8
# STT 완전성: 장 구간 중 기록 없는 시간이 이 비율을 넘으면 모름, ok 아닌 시간이 둘 중 큰 값 이상이면 불완전
STT_UNOBSERVED_MAX_RATIO = 0.5
STT_GAP_MIN_MS = 2000
STT_GAP_RATIO = 0.1
# 실패 장 간이 값: 문장마다 대본 내용 형태소가 STT 짝 구간에 나온 비율(lexical_coverage)이 HI 이상이면 1,
# LO 이상이면 0.5, 아니면 0 으로 보고 평균한 값(간이 전달률)을 3단계로 보인다 (이상이면 mostly, 미만이면 little)
# 문장 경계 LO · HI 는 실험 12(가상대본1 로 고르고 가상대본2 로 확인: 단계 일치율 0.889 · 심각한 어긋남 0)
FALLBACK_SENTENCE_LO = 0.35
FALLBACK_SENTENCE_HI = 0.45
FALLBACK_BAND_MOSTLY = 0.7
FALLBACK_BAND_LITTLE = 0.3
# 전달 단위 실패를 문장 단위로 복구한 평가 기준의 장 상세를 provisional 로 둘지. 복구 실험 11
# (`reports/results/sentence_fallback.json`, 문장 720개)에서 정확도 −0.14%p · 심각한 오판 +0.14%p 로
# 미리 정한 기준(≤ 1%p · ≤ 0.5%p) 안이라 정상(ok)으로 둔다
SENTENCE_FALLBACK_PROVISIONAL = False

# ── 말의 구성 (speech_mix) ─────────────────────────────────
# 짝 있는 문장 안에서 대본 어휘에 없는 형태소가 이어진 구간(꼬리)도 후보로 볼지. 실험 12 의 자연 데이터 라벨
# (후보 221개, 두 라벨러 일치 96.4%)에서 이 구간은 대부분 바꿔 말한 대본 내용이었다 — 짝 없는 문장만 보면
# precision 1.0(개발 · 시험), 꼬리를 더하면 시험 0.06~0.88. 그래서 끈다(대본 문장에 붙여 말한 곁말은 놓친다)
OFFSCRIPT_IN_SENTENCE = False
OFFSCRIPT_MIN_TOKENS = 2  # 꼬리를 켤 때 후보 구간의 최소 내용 형태소 수
UNALIGNED_SCRIPT_OVERLAP = 0.5  # 짝 없는 STT 문장이라도 이 장 대본 어휘가 이만큼이면 대본 내용
OTHER_SLIDE_OVERLAP = (
    0.6  # 후보 구간 어휘 중 다른 장 대본 어휘의 비율이 이 값 이상이면 다른 장 내용
)
OTHER_SLIDE_MIN_TOKENS = 3
# 화면에 따로 보일 범주 (정확도 검증을 통과한 것만). 나머지는 off_script 로 합친다
SPEECH_MIX_VALIDATED = {"script", "other_slide", "procedural", "off_script"}
# 대본 밖 구간의 LLM 분류(관련 곁말 · 무관한 말). 실제 발표 데이터로 재기 전까지 꺼 둔다
OFFSCRIPT_LLM_ENABLED = False

# 진행 말(인사 · 감사 · 마무리 · 장 넘김 · 청중 안내 · 질문 받기) 정규식. 구간 글에서 찾는다.
# 뜻이 있는 말을 넓게 잡지 않도록 보수적으로 두고, 구간의 내용 형태소가 PROCEDURAL_MAX_TOKENS 개 이하일 때만 쓴다
PROCEDURAL_PATTERNS = (
    r"안녕하(세요|십니까)",
    r"감사(합니다|드립니다|드려요|해요)",
    r"고맙(습니다|어요)",
    r"(들어|들어 )주셔서",
    r"경청해 ?주셔서",
    r"발표를? (마치|마무리|끝내)",
    r"여기까지(입니다|예요|였습니다|가)",
    r"여기까지 (발표|설명|말씀|하겠|드리)",
    r"이상으로 .{0,8}(발표|설명|마치|마무리|드렸)",
    r"^(네|자|그럼)?[ ,]*이상입니다",
    r"다음 (장|슬라이드|페이지)(으?로|에서?|을|를|은|는|이|입니다|\b)",
    r"넘어가(겠|볼|도록|죠|요)",
    r"질문(이|을)? (있으시|있으면|받겠|받을게|주세요|주시)",
    r"궁금한 (점|것)(이|은)? 있",
    r"(화면|슬라이드|그림|표|그래프|자료)(을|를|에서)? 보(시면|시는|십시오|세요)",
    r"(발표를? |그럼 |이제 |지금부터 |자,? )시작(하겠|해 보겠|할게|해볼게)",
)
PROCEDURAL_MAX_TOKENS = 6
