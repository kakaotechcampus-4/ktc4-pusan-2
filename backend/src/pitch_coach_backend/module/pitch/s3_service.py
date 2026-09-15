
from fastapi import UploadFile
from pitch_coach_backend.core import s3
from botocore.exceptions import ClientError, BotoCoreError
from pitch_coach_backend.core.config import settings

def upload(file: UploadFile, key: str):
    # s3에 파일 업로드
    try:
        s3.upload_fileobj(
            file.file, 
            settings.s3_bucket_name,
            key,
            ExtraArgs={"ContentType": file.content_type}
        )
    except (ClientError, BotoCoreError) as e:
        raise Exception(f"Failed to upload file to S3: {str(e)}")

    return generate_presigned_url(key)

def generate_presigned_url(key: str, expiration: int = 3600):
    return s3.generate_presigned_url(
        'get_object',
        Params={'Bucket': settings.s3_bucket_name, 'Key': key},
        ExpiresIn=expiration
    )