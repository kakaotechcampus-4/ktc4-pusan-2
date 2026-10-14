from .config import FAST_THRESHOLD, PAUSE_THRESHOLD, SLOW_THRESHOLD, WARNING_STREAK_THRESHOLD
from .schemas import SpeedResult, Word


def update_warning_streak(is_triggered: bool, streak: int, message: str) -> int:
    """경고 누적 계산."""
    if not is_triggered:
        return 0
    streak += 1
    if streak >= WARNING_STREAK_THRESHOLD:
        print(f"{message} 상태 누적 {streak}회. 개선 필요.")
    return streak


def analyze_sentence(sentence: list[Word]) -> SpeedResult:
    """문장 하나의 빠르기(cpm) · 판정 · pause 수."""
    # cpm 계산
    total_characters = sum(len(word.word) for word in sentence)
    speak_duration = sum(word.end - word.start for word in sentence) / 1000
    speed = total_characters / speak_duration * 60 if speak_duration > 0 else 0

    # 말하기 속도 판정
    if speed < SLOW_THRESHOLD:
        level = "slow"
    elif speed > FAST_THRESHOLD:
        level = "fast"
    else:
        level = "normal"

    # Count pauses
    pauses = 0
    for j in range(len(sentence) - 1):
        pause_duration = sentence[j + 1].start - sentence[j].end
        if pause_duration > PAUSE_THRESHOLD:
            pauses += 1

    return SpeedResult(speed=speed, level=level, pauses=pauses)


def analyze(words: list[list[Word]]) -> None:
    """빠르기와 필러 분석. 문장마다 결과와 경고 누적을 출력한다."""
    level_warning_count, pauses_warning_count = 0, 0

    for sentence in words:
        result = analyze_sentence(sentence)
        print(
            f"Speed: {result.speed:.2f} chars/min, Level: {result.level}, Pauses: {result.pauses}"
        )

        # 경고 누적 계산
        level_warning_count = update_warning_streak(
            result.level != "normal", level_warning_count, f"말의 속도 불안정({result.level})"
        )
        pauses_warning_count = update_warning_streak(
            result.pauses > 0, pauses_warning_count, "pause 과다"
        )
        print(level_warning_count, pauses_warning_count)
