"""LLM 출력 후처리. LLM 없이 도는 순수 함수만 둡니다."""

import logging
import re

logger = logging.getLogger(__name__)


def clean_script(text: str) -> str:
    """LLM이 섞어 낸 마크다운 문법을 script에서 제거합니다."""
    # 헤더 제거: 줄 맨 앞의 #(1~6개) + 공백일 때만 (#1, #해시태그 등은 유지)
    text = re.sub(r"^#{1,6}[ \t]+", "", text, flags=re.MULTILINE)
    # 굵게/기울임 강조 기호 제거: **text**, *text*
    # - 기호 안쪽에 공백이 붙으면 강조가 아님 (4 * 6 * 2 유지)
    # - 여는 기호 앞에 글자가 붙으면 강조가 아님 (4*6, 2**10 유지)
    # - 닫는 기호 뒤는 조사가 바로 붙을 수 있어 제한하지 않음 (**핵심**을 -> 핵심을)
    # - _text_, __text__ 는 식별자·수식과 겹칠 위험이 커서 처리하지 않음
    text = re.sub(r"(?<![\w*])\*\*(?=\S)(.+?)(?<=\S)\*\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?!\*)", r"\1", text)
    # 백틱 제거
    text = text.replace("`", "")
    # 마크다운 링크 [텍스트](URL) -> 텍스트 (슬라이드 마커 [슬라이드 1] 등은 뒤에 괄호가 없어 영향 없음)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # 줄 안의 연속 공백/탭만 하나로 정리, 문단 구분은 최대 빈 줄 하나(\n\n)까지 유지
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def find_highlights(script: str, keywords: list[str]) -> list[str]:
    """keyword가 script에 처음 등장하는 위치를 "start:end" 문자열로 돌려줍니다.

    오프셋은 clean_script를 거친 script 기준이어야 합니다.
    script에 없는 keyword는 건너뜁니다.
    """
    highlights = []
    for keyword in keywords:
        start = script.find(keyword)
        if start == -1:
            logger.warning("keyword %r not found in script", keyword)
            continue
        highlights.append(f"{start}:{start + len(keyword)}")
    return highlights
