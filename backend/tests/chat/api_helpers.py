"""대화 API 시험이 함께 쓰는 도우미. 픽스처는 같은 폴더의 conftest.py 에 있다."""

import uuid

from pitch_coach_backend.core.security import create_access_token


def bearer(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}
