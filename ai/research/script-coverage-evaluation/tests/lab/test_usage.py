"""출력 토큰 기록(coverage_lab.usage)과 역할별 상한 계산. LLM 은 부르지 않는다."""

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from coverage_lab.llm import Settings, structured_llm
from coverage_lab.usage import UsageLog, output_of, recommended_cap
from script_coverage.stt_evaluation.schemas import VerifierResult


def _result(tokens: int | None, reason: str | None, *, streaming: bool = True) -> LLMResult:
    usage = {"input_tokens": 10, "output_tokens": tokens, "total_tokens": 10 + (tokens or 0)}
    message = AIMessage(content="", usage_metadata=usage if tokens is not None else None)
    info = {"finish_reason": reason} if reason else {}
    llm_output = None if streaming else {"token_usage": {"completion_tokens": tokens}}
    return LLMResult(
        generations=[[ChatGeneration(message=message, generation_info=info)]],
        llm_output=llm_output,
    )


def test_output_of_reads_streaming_and_non_streaming_shapes():
    assert output_of(_result(321, "stop")) == (321, "stop")
    assert output_of(_result(123, "length", streaming=False)) == (123, "length")
    assert output_of(_result(None, None)) == (None, None)


def test_recommended_cap_is_twice_the_max_rounded_up_with_a_floor():
    assert recommended_cap(100) == 2048  # 최소
    assert recommended_cap(1024) == 2048
    assert recommended_cap(1025) == 3072  # 2050 → 3072
    assert recommended_cap(3000) == 6144


def test_summary_counts_and_cap():
    log = UsageLog()
    for tokens in (500, 900, 1500, 700):
        log.on_llm_end(_result(tokens, "stop"))
    log.on_llm_end(_result(None, "stop"))
    log.on_llm_error(TimeoutError())
    s = log.summary()
    assert s["calls"] == 5 and s["errors"] == 1 and s["unknown_usage"] == 1
    assert s["output_tokens"] == {"min": 500, "median": 900, "p95": 1500, "max": 1500}
    assert s["finish_reason"] == {"stop": 5}
    assert s["recommended_cap"] == 3072


def test_truncated_call_leaves_the_cap_undecided():
    # 잘린 호출이 있으면 잰 최대가 진짜 최대가 아니다
    log = UsageLog()
    log.on_llm_end(_result(800, "stop"))
    log.on_llm_end(_result(2048, "length"))
    assert log.summary()["recommended_cap"] is None


def test_structured_llm_passes_the_cap_and_callbacks_to_the_client():
    log = UsageLog()
    llm = structured_llm(
        VerifierResult,
        Settings(base_url="http://x/v1", api_key="k", model="m"),
        streaming=True,
        max_completion_tokens=4096,
        callbacks=[log],
    )
    client = llm.first  # with_structured_output 은 (client | parser) 꼴이다
    assert client.max_tokens == 4096 and client.callbacks == [log] and client.streaming is True
