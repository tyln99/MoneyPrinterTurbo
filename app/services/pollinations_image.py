"""
Pollinations (https://pollinations.ai) free text-to-image material source.

Unlike every other AI material provider in this project, Pollinations needs no
API key and bills nothing, which makes it the only AI image source that can be
exercised end to end at zero cost. It is intended for trying the pipeline out,
not for finished output:

  * Generated images carry a "pollinations.ai" watermark even when `nologo` is
    requested; removing it requires a registered tier.
  * Resolution is capped well below the requested size (a 1024x1536 request
    comes back around 627x940). The aspect ratio *is* honoured, so framing
    survives, but the material is upscaled before it reaches a 1080x1920 canvas.
  * It is a shared community service and returns 429/5xx under load, so the
    request helper below retries with backoff.

The endpoint is a plain GET that returns image bytes directly, not an
OpenAI-compatible `/images/generations` call, which is why this needs its own
module instead of pointing `openai_image_base_url` at it.
"""

from __future__ import annotations

import time
import urllib.parse
from typing import Any, Mapping

import requests
from loguru import logger

from app.config import config

DEFAULT_BASE_URL = "https://image.pollinations.ai"
DEFAULT_MODEL = "sana"

# The service is free and shared, so throttling is routine rather than
# exceptional; retry the same statuses the OpenAI-compatible source treats as
# temporary. Nothing is billed, so a retry cannot cost the user money.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (3, 8, 15)
# Generation is synchronous and can take a while when the queue is busy.
REQUEST_TIMEOUT = (30, 180)


def is_enabled(settings: Mapping[str, Any] | None = None) -> bool:
    """
    Always available: the service needs no credentials.

    Kept for symmetry with the other material providers so the pipeline can
    treat every source the same way.
    """
    return True


def _base_url() -> str:
    return str(
        config.app.get("pollinations_image_base_url", DEFAULT_BASE_URL)
        or DEFAULT_BASE_URL
    ).strip().rstrip("/")


def _model() -> str:
    return str(
        config.app.get("pollinations_image_model", DEFAULT_MODEL) or DEFAULT_MODEL
    ).strip()


def _tls_verify() -> bool:
    return bool(config.app.get("tls_verify", True))


def build_image_url(prompt: str, width: int, height: int, seed: int | None = None) -> str:
    """
    Build the GET URL for one image.

    The prompt travels in the path, so it must be percent-encoded with nothing
    left safe - a bare "/" in a prompt would otherwise change the route.
    """
    quoted = urllib.parse.quote(prompt, safe="")
    params = {
        "width": max(int(width), 1),
        "height": max(int(height), 1),
        "model": _model(),
        "nologo": "true",
    }
    if seed is not None:
        # Without a seed the service is deterministic per prompt, so two scenes
        # that share a keyword would get byte-identical images.
        params["seed"] = int(seed)
    return f"{_base_url()}/prompt/{quoted}?{urllib.parse.urlencode(params)}"


def generate_image(
    prompt: str,
    width: int,
    height: int,
    seed: int | None = None,
) -> tuple[bytes | None, str]:
    """
    Fetch one generated image, returning (image bytes, failure detail).

    Returns (None, detail) on failure so the caller can skip the keyword and
    carry on, matching the "empty result means skip" contract the other
    material sources use. Nothing here is billed, so unlike the paid sources
    every transport error is safe to retry.
    """
    url = build_image_url(prompt, width, height, seed)
    failure_detail = "no request attempt was made"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.get(
                url,
                proxies=config.proxy,
                verify=_tls_verify(),
                timeout=REQUEST_TIMEOUT,
            )
        except Exception as e:
            failure_detail = f"request failed: {type(e).__name__}: {e}"
        else:
            status = response.status_code
            content_type = str(response.headers.get("Content-Type", "")).lower()
            if status == 200 and content_type.startswith("image/"):
                return response.content, ""
            if status == 200:
                # A 200 carrying JSON is the service reporting an upstream
                # failure in the response body rather than in the status line.
                failure_detail = (
                    f"unexpected content type {content_type!r}: "
                    f"{response.text[:200]}"
                )
            else:
                failure_detail = f"HTTP {status}: {response.text[:200]}"
            if status not in RETRYABLE_STATUS_CODES and status != 200:
                logger.error(f"pollinations image request rejected: {failure_detail}")
                return None, failure_detail

        if attempt < MAX_ATTEMPTS:
            backoff = RETRY_BACKOFF_SECONDS[
                min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)
            ]
            logger.warning(
                "pollinations image request failed, retrying: "
                f"attempt={attempt}/{MAX_ATTEMPTS}, next_retry_in={backoff}s, "
                f"detail={failure_detail}"
            )
            time.sleep(backoff)

    return None, failure_detail
