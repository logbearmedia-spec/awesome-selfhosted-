"""Image generation.

Every provider takes the same request and returns PNG or JPEG bytes. Character
consistency is the hard part of running a synthetic account, and it is handled
two ways: a fixed seed derived from the persona, and an optional reference
image (``image.reference_image``) that providers with an image-conditioning
model use to keep the same face across posts.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import Config

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


class ImageError(Exception):
    """Raised when an image could not be generated."""


@dataclass(frozen=True)
class ImageRequest:
    prompt: str
    negative_prompt: str
    width: int
    height: int
    seed: int
    reference_image: Path | None = None


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    content_type: str
    provider: str

    @property
    def extension(self) -> str:
        return {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(
            self.content_type, ".bin"
        )


def _require(options: dict[str, Any], key: str, provider: str) -> Any:
    value = options.get(key)
    if value in (None, ""):
        raise ImageError(
            f"image provider {provider!r} needs image.options.{key} to be set"
        )
    return value


def _read_reference(request: ImageRequest) -> tuple[bytes, str]:
    path = request.reference_image
    if path is None:
        raise ImageError("no reference image configured")
    if not path.is_file():
        raise ImageError(f"reference image not found: {path}")
    mime = _MIME_BY_SUFFIX.get(path.suffix.lower())
    if mime is None:
        raise ImageError(
            f"reference image {path.name} has an unsupported extension; "
            f"use one of {', '.join(sorted(_MIME_BY_SUFFIX))}"
        )
    return path.read_bytes(), mime


# -- dry run ------------------------------------------------------------


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _placeholder_png(request: ImageRequest) -> bytes:
    """A deterministic banded image, so dry runs produce real, viewable files."""
    width, height = request.width, request.height
    digest = hashlib.sha256(request.prompt.encode()).digest()
    base = (digest[0], digest[1], digest[2])

    rows = bytearray()
    band_height = max(height // 16, 1)
    for y in range(height):
        band = y // band_height
        shade = 0.35 + 0.65 * (band % 8) / 7
        colour = bytes(
            max(0, min(255, int(channel * shade) ^ (digest[3 + band % 12] >> 4)))
            for channel in base
        )
        rows.append(0)  # PNG filter type 0 for the scanline
        rows.extend(colour * width)

    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + _png_chunk(b"IEND", b"")
    )


def _generate_dryrun(request: ImageRequest, cfg: Config) -> GeneratedImage:
    return GeneratedImage(
        data=_placeholder_png(request), content_type="image/png", provider="dryrun"
    )


# -- Replicate ----------------------------------------------------------


def _generate_replicate(request: ImageRequest, cfg: Config) -> GeneratedImage:
    import requests

    options = cfg.image.options
    token = _require(options, "api_token", "replicate")
    model = _require(options, "model", "replicate")
    timeout = int(options.get("timeout_seconds", 300))
    poll_every = float(options.get("poll_seconds", 2))

    payload: dict[str, Any] = {
        "prompt": request.prompt,
        "negative_prompt": request.negative_prompt,
        "width": request.width,
        "height": request.height,
        "seed": request.seed,
    }
    payload.update(options.get("extra_input") or {})

    if request.reference_image is not None:
        data, mime = _read_reference(request)
        field = options.get("reference_field", "image")
        payload[field] = f"data:{mime};base64,{base64.b64encode(data).decode()}"

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body: dict[str, Any] = {"input": payload}
    # Official models are addressed by path; community models by version hash.
    if "/" in model and len(model.split("/")) == 2 and ":" not in model:
        url = f"https://api.replicate.com/v1/models/{model}/predictions"
    else:
        url = "https://api.replicate.com/v1/predictions"
        body["version"] = model.split(":")[-1]

    response = requests.post(url, headers=headers, json=body, timeout=60)
    if response.status_code >= 400:
        raise ImageError(f"Replicate rejected the request ({response.status_code}): {response.text}")
    prediction = response.json()

    deadline = time.monotonic() + timeout
    while prediction.get("status") in ("starting", "processing"):
        if time.monotonic() > deadline:
            raise ImageError(f"Replicate prediction timed out after {timeout}s")
        time.sleep(poll_every)
        poll = requests.get(prediction["urls"]["get"], headers=headers, timeout=30)
        poll.raise_for_status()
        prediction = poll.json()

    if prediction.get("status") != "succeeded":
        raise ImageError(
            f"Replicate prediction {prediction.get('status')}: {prediction.get('error')}"
        )

    output = prediction.get("output")
    image_url = output[0] if isinstance(output, list) and output else output
    if not isinstance(image_url, str):
        raise ImageError(f"unexpected Replicate output shape: {output!r}")

    download = requests.get(image_url, timeout=120)
    download.raise_for_status()
    content_type = download.headers.get("Content-Type", "image/png").split(";")[0]
    return GeneratedImage(data=download.content, content_type=content_type, provider="replicate")


# -- ComfyUI (self-hosted) ----------------------------------------------


def _generate_comfyui(request: ImageRequest, cfg: Config) -> GeneratedImage:
    import requests

    options = cfg.image.options
    base = str(_require(options, "base_url", "comfyui")).rstrip("/")
    workflow_path = Path(_require(options, "workflow", "comfyui"))
    if not workflow_path.is_absolute():
        workflow_path = cfg.source.parent / workflow_path
    if not workflow_path.is_file():
        raise ImageError(f"ComfyUI workflow template not found: {workflow_path}")

    timeout = int(options.get("timeout_seconds", 600))
    poll_every = float(options.get("poll_seconds", 2))

    reference_name = ""
    if request.reference_image is not None:
        data, mime = _read_reference(request)
        upload = requests.post(
            f"{base}/upload/image",
            files={"image": (request.reference_image.name, data, mime)},
            data={"overwrite": "true"},
            timeout=60,
        )
        upload.raise_for_status()
        reference_name = upload.json().get("name", request.reference_image.name)

    template = workflow_path.read_text(encoding="utf-8")
    for token, value in (
        ("%prompt%", request.prompt),
        ("%negative%", request.negative_prompt),
        ("%seed%", str(request.seed)),
        ("%width%", str(request.width)),
        ("%height%", str(request.height)),
        ("%reference%", reference_name),
    ):
        # json.dumps then strip the quotes so quotes and newlines in the prompt
        # cannot break the surrounding JSON document.
        template = template.replace(token, json.dumps(value)[1:-1])

    try:
        workflow = json.loads(template)
    except json.JSONDecodeError as exc:
        raise ImageError(
            f"{workflow_path}: workflow is not valid JSON after substitution: {exc}"
        ) from exc

    queued = requests.post(f"{base}/prompt", json={"prompt": workflow}, timeout=60)
    if queued.status_code >= 400:
        raise ImageError(f"ComfyUI rejected the workflow ({queued.status_code}): {queued.text}")
    prompt_id = queued.json()["prompt_id"]

    deadline = time.monotonic() + timeout
    while True:
        if time.monotonic() > deadline:
            raise ImageError(f"ComfyUI render timed out after {timeout}s")
        history = requests.get(f"{base}/history/{prompt_id}", timeout=30)
        history.raise_for_status()
        entry = history.json().get(prompt_id)
        if entry:
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise ImageError(f"ComfyUI reported an error: {status}")
            images = [
                image
                for node in entry.get("outputs", {}).values()
                for image in node.get("images", [])
            ]
            if images:
                first = images[0]
                view = requests.get(
                    f"{base}/view",
                    params={
                        "filename": first["filename"],
                        "subfolder": first.get("subfolder", ""),
                        "type": first.get("type", "output"),
                    },
                    timeout=120,
                )
                view.raise_for_status()
                content_type = view.headers.get("Content-Type", "image/png").split(";")[0]
                return GeneratedImage(
                    data=view.content, content_type=content_type, provider="comfyui"
                )
        time.sleep(poll_every)


PROVIDERS: dict[str, Callable[[ImageRequest, Config], GeneratedImage]] = {
    "dryrun": _generate_dryrun,
    "replicate": _generate_replicate,
    "comfyui": _generate_comfyui,
}


def reference_path(cfg: Config) -> Path | None:
    raw = cfg.image.options.get("reference_image")
    if not raw:
        return None
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = cfg.source.parent / path
    return path.resolve()


def generate(request: ImageRequest, cfg: Config) -> GeneratedImage:
    provider = PROVIDERS.get(cfg.image.provider)
    if provider is None:
        raise ImageError(
            f"unknown image provider {cfg.image.provider!r}; expected one of "
            f"{', '.join(sorted(PROVIDERS))}"
        )
    return provider(request, cfg)
