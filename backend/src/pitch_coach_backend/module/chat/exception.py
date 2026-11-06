"""대화 API(화면 ↔ BE) 예외. code 는 chat-agent.md 5-9 의 오류 코드 그대로다."""

from fastapi import status

from pitch_coach_backend.core.exceptions import (
    AppException,
    ConflictException,
    NotFoundException,
)


class ChatDisabled(AppException):
    # 끄기 스위치(`agent_enabled`)가 꺼져 있다. FE 는 버튼을 숨긴다
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "AGENT_DISABLED"
    message = "지금은 코치 대화를 쓸 수 없어요."


class ChatTargetNotFound(NotFoundException):
    # 없는 대상과 남의 대상을 같은 404 로 돌려준다. 남의 대화가 있는지 알 수 없게 한다
    message = "기록을 찾을 수 없어요."


class ChatRequestConflict(ConflictException):
    # 같은 client_request_id 로 다른 내용을 보냈다.
    # 정상 화면에서는 생기지 않는 화면 버그 · 탭 경합이다
    code = "AGENT_REQUEST_CONFLICT"
    message = "보낸 질문과 내용이 달라 받지 못했어요. 다시 보내 주세요."


class ChatRunInProgress(ConflictException):
    code = "AGENT_RUN_IN_PROGRESS"
    message = "이 대화의 답변을 만들고 있어요."


class ChatCardChanged(ConflictException):
    # 화면이 보낸 args_hash 가 저장한 대기 행동과 다르다. 화면이 본 카드와 실행할 인자가 다르다
    code = "APPROVAL_HASH_MISMATCH"
    message = "요청이 변경되어 다시 확인이 필요해요."


class ChatExpired(ConflictException):
    code = "AGENT_EXPIRED"
    message = "확인 시간이 지나 실행하지 않았어요. 필요하면 다시 말해 주세요."


class ChatCancelled(ConflictException):
    code = "AGENT_CANCELLED"
    message = "취소된 확인 요청이에요. 필요하면 다시 말해 주세요."


class ChatDailyLimit(AppException):
    # 사용자 하루 턴 한도. Retry-After 는 다음 Asia/Seoul 00:00 까지의 초
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "AGENT_DAILY_LIMIT"
    message = "오늘 이용 횟수를 모두 썼으니 내일 다시 이용해 주세요."


class ChatNotAccepted(AppException):
    # 메시지 · 실행 · 하루 한도를 커밋하기 전에 DB 가 잠깐 안 됐다
    # (연결 끊김 · 잠금 대기 · 시간 초과).
    # FE 는 같은 client_request_id 로 다시 보낸다
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "AGENT_NOT_ACCEPTED"
    message = "요청을 접수하지 못했으니 다시 보내 주세요."


class ChatBusy(AppException):
    # 서비스 전체 하루 상한. 사용자 한도와 같이 다음 Asia/Seoul 00:00 까지 기다리게 한다
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "AGENT_BUSY"
    message = "오늘은 사용량이 많아 잠시 뒤 다시 이용해 주세요."
