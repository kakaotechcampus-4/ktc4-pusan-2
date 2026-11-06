from unittest.mock import MagicMock

import pytest
from botocore.exceptions import (
    ClientError,
    CredentialRetrievalError,
    NoCredentialsError,
    SSOError,
    TokenRetrievalError,
)

MISSING_CREDENTIALS = (
    NoCredentialsError,
    SSOError,
    TokenRetrievalError,
    CredentialRetrievalError,
)

# 세션이 만료됐을 때 S3 가 돌려주는 코드. HEAD 응답에는 본문이 없어 botocore 가 상태 코드를 그대로
# Code 로 두므로 head_bucket 에서는 "400" 으로 보인다
EXPIRED_CREDENTIAL_CODES = frozenset({"400", "ExpiredToken", "InvalidToken", "RequestExpired"})


def _check_bucket(s3, bucket: str) -> None:
    """자격 증명이 없거나 만료됐으면 건너뛰고, 설정이 틀렸으면 실패로 표시한다."""
    try:
        s3.head_bucket(Bucket=bucket)
    except MISSING_CREDENTIALS as e:
        pytest.skip(f"Skipping S3 tests due to missing credentials: {e}")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in EXPIRED_CREDENTIAL_CODES:
            pytest.skip(f"Skipping S3 tests due to expired credentials: {code}")
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


@pytest.fixture(scope="module")
def bucket():
    from pitch_coach_backend.core.config import settings

    return settings.s3_bucket_name


@pytest.fixture(scope="module")
def s3_client(bucket: str):
    from pitch_coach_backend.core.s3 import get_s3

    s3 = get_s3()
    _check_bucket(s3, bucket)
    return s3


# S3 주입받기
def test_s3_connection(s3_client, bucket):
    response = s3_client.head_bucket(Bucket=bucket)
    assert response["ResponseMetadata"]["HTTPStatusCode"] == 200


# ── 건너뛸지 실패로 볼지 판정: 실제 AWS 없이 가짜 S3 로 본다 ─────────────────────


def _s3_raising(error: Exception) -> MagicMock:
    s3 = MagicMock()
    s3.head_bucket.side_effect = error
    return s3


def _client_error(code: str, status: int) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": "fake"},
            "ResponseMetadata": {"HTTPStatusCode": status, "HTTPHeaders": {}},
        },
        "HeadBucket",
    )


@pytest.mark.parametrize(
    ("code", "status", "message"),
    [
        ("PermanentRedirect", 301, "리전"),
        ("301", 301, "리전"),
        ("AccessDenied", 403, "권한"),
        ("403", 403, "권한"),
        ("NoSuchBucket", 404, "존재"),
        ("404", 404, "존재"),
    ],
)
def test_wrong_bucket_settings_fail_instead_of_skip(code: str, status: int, message: str) -> None:
    with pytest.raises(pytest.fail.Exception, match=message):
        _check_bucket(_s3_raising(_client_error(code, status)), "fake-bucket")


@pytest.mark.parametrize(
    "error",
    [
        NoCredentialsError(),
        TokenRetrievalError(provider="sso", error_msg="fake"),
        _client_error("400", 400),
        _client_error("ExpiredToken", 400),
        _client_error("RequestExpired", 400),
    ],
    ids=["no-credentials", "token-retrieval", "head-400", "expired-token", "request-expired"],
)
def test_missing_or_expired_credentials_skip(error: Exception) -> None:
    with pytest.raises(pytest.skip.Exception):
        _check_bucket(_s3_raising(error), "fake-bucket")


def test_other_s3_errors_are_not_hidden() -> None:
    with pytest.raises(ClientError):
        _check_bucket(_s3_raising(_client_error("InternalError", 500)), "fake-bucket")
