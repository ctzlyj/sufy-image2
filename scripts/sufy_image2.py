#!/usr/bin/env python3
"""Portable LTS4AI image-model client."""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import getpass
import http.client
import json
import math
import os
import re
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


DEFAULT_BASE_URL = "https://api.lts4ai.com/v1"
DEFAULT_MODEL = "gpt-image-2.5"
MODEL_GUIDE = [
    {"id": DEFAULT_MODEL, "label": "gpt-image-2.5（默认）", "description": "新链路直连模型 ID；LTS4AI 生图渠道经 NextAICore 适配层转发，按张计费。"},
    {"id": "GPT-image-2", "label": "GPT-image-2（兼容别名）", "description": "站点兼容入口，上游同样映射到 gpt-image-2.5；仅在用户明确要求时使用。"},
    {"id": "gemini-3.1-pro-imagen-official", "label": "Imagen Pro（官方通道）", "description": "可选官方 Imagen 模型，按张计费。"},
    {"id": "gemini-3.5-flash-lite-imagen-official", "label": "Imagen Flash Lite（官方通道）", "description": "可选官方 Imagen 模型，按张计费。"},
    {"id": "gemini-3.6-flash-imagen-official", "label": "Imagen Flash（官方通道）", "description": "可选官方 Imagen 模型，按张计费。"},
]
MIN_CANVAS_PIXELS = 655_360
MAX_CANVAS_PIXELS = 8_294_400
MAX_CANVAS_SIDE = 3840
UNKNOWN_RESULT = "Result unknown; check task/billing status before resubmitting to avoid duplicate charges."
MAX_REFERENCE_IMAGES = 12
MAX_REFERENCE_BYTES = 15 * 1024 * 1024
SAVED_CREDENTIAL_RELATIVE_PATH = Path("JoyCode") / "credentials" / "sufy-image2.dpapi"

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


@dataclass(frozen=True)
class OutputImage:
    path: Path
    mime_type: str
    size: int


@dataclass(frozen=True)
class ImagePayload:
    data: bytes | None = None
    mime_type: str | None = None
    url: str | None = None


def resolve_size(
    ratio: str,
    quality: str,
    first_image: Path | None = None,
    explicit_resolution: str | None = None,
) -> str:
    """Map the site's ratio and quality controls to the provider size field."""
    if explicit_resolution is not None:
        validate_resolution(explicit_resolution)
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


def validate_resolution(resolution: str) -> None:
    if resolution == "auto":
        return
    if not re.fullmatch(r"[1-9][0-9]{0,3}x[1-9][0-9]{0,3}", resolution):
        raise SkillError("Resolution must look like 1024x1024 or be auto.")
    width, height = map(int, resolution.split("x"))
    if (width % 16 or height % 16 or max(width, height) > MAX_CANVAS_SIDE
            or not MIN_CANVAS_PIXELS <= width * height <= MAX_CANVAS_PIXELS
            or width > height * 3 or height > width * 3):
        raise SkillError("Unsupported resolution: use multiples of 16, sides <=3840, "
                         "655360-8294400 pixels and an aspect ratio between 1:3 and 3:1.")


def _centimeter_integer(value: str) -> int:
    text = unicodedata.normalize("NFKC", value).strip()
    if not re.fullmatch(r"[0-9]{1,9}(?:\.[0-9]{1,6})?", text):
        raise SkillError("Centimeters must be positive decimals with at most 9 integer and 6 decimal digits.")
    whole, _, fraction = text.partition(".")
    integer = int(whole) * 1_000_000 + int(fraction.ljust(6, "0"))
    if integer <= 0:
        raise SkillError("Centimeters must be greater than zero.")
    return integer


def calculate_custom_canvas(width_cm: str, height_cm: str, quality: str) -> dict[str, Any]:
    width_integer = _centimeter_integer(width_cm)
    height_integer = _centimeter_integer(height_cm)
    divisor = math.gcd(width_integer, height_integer)
    numerator, denominator = width_integer // divisor, height_integer // divisor
    if numerator > denominator * 3 or denominator > numerator * 3:
        raise SkillError("Canvas aspect ratio must be between 1:3 and 3:1; it will not be approximated.")
    unit_width, unit_height = numerator * 16, denominator * 16
    unit_pixels = unit_width * unit_height
    minimum = math.isqrt((MIN_CANVAS_PIXELS - 1) // unit_pixels) + 1
    maximum = min(MAX_CANVAS_SIDE // max(unit_width, unit_height), math.isqrt(MAX_CANVAS_PIXELS // unit_pixels))
    if minimum > maximum:
        raise SkillError("These centimeters cannot map exactly to a supported pixel canvas; adjust width/height.")
    target = {"1K": 1_048_576, "2K": 3_145_728, "4K": MAX_CANVAS_PIXELS}.get(quality.upper())
    if target is None:
        raise SkillError("Quality must be one of 1K, 2K, or 4K.")
    multiplier = max(minimum, min(maximum, math.isqrt(target // unit_pixels)))
    width, height = unit_width * multiplier, unit_height * multiplier
    return {"widthCm": width_cm, "heightCm": height_cm, "width": width, "height": height,
            "size": f"{width}x{height}", "ratio": f"{numerator}:{denominator}"}


def resolve_cli_canvas(arguments: argparse.Namespace, first_image: Path | None = None) -> tuple[str, dict[str, Any] | None]:
    if arguments.width_cm is not None or arguments.height_cm is not None:
        if arguments.width_cm is None or arguments.height_cm is None:
            raise SkillError("Provide both --width-cm and --height-cm.")
        if arguments.ratio is not None or arguments.resolution is not None:
            raise SkillError("Centimeter dimensions cannot be combined with --ratio or --resolution.")
        canvas = calculate_custom_canvas(arguments.width_cm, arguments.height_cm, arguments.quality)
        return canvas["size"], canvas
    if arguments.ratio is not None and arguments.resolution is not None:
        raise SkillError("Choose --ratio or --resolution, not both.")
    return resolve_size(arguments.ratio or "1:1", arguments.quality, first_image, arguments.resolution), None


def build_canvas_prompt(prompt: str, canvas: dict[str, Any] | None) -> str:
    _require_prompt(prompt)
    if canvas is None:
        return prompt
    return (f"{prompt}\n\n画布规格：宽{canvas['widthCm']}厘米、高{canvas['heightCm']}厘米，"
            f"整张图片宽:高={canvas['ratio']}，输出{canvas['width']}×{canvas['height']}像素。"
            "按此画布直接构图，不拉伸、不压扁、不裁切或添加边框凑比例；保留用户全部指定文案，"
            "不把画布尺寸当成商品尺寸或新增画面文案。")


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


class SfImage2Client:
    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = 600,
        retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        trimmed_key = api_key.strip()
        if not trimmed_key:
            raise SkillError("An LTS4AI API key is required.")
        if timeout <= 0:
            raise SkillError("Timeout must be greater than zero.")
        if retries < 0:
            raise SkillError("Retries cannot be negative.")
        self.api_key = trimmed_key
        self.api_root = normalize_api_root(base_url)
        self.model = model.strip() or DEFAULT_MODEL
        self.timeout = timeout
        self.retries = retries
        self.sleep = sleep

    def generate(
        self,
        prompt: str,
        size: str,
        output_dir: Path,
        stem: str = "generated",
    ) -> OutputImage:
        cleaned_prompt = _require_prompt(prompt)
        body = json.dumps(
            {
                "model": self.model,
                "prompt": cleaned_prompt,
                "size": size,
                "output_format": "png",
            },
            ensure_ascii=False,
        ).encode("utf-8")
        response_body, content_type = self._request(
            "POST",
            f"{self.api_root}/images/generations",
            body,
            {"Content-Type": "application/json"},
        )
        payload = extract_image_payload(response_body, content_type)
        return self._save_payload(payload, Path(output_dir), stem)

    def edit(
        self,
        prompt: str,
        images: list[ReferenceImage],
        size: str,
        output_dir: Path,
        stem: str = "edited",
    ) -> OutputImage:
        cleaned_prompt = _require_prompt(prompt)
        if not images:
            raise SkillError("At least one reference image is required for editing.")
        boundary = f"----sufy-image2-{uuid.uuid4().hex}"
        body = build_multipart_body(
            boundary,
            {
                "model": self.model,
                "prompt": cleaned_prompt,
                "size": size,
                "output_format": "png",
            },
            images,
        )
        response_body, content_type = self._request(
            "POST",
            f"{self.api_root}/images/edits",
            body,
            {"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        payload = extract_image_payload(response_body, content_type)
        return self._save_payload(payload, Path(output_dir), stem)

    def list_models(self) -> list[str]:
        response_body, content_type = self._request(
            "GET",
            f"{self.api_root}/models",
            None,
            {},
        )
        if "json" not in content_type.lower():
            raise SkillError("Model list response was not JSON.")
        try:
            payload = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SkillError("Model list response was invalid JSON.") from error
        raw_models = payload.get("data", []) if isinstance(payload, dict) else []
        models = []
        for item in raw_models if isinstance(raw_models, list) else []:
            model_id = item.get("id") if isinstance(item, dict) else item
            if isinstance(model_id, str) and model_id.strip():
                models.append(model_id.strip())
        return sorted(set(models), key=str.casefold)

    def _request(
        self,
        method: str,
        url: str,
        body: bytes | None,
        headers: dict[str, str],
    ) -> tuple[bytes, str]:
        request_headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json, text/event-stream, image/*",
            **headers,
        }
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read(), response.headers.get("Content-Type", "")
            except urllib.error.HTTPError as error:
                try:
                    error_body = error.read()
                except (http.client.HTTPException, TimeoutError, OSError):
                    error_body = b""
                if method == "POST" and (error.code >= 500 or error.code in {408, 409}):
                    raise SkillError(f"{self._http_error(error.code, error_body)} {UNKNOWN_RESULT}") from None
                if attempt < self.retries and (error.code == 429 or (method == "GET" and error.code >= 500)):
                    self.sleep(_retry_delay(attempt))
                    continue
                raise self._http_error(error.code, error_body) from None
            except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as error:
                suffix = f" {UNKNOWN_RESULT}" if method == "POST" else ""
                raise SkillError(f"LTS4AI request failed: {_safe_network_reason(error)}{suffix}") from None
        raise SkillError("LTS4AI request failed after retries.")

    def _http_error(self, status: int, response_body: bytes) -> SkillError:
        if status in {401, 403}:
            return SkillError(f"LTS4AI authentication failed (HTTP {status}).")
        if status == 429:
            return SkillError("LTS4AI rate limit or quota was reached (HTTP 429).")
        message = _provider_error_message(response_body)
        safe_message = message.replace(self.api_key, "[REDACTED]") if message else ""
        suffix = f": {safe_message}" if safe_message else ""
        return SkillError(f"LTS4AI image request failed (HTTP {status}){suffix}")

    def _save_payload(self, payload: ImagePayload, output_dir: Path, stem: str) -> OutputImage:
        if payload.url is not None:
            data, content_type = download_remote_image(payload.url, self.timeout)
            payload = ImagePayload(data=data, mime_type=content_type)
        if payload.data is None:
            raise SkillError("Provider response did not contain image bytes.")
        mime_type = detect_image_mime(payload.data)
        if mime_type is None:
            raise SkillError("Provider response was not a valid image.")
        extension = extension_for_mime(mime_type)
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = available_output_path(output_dir, sanitize_stem(stem), extension)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(payload.data)
        temporary.replace(destination)
        return OutputImage(path=destination.resolve(), mime_type=mime_type, size=len(payload.data))


def normalize_api_root(base_url: str) -> str:
    value = base_url.strip()
    if not value:
        raise SkillError("Base URL is required.")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SkillError("Base URL must be an absolute HTTP or HTTPS URL.")
    path = parsed.path.rstrip("/")
    endpoint_suffixes = (
        "/v1/images/generations",
        "/v1/images/edits",
        "/v1/models",
    )
    for suffix in endpoint_suffixes:
        if path.endswith(suffix):
            path = path[: -len(suffix)] + "/v1"
            break
    if not path.endswith("/v1"):
        path = f"{path}/v1" if path else "/v1"
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def build_multipart_body(
    boundary: str,
    fields: dict[str, str],
    images: list[ReferenceImage],
) -> bytes:
    chunks: list[bytes] = []
    boundary_bytes = boundary.encode("ascii")
    for name, value in fields.items():
        chunks.extend(
            [
                b"--" + boundary_bytes + b"\r\n",
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    for image in images:
        safe_name = image.path.name.replace('"', "_").replace("\r", "_").replace("\n", "_")
        chunks.extend(
            [
                b"--" + boundary_bytes + b"\r\n",
                (
                    f'Content-Disposition: form-data; name="image"; filename="{safe_name}"\r\n'
                    f"Content-Type: {image.mime_type}\r\n\r\n"
                ).encode("utf-8"),
                image.path.read_bytes(),
                b"\r\n",
            ]
        )
    chunks.append(b"--" + boundary_bytes + b"--\r\n")
    return b"".join(chunks)


def extract_image_payload(response_body: bytes, content_type: str = "") -> ImagePayload:
    if content_type.lower().startswith("image/"):
        return ImagePayload(data=response_body, mime_type=content_type.split(";", 1)[0].strip())
    text = response_body.decode("utf-8", errors="replace").strip()
    payloads: list[Any] = []
    if text:
        try:
            payloads.append(json.loads(text))
        except json.JSONDecodeError:
            for block in re.split(r"\r?\n\r?\n", text):
                data = "\n".join(
                    line.strip()[5:].strip()
                    for line in block.splitlines()
                    if line.strip().startswith("data:")
                ).strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    payloads.append(json.loads(data))
                except json.JSONDecodeError:
                    continue
    for payload in payloads:
        found = _find_image_payload(payload)
        if found is not None:
            return found
    raise SkillError("Provider response did not contain usable image data.")


def _find_image_payload(value: Any) -> ImagePayload | None:
    if isinstance(value, str):
        if value.startswith("data:"):
            match = re.fullmatch(r"data:([^;,]+);base64,(.+)", value, flags=re.DOTALL)
            if match is not None:
                try:
                    return ImagePayload(data=base64.b64decode(match.group(2), validate=True), mime_type=match.group(1))
                except ValueError:
                    return None
        if value.startswith("https://") or value.startswith("http://"):
            return ImagePayload(url=value)
        return None
    if isinstance(value, list):
        for item in value:
            found = _find_image_payload(item)
            if found is not None:
                return found
        return None
    if not isinstance(value, dict):
        return None
    encoded = value.get("b64_json", value.get("b64Json"))
    if isinstance(encoded, str) and encoded.strip():
        try:
            decoded = base64.b64decode(encoded.strip(), validate=True)
        except ValueError:
            decoded = b""
        if decoded:
            mime_type = value.get("mime_type", value.get("mimeType"))
            return ImagePayload(data=decoded, mime_type=mime_type if isinstance(mime_type, str) else None)
    preferred_keys = ("data", "images", "image", "url", "image_url", "choices", "content", "message", "output")
    for key in preferred_keys:
        if key in value:
            found = _find_image_payload(value[key])
            if found is not None:
                return found
    return None


def download_remote_image(url: str, timeout: float) -> tuple[bytes, str]:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise SkillError("Provider returned an unsupported image URL.")
    request = urllib.request.Request(url, headers={"Accept": "image/*"}, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as error:
        raise SkillError(f"Unable to download provider image: {_safe_network_reason(error)}") from None
    if not data:
        raise SkillError("Provider image URL returned an empty body.")
    return data, content_type or detect_image_mime(data) or "application/octet-stream"


def detect_image_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def extension_for_mime(mime_type: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }.get(mime_type.lower(), ".png")


def sanitize_stem(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip(".-")
    return cleaned or "generated"


def available_output_path(output_dir: Path, stem: str, extension: str) -> Path:
    candidate = output_dir / f"{stem}{extension}"
    index = 2
    while candidate.exists():
        candidate = output_dir / f"{stem}-{index}{extension}"
        index += 1
    return candidate


def _require_prompt(prompt: str) -> str:
    cleaned = prompt.strip()
    if not cleaned:
        raise SkillError("Prompt cannot be empty.")
    return cleaned


def _retry_delay(attempt: int) -> float:
    return 1.0 if attempt == 0 else 3.0


def _provider_error_message(body: bytes) -> str:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"][:240]
        if isinstance(payload.get("message"), str):
            return payload["message"][:240]
    return ""


def _safe_network_reason(error: BaseException) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    if isinstance(error, urllib.error.URLError):
        reason = error.reason
        return str(reason)[:160] if reason else "network unavailable"
    return error.__class__.__name__


def load_saved_api_key() -> str:
    if os.name != "nt":
        return ""
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        return ""
    credential_path = Path(local_app_data) / SAVED_CREDENTIAL_RELATIVE_PATH
    helper = Path(__file__).resolve().with_name("read_dpapi_credential.ps1")
    if not credential_path.is_file() or not helper.is_file():
        return ""
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-File", str(helper),
                "-CredentialPath", str(credential_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def resolve_api_key(read_stdin: bool) -> str:
    environment_key = os.environ.get("LTS4AI_API_KEY", "").strip()
    if environment_key:
        return environment_key
    if read_stdin:
        stdin_key = sys.stdin.readline().strip()
        if stdin_key:
            return stdin_key
        raise SkillError("LTS4AI API key from stdin was empty.")
    saved_key = load_saved_api_key()
    if saved_key:
        return saved_key
    if sys.stdin.isatty():
        interactive_key = getpass.getpass("LTS4AI API key: ").strip()
        if interactive_key:
            return interactive_key
    raise SkillError(
        "LTS4AI API key is required. Configure the Windows DPAPI credential, set LTS4AI_API_KEY, or use --api-key-stdin."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sufy_image2.py",
        description="Generate and edit images with LTS4AI gpt-image-2.5, the GPT-image-2 compatibility alias, or official Imagen models.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url", default=DEFAULT_BASE_URL)
    common.add_argument("--model", default=DEFAULT_MODEL, help=f"Exact LTS4AI model ID (default: {DEFAULT_MODEL}).")
    common.add_argument("--timeout", type=float, default=None)
    common.add_argument("--retries", type=int, default=2, help="Retries for HTTP 429 and read-only GET 5xx, never uncertain generation errors.")
    common.add_argument(
        "--api-key-stdin",
        action="store_true",
        help="Read the API key from one stdin line instead of a command argument.",
    )

    canvas_options = argparse.ArgumentParser(add_help=False)
    canvas_options.add_argument(
        "--ratio",
        choices=["Adaptive", *FIXED_RATIOS.keys()],
        default=None,
    )
    canvas_options.add_argument("--quality", choices=["1K", "2K", "4K"], default="2K")
    canvas_options.add_argument(
        "--resolution",
        help="Supported pixel canvas such as 2048x2048 or auto; not combined with ratio/centimeters.",
    )
    canvas_options.add_argument("--width-cm", help="Exact canvas width in centimeters, paired with --height-cm.")
    canvas_options.add_argument("--height-cm", help="Exact canvas height in centimeters, paired with --width-cm.")
    image_options = argparse.ArgumentParser(add_help=False, parents=[common, canvas_options])
    image_options.add_argument("--output-dir", type=Path, default=Path("output"))

    subparsers.add_parser("canvas", parents=[canvas_options], help="Calculate a supported canvas offline; no key or paid call.")
    subparsers.add_parser("guide", help="Show the Chinese model guide and local comparison image offline.")
    subparsers.add_parser("models", parents=[common], help="List available provider models.")

    generate = subparsers.add_parser(
        "generate",
        parents=[image_options],
        help="Generate one image from text.",
    )
    generate.add_argument("--prompt", required=True)

    edit = subparsers.add_parser(
        "edit",
        parents=[image_options],
        help="Edit an image using one to twelve references.",
    )
    edit.add_argument("--prompt", required=True)
    edit.add_argument("--image", type=Path, action="append", required=True)

    batch = subparsers.add_parser(
        "batch",
        parents=[image_options],
        help="Run up to ten text or edit tasks.",
    )
    prompt_source = batch.add_mutually_exclusive_group(required=True)
    prompt_source.add_argument("--prompt")
    prompt_source.add_argument("--prompts-file", type=Path)
    batch.add_argument("--count", type=int, default=1)
    batch.add_argument("--concurrency", type=int, default=2)
    batch.add_argument("--image", type=Path, action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "guide":
            _print_json({"ok": True, "operation": "guide", "defaultModel": DEFAULT_MODEL, "models": MODEL_GUIDE})
            return 0
        if arguments.command == "canvas":
            size, canvas = resolve_cli_canvas(arguments)
            _print_json({"ok": True, "operation": "canvas", "size": size, **({"canvas": canvas} if canvas else {})})
            return 0
        api_key = resolve_api_key(arguments.api_key_stdin)
        timeout = arguments.timeout
        if timeout is None:
            timeout = 1800 if getattr(arguments, "quality", "2K") == "4K" else 600
        client = SfImage2Client(
            api_key=api_key,
            base_url=arguments.base_url,
            model=arguments.model,
            timeout=timeout,
            retries=arguments.retries,
        )
        if arguments.command == "models":
            _print_json({"ok": True, "operation": "models", "models": client.list_models()})
            return 0
        if arguments.command == "generate":
            size, canvas = resolve_cli_canvas(arguments)
            output = client.generate(build_canvas_prompt(arguments.prompt, canvas), size, arguments.output_dir)
            _print_json(_single_summary("generate", client.model, size, output, canvas))
            return 0
        if arguments.command == "edit":
            references = validate_reference_images(arguments.image)
            size, canvas = resolve_cli_canvas(arguments, references[0].path)
            output = client.edit(build_canvas_prompt(arguments.prompt, canvas), references, size, arguments.output_dir)
            _print_json(_single_summary("edit", client.model, size, output, canvas))
            return 0
        if arguments.command == "batch":
            summary = run_batch(client, arguments)
            _print_json(summary)
            return 0
        raise SkillError(f"Unsupported command: {arguments.command}")
    except SkillError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


def run_batch(client: SfImage2Client, arguments: argparse.Namespace) -> dict[str, Any]:
    prompts = _batch_prompts(arguments.prompt, arguments.prompts_file, arguments.count)
    if not 1 <= arguments.concurrency <= 10:
        raise SkillError("Batch concurrency must be between 1 and 10.")
    references = validate_reference_images(arguments.image) if arguments.image else []
    first_image = references[0].path if references else None
    size, canvas = resolve_cli_canvas(arguments, first_image)
    operation = "edit" if references else "generate"

    def worker(item: tuple[int, str]) -> tuple[int, OutputImage]:
        index, prompt = item
        prompt = build_canvas_prompt(prompt, canvas)
        stem = f"{operation}-{index:03d}"
        if references:
            output = client.edit(prompt, references, size, arguments.output_dir, stem=stem)
        else:
            output = client.generate(prompt, size, arguments.output_dir, stem=stem)
        return index, output

    indexed_prompts = list(enumerate(prompts, start=1))
    results: list[tuple[int, OutputImage]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(arguments.concurrency, len(prompts))) as executor:
        futures = [executor.submit(worker, item) for item in indexed_prompts]
        for future in futures:
            results.append(future.result())
    results.sort(key=lambda item: item[0])
    return {
        "ok": True,
        "operation": "batch-edit" if references else "batch-generate",
        "model": client.model,
        "size": size,
        **({"canvas": canvas} if canvas else {}),
        "outputs": [
            {"index": index, **_output_record(output)}
            for index, output in results
        ],
    }


def _batch_prompts(prompt: str | None, prompts_file: Path | None, count: int) -> list[str]:
    if prompts_file is not None:
        if not prompts_file.is_file():
            raise SkillError(f"Prompts file does not exist: {prompts_file}")
        prompts = [line.strip() for line in prompts_file.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    else:
        if count < 1:
            raise SkillError("Batch count must be at least 1.")
        prompts = [_require_prompt(prompt or "")] * count
    if not prompts:
        raise SkillError("The batch prompt list is empty.")
    if len(prompts) > 10:
        raise SkillError("A batch may contain at most 10 tasks.")
    return prompts


def _single_summary(operation: str, model: str, size: str, output: OutputImage, canvas: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "operation": operation,
        "model": model,
        "size": size,
        **({"canvas": canvas} if canvas else {}),
        "outputs": [{"index": 1, **_output_record(output)}],
    }


def _output_record(output: OutputImage) -> dict[str, Any]:
    dimensions = read_image_dimensions(output.path)
    return {
        "path": str(output.path),
        "mimeType": output.mime_type,
        "bytes": output.size,
        **({"width": dimensions[0], "height": dimensions[1]} if dimensions else {}),
    }


def _print_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
