from enum import StrEnum


class SuggestionCategory(StrEnum):
    POSITIVE = "POSITIVE"
    IMPROVEMENT = "IMPROVEMENT"
    PROBLEM = "PROBLEM"
    QUESTION = "QUESTION"
    OTHER = "OTHER"
