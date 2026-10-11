from script_coverage.script_analysis.schemas import SlideScript
from script_coverage.script_analysis.semantic import EMPTY_SEMANTICS, semantic_user_message
from script_coverage.shared.text import normalize_script
from tests.unit.shared.slides import SLIDES


def test_semantic_user_message_format():
    slide = SlideScript(slide_number=6, script=SLIDES[("가상대본1", 6)])
    msg = semantic_user_message(slide, normalize_script(slide.script))
    assert msg == (
        "[슬라이드 6]\n"
        "[S0] 2025년 3월부터 8주 동안 제휴 도서관 3곳에서 시범 운영을 했습니다.\n"
        "[S1] 참여한 이용자는 1,860명이었고, 빈자리를 찾는 데 걸린 시간은 평균 23분에서 13분으로 약 42퍼센트 줄었습니다.\n"
        "[S2] 알림을 받고 10분 안에 자리를 잡은 비율은 76%였습니다.\n"
        "[S3] 노쇼 비율도 18%에서 7%로 낮아졌습니다.\n"
        "[S4] 시범 운영 뒤 설문에서 이용자의 85퍼센트 이상이 계속 쓰고 싶다고 답했습니다."
    )


def test_empty_semantics():
    assert EMPTY_SEMANTICS.sentence_roles == []
    assert EMPTY_SEMANTICS.key_points == []
