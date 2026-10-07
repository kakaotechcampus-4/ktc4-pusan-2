from pitch_coach_backend.core.exceptions import (
    AppException,
    ConflictException,
    NotFoundException,
)


class NonExistentPitch(AppException):
    status_code = 404
    code = "PITCH_NOT_FOUND"
    message = "존재하지 않는 발표자료입니다."

class NonExistentPresentationVersion(AppException):
    status_code = 404
    code = "PRESENTATION_VERSION_NOT_FOUND"
    message = "존재하지 않는 발표자료 버전입니다."

class NonExistentEvaluation(AppException):
    status_code = 404
    code = "EVALUATION_NOT_FOUND"
    message = "존재하지 않는 평가 기준 버전입니다."


class StandardParseFailed(AppException):
    status_code = 502
    code = "STANDARD_PARSE_FAILED"
    message = "평가 기준을 나누지 못했습니다. 잠시 후 다시 시도해 주세요."


class InvalidScript(AppException):
    code = "INVALID_SCRIPT"
    message = "대본이 올바르지 않습니다."


class NonExistentScript(NotFoundException):
    code = "SCRIPT_NOT_FOUND"
    message = "존재하지 않는 대본입니다."


class ScriptParseInProgress(ConflictException):
    code = "SCRIPT_PARSE_IN_PROGRESS"
    message = "대본을 분석하고 있습니다. 잠시 후 다시 확인해 주세요."


class ScriptAlreadyParsed(ConflictException):
    code = "SCRIPT_ALREADY_PARSED"
    message = "이미 분석이 끝난 대본입니다."


class ScriptReuploadRequired(ConflictException):
    code = "SCRIPT_REUPLOAD_REQUIRED"
    message = "이 대본은 원문이 없어 다시 분석할 수 없습니다. 대본을 다시 올려 주세요."
