"""QR-code generation + deep-link token encoding.

Token format: base64url-encoded JSON of the request payload, no padding.
Self-describing — the viewer page decodes it client-side, no DB needed,
no PHI in transit.
"""
from __future__ import annotations

import base64
import io
import json
import os
from typing import Any

import qrcode
from PIL import Image

QR_BASE_URL = os.environ.get("QR_BASE_URL", "https://schedule.giready.com/v")


def encode_token(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def deep_link_url(payload: dict[str, Any]) -> str:
    return f"{QR_BASE_URL}/{encode_token(payload)}"


def png_bytes(url: str, size_px: int = 150) -> bytes:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=1)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB").resize(
        (size_px, size_px), Image.NEAREST
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def png_to_data_uri(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def deep_link_qr_data_uri(payload: dict[str, Any]) -> str:
    return png_to_data_uri(png_bytes(deep_link_url(payload)))


# meds.giready.com is a fixed URL — build the QR once and reuse it across requests.
MEDS_REFERENCE_URL = "https://meds.giready.com"
_MEDS_REFERENCE_QR: str | None = None


def meds_reference_qr_data_uri() -> str:
    global _MEDS_REFERENCE_QR
    if _MEDS_REFERENCE_QR is None:
        _MEDS_REFERENCE_QR = png_to_data_uri(png_bytes(MEDS_REFERENCE_URL))
    return _MEDS_REFERENCE_QR
