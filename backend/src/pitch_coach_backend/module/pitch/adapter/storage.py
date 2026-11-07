import logging
import uuid

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import UploadFile

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.s3 import get_s3
from pitch_coach_backend.module.pitch.exception import StorageError

logger = logging.getLogger(__name__)


def _failed(action: str, cause: object) -> StorageError:
    """원인을 로그에 남기고 예외를 만든다. AppException 핸들러는 로그를 남기지 않는다."""
    logger.error("S3 %s 실패: %s", action, cause)
    return StorageError()


def pitch_prefix(pitch_id: uuid.UUID) -> str:
    """발표의 파일이 모두 들어가는 경로. 발표를 지울 때 이 아래를 지운다."""
    return f"pitches/{pitch_id}/"


def presentation_key(pitch_id: uuid.UUID, presentation_id: object, suffix: str) -> str:
    return f"{pitch_prefix(pitch_id)}presentations/{presentation_id}.{suffix}"


def upload(file: UploadFile, key: str, content_type: str):
    try:
        get_s3().upload_fileobj(
            file.file,
            settings.s3_bucket_name,
            key,
            ExtraArgs={"ContentType": content_type},
        )
    except (ClientError, BotoCoreError) as e:
        raise _failed("파일 업로드", e) from e

    return key


def upload_bytes(data: bytes, key: str, content_type: str) -> str:
    try:
        get_s3().put_object(
            Bucket=settings.s3_bucket_name, Key=key, Body=data, ContentType=content_type
        )
    except (ClientError, BotoCoreError) as e:
        raise _failed("파일 업로드", e) from e
    return key


def generate_presigned_url(key: str, expiration: int = 3600):
    try:
        url = get_s3().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket_name, "Key": key},
            ExpiresIn=expiration,
        )
    except (ClientError, BotoCoreError) as e:
        raise _failed("presigned URL 생성", e) from e

    return url


def delete(key: str) -> None:
    """파일 하나를 지운다. 없는 파일이어도 성공이다 (S3 DeleteObject)."""
    try:
        get_s3().delete_object(Bucket=settings.s3_bucket_name, Key=key)
    except (ClientError, BotoCoreError) as e:
        raise _failed("파일 삭제", e) from e


def delete_prefix(prefix: str) -> None:
    """prefix 아래 파일을 모두 지운다. 목록과 삭제 모두 한 번에 1000개까지라 페이지마다 지운다."""
    s3 = get_s3()
    try:
        pages = s3.get_paginator("list_objects_v2").paginate(
            Bucket=settings.s3_bucket_name, Prefix=prefix
        )
        for page in pages:
            objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if not objects:
                continue
            result = s3.delete_objects(
                Bucket=settings.s3_bucket_name, Delete={"Objects": objects, "Quiet": True}
            )
            # 요청은 성공해도 파일별 실패는 Errors 로만 온다
            if result.get("Errors"):
                raise _failed("파일 삭제", result["Errors"])
    except (ClientError, BotoCoreError) as e:
        raise _failed("파일 삭제", e) from e
