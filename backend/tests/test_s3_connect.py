
from botocore.exceptions import (
    ClientError,
    NoCredentialsError,
    SSOError,
    TokenRetrievalError,
    CredentialRetrievalError
)
import pytest

MISSING_CREDENTIALS = (
    ClientError,
    NoCredentialsError,
    SSOError,
    TokenRetrievalError,
    CredentialRetrievalError
)

@pytest.fixture(scope="module")
def bucket():
    from pitch_coach_backend.core.config import settings
    return settings.s3_bucket_name

@pytest.fixture(scope="module")
def s3_client(bucket: str):
    from pitch_coach_backend.core.s3 import s3

    try:
        s3.head_bucket(Bucket=bucket)
    except MISSING_CREDENTIALS as e:
        pytest.skip(f"Skipping S3 tests due to missing credentials: {e}")
    except ClientError as e:
        meta = e.response["ResponseMetadata"]
        status = meta["HTTPStatusCode"]
        if status == 301:
            actual = meta["HTTPHeaders"].get("x-amz-bucket-region", "?")
            pytest.fail(f"버킷 '{bucket}' 의 리전은 {actual} 입니다. S3_REGION 을 맞추세요.")
        if status == 403:
            pytest.fail(f"버킷 '{bucket}' 에 대한 접근 권한이 없습니다.")
        if status == 404:
            pytest.fail(f"버킷 '{bucket}' 이 존재하지 않습니다.")
        raise

    return s3

# S3 주입받기 
def test_s3_connection(s3_client, bucket):
    response = s3_client.head_bucket(Bucket=bucket)
    assert response["ResponseMetadata"]["HTTPStatusCode"] == 200
