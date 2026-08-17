#!/usr/bin/env python3
"""Portable LTS4AI SF-gpt-image-2 client."""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path


DEFAULT_BASE_URL = "https://api.lts4ai.com/v1"
DEFAULT_MODEL = "SF-gpt-image-2"
MAX_REFERENCE_IMAGES = 12
MAX_REFERENCE_BYTES = 15 * 1024 * 1024

SUPPORTED_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

FIXED_RATIOS = {
    "1:1": 1.0,
    "16:9": 16 / 9,
    "21:9": 21 / 9,
    "4:3": 4 / 3,
    "3:2": 3 / 2,
    "5:4": 5 / 4,
    "2:1": 2.0,
    "3:4": 3 / 4,
    "2:3": 2 / 3,
    "4:5": 4 / 5,
    "9:16": 9 / 16,
}

FOUR_K_SIZES = {
    "1:1": "2880x2880",
    "16:9": "3840x2160",
    "21:9": "3840x1648",
    "4:3": "3264x2448",
    "3:2": "3504x2336",
    "5:4": "3200x2560",
    "2:1": "3840x1920",
    "3:4": "2448x3264",
    "2:3": "2336x3504",
    "4:5": "2560x3200",
    "9:16": "2160x3840",
}


class SkillError(RuntimeError):
    """A safe, user-facing skill error."""


@dataclass(frozen=True)
class ReferenceImage:
    path: Path
    mime_type: str
    size: int


def resolve_size(
    ratio: str,
    quality: str,
    first_image: Path | None = None,
    explicit_resolution: str | None = None,
) -> str:
    """Map the site's ratio and quality controls to the provider size field."""
    if explicit_resolution is not None:
        if explicit_resolution != "auto" and not re.fullmatch(r"[1-9]\d*x[1-9]\d*", explicit_resolution):
            raise SkillError("Resolution must look like 1024x1024 or be auto.")
        return explicit_resolution

    normalized_quality = quality.upper()
    if normalized_quality not in {"1K", "2K", "4K"}:
        raise SkillError("Quality must be one of 1K, 2K, or 4K.")

    normalized_ratio = ratio
    if normalized_ratio == "Adaptive" and first_image is not None:
        dimensions = read_image_dimensions(first_image)
        if dimensions is not None:
            normalized_ratio = nearest_ratio(*dimensions)
    if normalized_ratio != "Adaptive" and normalized_ratio not in FIXED_RATIOS:
        raise SkillError(f"Unsupported aspect ratio: {ratio}")

    if normalized_quality == "4K":
        return "3840x2160" if normalized_ratio == "Adaptive" else FOUR_K_SIZES[normalized_ratio]
    if normalized_ratio in {"Adaptive", "1:1"}:
        return "1024x1024"
    if normalized_ratio in {"16:9", "4:3", "3:2"}:
        return "1536x1024"
    if normalized_ratio in {"9:16", "3:4", "2:3", "4:5"}:
        return "1024x1536"
    return "auto"


def nearest_ratio(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        raise SkillError("Image dimensions must be positive.")
    value = width / height
    return min(FIXED_RATIOS, key=lambda item: abs(FIXED_RATIOS[item] - value))


def validate_reference_images(paths: list[Path]) -> list[ReferenceImage]:
    if not paths:
        raise SkillError("At least one reference image is required.")
    if len(paths) > MAX_REFERENCE_IMAGES:
        raise SkillError(f"At most {MAX_REFERENCE_IMAGES} reference images are supported.")

    references: list[ReferenceImage] = []
    total_size = 0
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_file():
            raise SkillError(f"Reference image does not exist: {path}")
        mime_type = SUPPORTED_MIME_TYPES.get(path.suffix.lower())
        if mime_type is None:
            raise SkillError("Only supported JPEG, PNG, GIF, and WebP images may be uploaded.")
        size = path.stat().st_size
        total_size += size
        references.append(ReferenceImage(path=path, mime_type=mime_type, size=size))

    if total_size > MAX_REFERENCE_BYTES:
        raise SkillError("Reference images exceed the 15 MB total input limit.")
    return references


def read_image_dimensions(path: Path) -> tuple[int, int] | None:
    data = path.read_bytes()[:65536]
    suffix = path.suffix.lower()
    if suffix == ".png" and len(data) >= 24 and data.startswith(b"\x89PNG\r\n\x1a\n"):
        return struct.unpack(">II", data[16:24])
    if suffix == ".gif" and len(data) >= 10 and data[:6] in {b"GIF87a", b"GIF89a"}:
        return struct.unpack("<HH", data[6:10])
    if suffix in {".jpg", ".jpeg"}:
        return _jpeg_dimensions(data)
    if suffix == ".webp":
        return _webp_dimensions(data)
    return None


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if not data.startswith(b"\xff\xd8"):
        return None
    offset = 2
    while offset + 9 <= len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        offset += 2
        if marker in {0xD8, 0xD9}:
            continue
        if offset + 2 > len(data):
            return None
        segment_length = struct.unpack(">H", data[offset : offset + 2])[0]
        if segment_length < 2 or offset + segment_length > len(data):
            return None
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height, width = struct.unpack(">HH", data[offset + 3 : offset + 7])
            return width, height
        offset += segment_length
    return None


def _webp_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    chunk = data[12:16]
    if chunk == b"VP8X":
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    return None
