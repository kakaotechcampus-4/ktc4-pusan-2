from functools import cache

import boto3

from pitch_coach_backend.core.config import settings


@cache
def get_s3():
    return boto3.Session(profile_name=settings.aws_profile).client("s3", region_name=settings.s3_region)
