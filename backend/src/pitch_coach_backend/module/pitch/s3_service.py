
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import UploadFile

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.s3 import s3


def upload(file: UploadFile, key: str):
    try:
        s3.upload_fileobj(
            file.file,
            settings.s3_bucket_name,
            key,
            ExtraArgs={"ContentType": file.content_type or "application/octet-stream"}
        )
    except (ClientError, BotoCoreError) as e:
        raise Exception(f"S3에 파일 업로드 실패: {str(e)}")

    return key

def generate_presigned_url(key: str, expiration: int = 3600):
    return s3.generate_presigned_url(
        'get_object',
        Params={'Bucket': settings.s3_bucket_name, 'Key': key},
        ExpiresIn=expiration
    )
