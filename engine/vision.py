"""
Image preparation for the vision class: decode, orient, resize to at most VISION_MAX_PIXELS (1 megapixel), re-encode
once, hash, and estimate the input tokens the provider will count.

    img = prepare_image(data_url_or_base64_or_bytes, max_pixels=1_000_000, tokens_per_megapixel=1000)
    img.data_url          # "data:image/jpeg;base64,..." for the image_url content part
    img.sha               # sha256 of the normalized pixels: the cache identity ("same photo" survives re-encoding)
    img.est_tokens        # what the input budget guard counts for it

Never touches the network (an image URL is not accepted: the engine does not fetch). Anything that is not a readable
image raises ImageError, which engine.run turns into a degraded `invalid_image` result.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import math
import re
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

MIN_IMAGE_TOKENS = 256
_DATA_URL = re.compile(r"^data:image/[\w.+-]+;base64,", re.I)


class ImageError(ValueError):
    pass


@dataclass(frozen=True)
class PreparedImage:
    data_url: str
    sha: str
    width: int
    height: int
    from_size: tuple[int, int]
    resized: bool
    est_tokens: int
    bytes_in: int
    bytes_out: int


def decode(image: str | bytes, max_bytes: int) -> bytes:
    """A data URL, a base64 string or raw bytes -> the file's bytes."""
    if isinstance(image, (bytes, bytearray)):
        raw = bytes(image)
    elif isinstance(image, str):
        s = image.strip()
        if s.lower().startswith(("http://", "https://")):
            raise ImageError("image URLs are not fetched: send the image itself (data URL or base64)")
        s = _DATA_URL.sub("", s)
        try:
            raw = base64.b64decode(s, validate=False)
        except (binascii.Error, ValueError) as e:
            raise ImageError(f"image is not valid base64 ({e})") from None
    else:
        raise ImageError(f"image must be a string or bytes, got {type(image).__name__}")
    if not raw:
        raise ImageError("image is empty")
    if len(raw) > max_bytes:
        raise ImageError(f"image is {len(raw) / 1e6:.1f} MB, over the {max_bytes / 1e6:.0f} MB limit")
    return raw


def prepare_image(image: str | bytes, max_pixels: int = 1_000_000, tokens_per_megapixel: int = 1000,
                  max_bytes: int = 12_000_000, quality: int = 88) -> PreparedImage:
    raw = decode(image, max_bytes)
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as e:
        raise ImageError(f"not a readable image ({type(e).__name__})") from None
    img = ImageOps.exif_transpose(img)                  # phone photos carry their rotation in EXIF
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))   # transparent -> white paper, not black
        bg.paste(rgba, mask=rgba.split()[3])
        img = bg
    else:
        img = img.convert("RGB")
    w0, h0 = img.size
    resized = w0 * h0 > max_pixels
    if resized:
        scale = math.sqrt(max_pixels / (w0 * h0))
        img = img.resize((max(1, int(w0 * scale)), max(1, int(h0 * scale))), Image.LANCZOS)
    w, h = img.size
    sha = hashlib.sha256(f"{w}x{h}".encode() + img.tobytes()).hexdigest()
    out = io.BytesIO()
    img.save(out, "JPEG", quality=quality, optimize=True)
    data = out.getvalue()
    tokens = max(MIN_IMAGE_TOKENS, math.ceil(w * h / 1e6 * tokens_per_megapixel))
    return PreparedImage("data:image/jpeg;base64," + base64.b64encode(data).decode(), sha, w, h, (w0, h0), resized,
                         tokens, len(raw), len(data))
