import uuid

from pydantic import BaseModel, ConfigDict


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str
    # 1초 원자료를 Take 가 끝난 뒤에도 남겨 다시 쓰는 것에 대한 동의 (마이페이지)
    raw_data_consent: bool


class UserSettingsUpdate(BaseModel):
    """마이페이지 설정. 보낸 칸만 바꾼다."""

    raw_data_consent: bool | None = None
