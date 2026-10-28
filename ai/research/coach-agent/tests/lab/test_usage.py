"""출력 토큰 기록(coach_lab.usage)과 코칭 계획 LLM 의 인자 전달. LLM 은 부르지 않는다."""

from coach_lab.llm import Settings, plan_llm
from coach_lab.usage import UsageLog, recommended_cap


def test_recommended_cap_has_a_floor():
    assert recommended_cap(486) == 2048
    assert recommended_cap(1025) == 3072


def test_errors_or_truncation_leave_the_cap_undecided():
    log = UsageLog()
    log.on_llm_error(ValueError())
    assert log.summary()["recommended_cap"] is None


def test_plan_llm_passes_streaming_cap_and_callbacks():
    log = UsageLog()
    llm = plan_llm(
        Settings(base_url="http://x/v1", api_key="k", model="m"),
        streaming=True,
        max_completion_tokens=2048,
        callbacks=[log],
    )
    client = getattr(llm.first, "bound", llm.first)
    assert client.max_tokens == 2048 and client.callbacks == [log] and client.streaming is True
