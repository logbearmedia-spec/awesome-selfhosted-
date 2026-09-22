"""Publishing to Instagram through the Graph API.

Publishing is a three-step dance: create a media container pointing at a public
URL, wait for Instagram to finish downloading and processing it, then publish
the container. Containers can fail after being accepted, so the status poll is
not optional.

Requires an Instagram Professional (Business or Creator) account linked to a
Facebook Page, and a token with instagram_basic, instagram_content_publish and
pages_read_engagement. Personal accounts cannot publish through the API at all.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from .config import Config, ConfigError


class PublishError(Exception):
    """Raised when a post could not be published."""


@dataclass(frozen=True)
class PublishResult:
    media_id: str
    permalink: str
    dry_run: bool = False


class InstagramPublisher:
    def __init__(self, cfg: Config, *, dry_run: bool = False) -> None:
        self.cfg = cfg
        self.dry_run = dry_run
        self._session = None
        if not dry_run:
            if not cfg.instagram.ig_user_id or not cfg.instagram.access_token:
                raise ConfigError(
                    "publishing needs instagram.ig_user_id and instagram.access_token; "
                    "set the environment variables they reference, or use --dry-run"
                )

    @property
    def session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def _url(self, path: str) -> str:
        return f"{self.cfg.instagram.graph_base}/{self.cfg.instagram.api_version}/{path}"

    def _call(self, method: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        params = {**params, "access_token": self.cfg.instagram.access_token}
        response = self.session.request(method, self._url(path), params=params, timeout=60)
        try:
            payload = response.json()
        except ValueError as exc:
            raise PublishError(
                f"Graph API returned a non-JSON response ({response.status_code})"
            ) from exc
        if "error" in payload:
            error = payload["error"]
            raise PublishError(
                f"Graph API error {error.get('code')}: {error.get('message')} "
                f"(type {error.get('type')})"
            )
        if response.status_code >= 400:
            raise PublishError(f"Graph API returned {response.status_code}: {payload}")
        return payload

    def quota_usage(self) -> int:
        """Posts published in the trailing 24 hours, per Instagram's own counter."""
        if self.dry_run:
            return 0
        payload = self._call(
            "GET",
            f"{self.cfg.instagram.ig_user_id}/content_publishing_limit",
            {"fields": "quota_usage"},
        )
        entries = payload.get("data") or [{}]
        return int(entries[0].get("quota_usage", 0))

    def _create_container(self, params: dict[str, Any]) -> str:
        payload = self._call("POST", f"{self.cfg.instagram.ig_user_id}/media", params)
        container_id = payload.get("id")
        if not container_id:
            raise PublishError(f"no container id in response: {payload}")
        return container_id

    def _await_container(self, container_id: str, timeout: int = 300) -> None:
        deadline = time.monotonic() + timeout
        delay = 2.0
        while True:
            payload = self._call("GET", container_id, {"fields": "status_code,status"})
            status = payload.get("status_code")
            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise PublishError(
                    f"container {container_id} ended as {status}: "
                    f"{payload.get('status', 'no detail')}"
                )
            if time.monotonic() > deadline:
                raise PublishError(
                    f"container {container_id} was still {status} after {timeout}s"
                )
            time.sleep(delay)
            delay = min(delay * 1.5, 15.0)

    def _publish_container(self, container_id: str) -> PublishResult:
        payload = self._call(
            "POST",
            f"{self.cfg.instagram.ig_user_id}/media_publish",
            {"creation_id": container_id},
        )
        media_id = payload.get("id")
        if not media_id:
            raise PublishError(f"no media id in publish response: {payload}")
        permalink = ""
        try:
            permalink = self._call("GET", media_id, {"fields": "permalink"}).get(
                "permalink", ""
            )
        except PublishError:
            # The post is live; only the convenience link is missing.
            pass
        return PublishResult(media_id=media_id, permalink=permalink)

    def publish_image(
        self, image_url: str, caption: str, alt_text: str = ""
    ) -> PublishResult:
        if self.dry_run:
            return PublishResult(media_id="dry-run", permalink="", dry_run=True)
        if image_url.startswith("file://"):
            raise PublishError(
                "Instagram fetches media over HTTP, so it cannot read a local file "
                "path. Set storage.public_base_url, or use the s3 storage provider."
            )
        params: dict[str, Any] = {"image_url": image_url, "caption": caption}
        if alt_text:
            params["alt_text"] = alt_text
        container_id = self._create_container(params)
        self._await_container(container_id)
        return self._publish_container(container_id)

    def publish_carousel(
        self, image_urls: list[str], caption: str
    ) -> PublishResult:
        if not 2 <= len(image_urls) <= 10:
            raise PublishError("a carousel needs between 2 and 10 images")
        if self.dry_run:
            return PublishResult(media_id="dry-run", permalink="", dry_run=True)
        children: list[str] = []
        for url in image_urls:
            child = self._create_container({"image_url": url, "is_carousel_item": "true"})
            self._await_container(child)
            children.append(child)
        container_id = self._create_container(
            {"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption}
        )
        self._await_container(container_id)
        return self._publish_container(container_id)

    def publish_reel(
        self, video_url: str, caption: str, cover_url: str = ""
    ) -> PublishResult:
        if self.dry_run:
            return PublishResult(media_id="dry-run", permalink="", dry_run=True)
        params: dict[str, Any] = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true",
        }
        if cover_url:
            params["cover_url"] = cover_url
        container_id = self._create_container(params)
        # Video transcoding is slower than image processing.
        self._await_container(container_id, timeout=900)
        return self._publish_container(container_id)

    def comment(self, media_id: str, message: str) -> str:
        """Post a comment, used to keep hashtags out of the caption body."""
        if self.dry_run:
            return "dry-run"
        payload = self._call("POST", f"{media_id}/comments", {"message": message})
        return payload.get("id", "")
