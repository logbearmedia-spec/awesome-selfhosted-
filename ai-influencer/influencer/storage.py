"""Publishing media to a public URL.

Instagram's Graph API does not accept file uploads: it fetches the media from a
URL you give it, so generated images have to be reachable over public HTTP
before a post can be created. Two backends cover the usual setups — a directory
served by an existing web server, and any S3-compatible object store (AWS S3,
MinIO, Garage, Ceph), signed here with SigV4 so there is no boto3 dependency.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from .config import Config


class StorageError(Exception):
    """Raised when media could not be made publicly reachable."""


@dataclass(frozen=True)
class StoredMedia:
    url: str
    local_path: Path | None


def _require(options: dict[str, Any], key: str, provider: str) -> Any:
    value = options.get(key)
    if value in (None, ""):
        raise StorageError(
            f"storage provider {provider!r} needs storage.options.{key} to be set"
        )
    return value


def _store_local(name: str, data: bytes, content_type: str, cfg: Config) -> StoredMedia:
    directory = Path(cfg.storage.directory)
    if not directory.is_absolute():
        directory = cfg.source.parent / directory
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)

    target = directory / name
    target.write_bytes(data)

    if not cfg.storage.public_base_url:
        # Still useful for dry runs and previews; publishing will refuse this.
        return StoredMedia(url=target.resolve().as_uri(), local_path=target)
    return StoredMedia(
        url=f"{cfg.storage.public_base_url}/{quote(name)}", local_path=target
    )


def _sign_key(secret: str, datestamp: str, region: str, service: str) -> bytes:
    def sign(key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()

    key = sign(f"AWS4{secret}".encode("utf-8"), datestamp)
    key = sign(key, region)
    key = sign(key, service)
    return sign(key, "aws4_request")


def _store_s3(name: str, data: bytes, content_type: str, cfg: Config) -> StoredMedia:
    import requests

    options = cfg.storage.options
    bucket = _require(options, "bucket", "s3")
    access_key = _require(options, "access_key_id", "s3")
    secret_key = _require(options, "secret_access_key", "s3")
    region = options.get("region", "us-east-1")
    endpoint = str(options.get("endpoint", "https://s3.amazonaws.com")).rstrip("/")
    path_style = bool(options.get("path_style", False))
    prefix = str(options.get("prefix", "")).strip("/")
    acl = options.get("acl", "public-read")

    key = f"{prefix}/{name}" if prefix else name

    split = urlsplit(endpoint)
    if not split.scheme or not split.netloc:
        raise StorageError(f"storage.options.endpoint is not a full URL: {endpoint!r}")

    if path_style:
        host = split.netloc
        canonical_uri = "/" + quote(f"{bucket}/{key}", safe="/~")
    else:
        host = f"{bucket}.{split.netloc}"
        canonical_uri = "/" + quote(key, safe="/~")
    request_url = f"{split.scheme}://{host}{canonical_uri}"

    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(data).hexdigest()

    headers = {
        "content-type": content_type,
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    if acl:
        headers["x-amz-acl"] = str(acl)

    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(
        f"{key_name}:{headers[key_name].strip()}\n" for key_name in sorted(headers)
    )
    canonical_request = "\n".join(
        ["PUT", canonical_uri, "", canonical_headers, signed_headers, payload_hash]
    )

    scope = f"{datestamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signature = hmac.new(
        _sign_key(secret_key, datestamp, region, "s3"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    headers["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    response = requests.put(request_url, data=data, headers=headers, timeout=120)
    if response.status_code >= 400:
        raise StorageError(
            f"upload to {request_url} failed ({response.status_code}): {response.text}"
        )

    if cfg.storage.public_base_url:
        return StoredMedia(url=f"{cfg.storage.public_base_url}/{quote(key)}", local_path=None)
    return StoredMedia(url=request_url, local_path=None)


def store(name: str, data: bytes, content_type: str, cfg: Config) -> StoredMedia:
    if cfg.storage.provider == "local":
        return _store_local(name, data, content_type, cfg)
    if cfg.storage.provider == "s3":
        return _store_s3(name, data, content_type, cfg)
    raise StorageError(
        f"unknown storage provider {cfg.storage.provider!r}; expected local or s3"
    )
