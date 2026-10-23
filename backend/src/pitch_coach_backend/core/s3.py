from functools import cache

import boto3
from botocore.config import Config

from pitch_coach_backend.core.config import settings


@cache
def get_s3():
    return boto3.Session(profile_name=settings.aws_profile).client(
        "s3",
        region_name=settings.s3_region,
        endpoint_url=f"https://s3.{settings.s3_region}.amazonaws.com",
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )
