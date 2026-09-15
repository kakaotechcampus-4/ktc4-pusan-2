import boto3
from pitch_coach_backend.core.config import settings

s3 = boto3.client("s3", region_name=settings.s3_region)

def get_s3():
    yield s3