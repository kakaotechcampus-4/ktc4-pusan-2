"""1차 분석 (의미 분석): 문장 역할 → Core Claim → Key Point 를 LLM 한 번으로 받는다."""

from ..shared.llm_step import config_hash
from ..shared.text import NormalizedScript
from .prompts.semantic import SEMANTIC_SYSTEM_PROMPT, SENTENCE_LINE, USER_MESSAGE_TEMPLATE
from .schemas import SlideScript, SlideSemanticAnalysis

EMPTY_SEMANTICS = SlideSemanticAnalysis(sentence_roles=[], core_claim="", key_points=[])


def semantic_config_hash(model: str) -> str:
    """프롬프트·메시지 형식·스키마·모델이 바뀌면 달라지는 해시. 같은 대본이라도 이 값이 다르면 다시 분석한다."""
    return config_hash(
        {
            "model": model,
            "prompt": SEMANTIC_SYSTEM_PROMPT,
            "user_message": USER_MESSAGE_TEMPLATE,
            "sentence_line": SENTENCE_LINE,
            "schema": SlideSemanticAnalysis.model_json_schema(),
        }
    )


def semantic_user_message(slide: SlideScript, norm: NormalizedScript) -> str:
    """정규화 단계에서 나눈 문장에 번호를 붙여 보낸다. LLM 은 문장을 복사하지 않고 번호로 가리킨다."""
    numbered = "\n".join(SENTENCE_LINE.format(index=s.index, text=s.text) for s in norm.sentences)
    return USER_MESSAGE_TEMPLATE.format(slide_number=slide.slide_number, script=numbered)


def analyze_semantics(slide: SlideScript, norm: NormalizedScript, llm) -> SlideSemanticAnalysis:
    """LLM API (의미 분석): 문장 역할 → Core Claim → Key Point 를 한 번의 호출로 받는다."""
    return llm.invoke(
        [
            ("system", SEMANTIC_SYSTEM_PROMPT),
            ("user", semantic_user_message(slide, norm)),
        ]
    )
