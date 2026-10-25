
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import UploadFile

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.s3 import get_s3


def upload(file: UploadFile, key: str, content_type: str | None = None):
    # content_type 을 주지 않으면 클라이언트가 보낸 값, 그것도 없으면 octet-stream 으로 처리한다.
    try:
        get_s3().upload_fileobj(
            file.file,
            settings.s3_bucket_name,
            key,
            ExtraArgs={
                "ContentType": content_type or file.content_type or "application/octet-stream"
            },
        )
    except (ClientError, BotoCoreError) as e:
        raise Exception(f"S3에 파일 업로드 실패: {str(e)}") from e

    return key

def generate_presigned_url(key: str, expiration: int = 3600):
    try:
        url = get_s3().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket_name, "Key": key},
            ExpiresIn=expiration
        )
    except (ClientError, BotoCoreError) as e:
        raise Exception(f"S3 presigned URL 생성 실패: {str(e)}") from e

    return url


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
                raise Exception(f"S3 파일 삭제 실패: {result['Errors']}")
    except (ClientError, BotoCoreError) as e:
        raise Exception(f"S3 파일 삭제 실패: {str(e)}") from e
