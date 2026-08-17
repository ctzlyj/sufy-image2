import importlib.util
import base64
import json
import struct
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sufy_image2.py"
SPEC = importlib.util.spec_from_file_location("sufy_image2", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Unable to load sufy_image2 module")
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def png_header(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", width, height)


PNG_BYTES = png_header(2, 2) + b"test-image"
PNG_BASE64 = base64.b64encode(PNG_BYTES).decode("ascii")


class MockProviderState:
    def __init__(self):
        self.requests = []
        self.responses = []
        self.remote_image = PNG_BYTES


def start_mock_provider(state: MockProviderState):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            state.requests.append({
                "method": "POST",
                "path": self.path,
                "headers": dict(self.headers.items()),
                "body": body,
            })
            status, content_type, response_body = state.responses.pop(0)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

        def do_GET(self):
            state.requests.append({
                "method": "GET",
                "path": self.path,
                "headers": dict(self.headers.items()),
                "body": b"",
            })
            if self.path == "/result.png":
                body = state.remote_image
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class ResolutionAndValidationTests(unittest.TestCase):
    def test_current_site_resolution_mapping(self):
        self.assertEqual(module.resolve_size("1:1", "2K"), "1024x1024")
        self.assertEqual(module.resolve_size("16:9", "2K"), "1536x1024")
        self.assertEqual(module.resolve_size("2:3", "2K"), "1024x1536")
        self.assertEqual(module.resolve_size("21:9", "2K"), "auto")
        self.assertEqual(module.resolve_size("2:3", "4K"), "2336x3504")
        self.assertEqual(module.resolve_size("Adaptive", "4K"), "3840x2160")

    def test_explicit_resolution_override_is_preserved(self):
        self.assertEqual(
            module.resolve_size("1:1", "2K", explicit_resolution="2048x2048"),
            "2048x2048",
        )

    def test_adaptive_edit_uses_first_image_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "wide.png"
            image_path.write_bytes(png_header(1600, 900))
            self.assertEqual(module.resolve_size("Adaptive", "4K", image_path), "3840x2160")

    def test_reference_validation_accepts_supported_images_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.png"
            second = Path(directory) / "second.webp"
            first.write_bytes(png_header(100, 100))
            second.write_bytes(b"RIFF\x00\x00\x00\x00WEBP")
            references = module.validate_reference_images([first, second])
            self.assertEqual([item.path for item in references], [first, second])
            self.assertEqual([item.mime_type for item in references], ["image/png", "image/webp"])

    def test_reference_validation_rejects_unsupported_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "sample.avif"
            image_path.write_bytes(b"not-an-image")
            with self.assertRaisesRegex(module.SkillError, "supported"):
                module.validate_reference_images([image_path])

    def test_reference_validation_rejects_more_than_twelve_images(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(13):
                path = Path(directory) / f"{index}.png"
                path.write_bytes(png_header(1, 1))
                paths.append(path)
            with self.assertRaisesRegex(module.SkillError, "12"):
                module.validate_reference_images(paths)

    def test_reference_validation_rejects_more_than_fifteen_megabytes(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.png"
            second = Path(directory) / "second.png"
            first.write_bytes(png_header(1, 1) + b"0" * (8 * 1024 * 1024))
            second.write_bytes(png_header(1, 1) + b"1" * (8 * 1024 * 1024))
            with self.assertRaisesRegex(module.SkillError, "15 MB"):
                module.validate_reference_images([first, second])


class ProviderContractTests(unittest.TestCase):
    def setUp(self):
        self.state = MockProviderState()
        self.server, self.thread = start_mock_provider(self.state)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/v1"
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.output_dir = Path(self.temporary_directory.name)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary_directory.cleanup()

    def queue_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.state.responses.append((status, "application/json", body))

    def client(self, **overrides):
        options = {
            "api_key": "test-secret-key",
            "base_url": self.base_url,
            "sleep": lambda _seconds: None,
        }
        options.update(overrides)
        return module.SfImage2Client(**options)

    def test_text_to_image_uses_exact_sf_generation_contract(self):
        self.queue_json({"data": [{"b64_json": PNG_BASE64}]})
        result = self.client().generate("draw a red circle", "1024x1024", self.output_dir)

        request = self.state.requests[0]
        self.assertEqual(request["path"], "/v1/images/generations")
        self.assertEqual(request["headers"]["Authorization"], "Bearer test-secret-key")
        self.assertEqual(request["headers"]["Content-Type"], "application/json")
        payload = json.loads(request["body"])
        self.assertEqual(payload, {
            "model": "SF-gpt-image-2",
            "prompt": "draw a red circle",
            "size": "1024x1024",
            "output_format": "png",
        })
        self.assertNotIn("n", payload)
        self.assertNotIn("response_format", payload)
        self.assertEqual(result.path.read_bytes(), PNG_BYTES)
        self.assertEqual(result.mime_type, "image/png")

    def test_multi_reference_edit_uses_ordered_multipart_contract(self):
        self.queue_json({"data": [{"b64_json": PNG_BASE64}]})
        first = self.output_dir / "first.png"
        second = self.output_dir / "second.webp"
        first.write_bytes(PNG_BYTES)
        second.write_bytes(b"RIFF\x04\x00\x00\x00WEBPtest")
        references = module.validate_reference_images([first, second])

        self.client().edit("keep the product", references, "1024x1536", self.output_dir)

        request = self.state.requests[0]
        self.assertEqual(request["path"], "/v1/images/edits")
        content_type = request["headers"]["Content-Type"]
        self.assertRegex(content_type, r"^multipart/form-data; boundary=.+")
        body = request["body"]
        self.assertIn(b'name="model"\r\n\r\nSF-gpt-image-2', body)
        self.assertIn(b'name="prompt"\r\n\r\nkeep the product', body)
        self.assertIn(b'name="size"\r\n\r\n1024x1536', body)
        self.assertIn(b'name="output_format"\r\n\r\npng', body)
        first_position = body.index(b'filename="first.png"')
        second_position = body.index(b'filename="second.webp"')
        self.assertLess(first_position, second_position)
        boundary = content_type.split("boundary=", 1)[1].encode("ascii")
        self.assertIn(b"--" + boundary, body)
        self.assertNotIn(b"response_format", body)

    def test_extracts_data_url_sse_and_remote_url_responses(self):
        data_url = f"data:image/png;base64,{PNG_BASE64}"
        self.queue_json({"data": [{"url": data_url}]})
        sse = f'data: {{"data":[{{"b64_json":"{PNG_BASE64}"}}]}}\n\ndata: [DONE]\n\n'.encode()
        self.state.responses.append((200, "text/event-stream", sse))
        self.queue_json({"data": [{"url": f"http://127.0.0.1:{self.server.server_port}/result.png"}]})

        first = self.client().generate("one", "1024x1024", self.output_dir, stem="one")
        second = self.client().generate("two", "1024x1024", self.output_dir, stem="two")
        third = self.client().generate("three", "1024x1024", self.output_dir, stem="three")

        self.assertEqual(first.path.read_bytes(), PNG_BYTES)
        self.assertEqual(second.path.read_bytes(), PNG_BYTES)
        self.assertEqual(third.path.read_bytes(), PNG_BYTES)
        self.assertEqual(self.state.requests[-1]["path"], "/result.png")

    def test_retries_rate_limits_and_server_errors(self):
        self.queue_json({"error": {"message": "slow down"}}, status=429)
        self.queue_json({"error": {"message": "temporary"}}, status=503)
        self.queue_json({"data": [{"b64_json": PNG_BASE64}]})
        sleeps = []
        client = self.client(retries=2, sleep=sleeps.append)

        result = client.generate("retry", "1024x1024", self.output_dir)

        self.assertTrue(result.path.exists())
        self.assertEqual(len(self.state.requests), 3)
        self.assertEqual(sleeps, [1.0, 3.0])

    def test_errors_never_include_api_key(self):
        self.queue_json({"error": {"message": "unauthorized"}}, status=401)
        with self.assertRaises(module.SkillError) as raised:
            self.client().generate("fail", "1024x1024", self.output_dir)
        self.assertNotIn("test-secret-key", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
