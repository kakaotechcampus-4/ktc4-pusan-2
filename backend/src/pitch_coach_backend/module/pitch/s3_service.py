
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import UploadFile

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.s3 import get_s3


def upload(file: UploadFile, key: str):
    try:
        get_s3().upload_fileobj(
            file.file,
            settings.s3_bucket_name,
            key,
            ExtraArgs={"ContentType": file.content_type or "application/octet-stream"} # 알려지지 않은 파일의 경우 octet-stream 으로 처리한다.
        )
    except (ClientError, BotoCoreError) as e:
        raise Exception(f"S3에 파일 업로드 실패: {str(e)}")

    return key
