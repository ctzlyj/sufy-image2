#!/usr/bin/env python3
"""Portable LTS4AI SF-gpt-image-2 client."""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import getpass
import json
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


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
                error_body = error.read()
                if attempt < self.retries and (error.code == 429 or error.code >= 500):
                    self.sleep(_retry_delay(attempt))
                    continue
                raise self._http_error(error.code, error_body) from None
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                raise SkillError(f"LTS4AI request failed: {_safe_network_reason(error)}") from None
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
        mime_type = payload.mime_type or detect_image_mime(payload.data)
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
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped.startswith("data:"):
                    continue
                data = stripped[5:].strip()
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
    return data, content_type or detect_image_mime(data)


def detect_image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


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


def resolve_api_key(read_stdin: bool) -> str:
    environment_key = os.environ.get("LTS4AI_API_KEY", "").strip()
    if environment_key:
        return environment_key
    if read_stdin:
        stdin_key = sys.stdin.readline().strip()
        if stdin_key:
            return stdin_key
        raise SkillError("LTS4AI API key from stdin was empty.")
    if sys.stdin.isatty():
        interactive_key = getpass.getpass("LTS4AI API key: ").strip()
        if interactive_key:
            return interactive_key
    raise SkillError(
        "LTS4AI API key is required. Set LTS4AI_API_KEY or use --api-key-stdin."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sufy_image2.py",
        description="Generate and edit images with LTS4AI SF-gpt-image-2.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base-url", default=DEFAULT_BASE_URL)
    common.add_argument("--model", default=DEFAULT_MODEL)
    common.add_argument("--timeout", type=float, default=None)
    common.add_argument("--retries", type=int, default=2)
    common.add_argument(
        "--api-key-stdin",
        action="store_true",
        help="Read the API key from one stdin line instead of a command argument.",
    )

    image_options = argparse.ArgumentParser(add_help=False, parents=[common])
    image_options.add_argument(
        "--ratio",
        choices=["Adaptive", *FIXED_RATIOS.keys()],
        default="1:1",
    )
    image_options.add_argument("--quality", choices=["1K", "2K", "4K"], default="2K")
    image_options.add_argument(
        "--resolution",
        help="Expert override such as 2048x2048 or auto.",
    )
    image_options.add_argument("--output-dir", type=Path, default=Path("output"))

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
            size = resolve_size(
                arguments.ratio,
                arguments.quality,
                explicit_resolution=arguments.resolution,
            )
            output = client.generate(arguments.prompt, size, arguments.output_dir)
            _print_json(_single_summary("generate", size, output))
            return 0
        if arguments.command == "edit":
            references = validate_reference_images(arguments.image)
            size = resolve_size(
                arguments.ratio,
                arguments.quality,
                references[0].path,
                arguments.resolution,
            )
            output = client.edit(arguments.prompt, references, size, arguments.output_dir)
            _print_json(_single_summary("edit", size, output))
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
    size = resolve_size(
        arguments.ratio,
        arguments.quality,
        first_image,
        arguments.resolution,
    )
    operation = "edit" if references else "generate"

    def worker(item: tuple[int, str]) -> tuple[int, OutputImage]:
        index, prompt = item
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


def _single_summary(operation: str, size: str, output: OutputImage) -> dict[str, Any]:
    return {
        "ok": True,
        "operation": operation,
        "model": DEFAULT_MODEL,
        "size": size,
        "outputs": [{"index": 1, **_output_record(output)}],
    }


def _output_record(output: OutputImage) -> dict[str, Any]:
    return {
        "path": str(output.path),
        "mimeType": output.mime_type,
        "bytes": output.size,
    }


def _print_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
