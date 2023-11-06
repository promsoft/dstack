from typing import Optional

import botocore.exceptions
from boto3 import Session

from dstack._internal.server import settings


class S3Storage:
    def __init__(
        self,
        bucket: str,
        region: str,
    ):
        self._session = Session()
        self._client = self._session.client("s3", region_name=region)
        self.bucket = bucket

    def upload_code(
        self,
        project_id: str,
        repo_id: str,
        code_hash: str,
        blob: bytes,
    ):
        self._client.put_object(
            Bucket=self.bucket,
            Key=_get_code_key(project_id, repo_id, code_hash),
            Body=blob,
        )

    def get_code(
        self,
        project_id: str,
        repo_id: str,
        code_hash: str,
    ) -> Optional[bytes]:
        try:
            response = self._client.get_object(
                Bucket=self.bucket,
                Key=_get_code_key(project_id, repo_id, code_hash),
            )
        except botocore.exceptions.ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchKey":
                return None
            raise e
        return response["Body"].read()


def _get_code_key(project_id: str, repo_id: str, code_hash: str) -> str:
    return f"data/projects/{project_id}/codes/{repo_id}/{code_hash}"


_default_storage = None


def init_default_storage():
    global _default_storage
    _default_storage = S3Storage(
        bucket=settings.SERVER_BUCKET,
        region="eu-west-1",
    )


def get_default_storage() -> Optional[S3Storage]:
    return _default_storage
