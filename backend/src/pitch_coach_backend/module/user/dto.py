import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str
    # 1초 원자료를 Take 가 끝난 뒤에도 남겨 다시 쓰는 것에 대한 동의 (마이페이지)
    raw_data_consent: bool
    # 사용자 테스트 참여 동의. 동의하지 않았으면 둘 다 null 이다
    test_consent_at: datetime | None
    test_consent_version: int | None


class UserSettingsUpdate(BaseModel):
    """마이페이지 설정. 보낸 칸만 바꾼다."""

    raw_data_consent: bool | None = None
    # 동의한 안내문 버전. 철회는 받지 않는다
    test_consent_version: Annotated[int, Field(strict=True, ge=1)] | None = None
