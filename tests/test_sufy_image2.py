import importlib.util
import base64
import contextlib
import io
import json
import os
import struct
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock


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
            if state.responses:
                status, content_type, response_body = state.responses.pop(0)
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(response_body)))
                self.end_headers()
                self.wfile.write(response_body)
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


class CliTests(unittest.TestCase):
    def setUp(self):
        self.state = MockProviderState()
        self.server, self.thread = start_mock_provider(self.state)
        self.base_url = f"http://127.0.0.1:{self.server.server_port}/v1"
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary_directory.cleanup()

    def queue_image(self, count=1):
        for _index in range(count):
            body = json.dumps({"data": [{"b64_json": PNG_BASE64}]}).encode("utf-8")
            self.state.responses.append((200, "application/json", body))

    def invoke(self, arguments, stdin=""):
        stdout = io.StringIO()
        stderr = io.StringIO()
        environment = {"LTS4AI_API_KEY": "cli-secret"}
        with (
            mock.patch.dict(os.environ, environment, clear=False),
            mock.patch("sys.stdin", io.StringIO(stdin)),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            status = module.main(arguments)
        return status, stdout.getvalue(), stderr.getvalue()

    def common_arguments(self):
        return [
            "--base-url", self.base_url,
            "--output-dir", str(self.directory / "outputs"),
            "--retries", "0",
        ]

    def test_api_key_resolution_prefers_environment_and_supports_stdin(self):
        with mock.patch.dict(os.environ, {"LTS4AI_API_KEY": "environment-secret"}):
            self.assertEqual(module.resolve_api_key(False), "environment-secret")
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("sys.stdin", io.StringIO("stdin-secret\n")),
        ):
            self.assertEqual(module.resolve_api_key(True), "stdin-secret")

    def test_empty_stdin_key_is_rejected(self):
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("sys.stdin", io.StringIO("\n")),
        ):
            with self.assertRaisesRegex(module.SkillError, "API key"):
                module.resolve_api_key(True)

    def test_parser_never_accepts_command_line_api_key(self):
        parser = module.build_parser()
        option_strings = {
            option
            for action in parser._actions
            for option in action.option_strings
        }
        for action in parser._subparsers._group_actions[0].choices.values():
            option_strings.update(
                option
                for sub_action in action._actions
                for option in sub_action.option_strings
            )
        self.assertNotIn("--api-key", option_strings)
        self.assertIn("--api-key-stdin", option_strings)

    def test_generate_command_writes_image_and_json_summary(self):
        self.queue_image()
        arguments = ["generate", "--prompt", "draw tea", *self.common_arguments()]
        status, stdout, stderr = self.invoke(arguments)
        summary = json.loads(stdout)
        self.assertEqual(status, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(summary["operation"], "generate")
        self.assertEqual(len(summary["outputs"]), 1)
        self.assertTrue(Path(summary["outputs"][0]["path"]).is_file())

    def test_edit_command_accepts_multiple_images(self):
        self.queue_image()
        first = self.directory / "first.png"
        second = self.directory / "second.png"
        first.write_bytes(PNG_BYTES)
        second.write_bytes(PNG_BYTES)
        arguments = [
            "edit", "--prompt", "polish product", "--image", str(first),
            "--image", str(second), *self.common_arguments(),
        ]
        status, stdout, _stderr = self.invoke(arguments)
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout)["operation"], "edit")
        self.assertEqual(self.state.requests[0]["path"], "/v1/images/edits")

    def test_models_command_lists_provider_models(self):
        response = json.dumps({"data": [{"id": "SF-gpt-image-2"}, {"id": "other"}]}).encode()
        self.state.responses.append((200, "application/json", response))
        status, stdout, _stderr = self.invoke([
            "models", "--base-url", self.base_url, "--retries", "0",
        ])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stdout)["models"], ["other", "SF-gpt-image-2"])

    def test_batch_repeats_prompt_with_bounded_concurrency_and_ordered_outputs(self):
        self.queue_image(3)
        arguments = [
            "batch", "--prompt", "premium product", "--count", "3",
            "--concurrency", "2", *self.common_arguments(),
        ]
        status, stdout, _stderr = self.invoke(arguments)
        outputs = json.loads(stdout)["outputs"]
        self.assertEqual(status, 0)
        self.assertEqual(len(outputs), 3)
        self.assertEqual([item["index"] for item in outputs], [1, 2, 3])
        self.assertEqual(len({item["path"] for item in outputs}), 3)
        self.assertEqual(len(self.state.requests), 3)

    def test_batch_reads_prompt_queue_and_rejects_more_than_ten(self):
        prompts_file = self.directory / "prompts.txt"
        prompts_file.write_text("\n".join(f"prompt {index}" for index in range(11)), encoding="utf-8")
        arguments = [
            "batch", "--prompts-file", str(prompts_file), *self.common_arguments(),
        ]
        status, stdout, stderr = self.invoke(arguments)
        self.assertEqual(status, 1)
        self.assertEqual(stdout, "")
        self.assertIn("10", stderr)
        self.assertNotIn("cli-secret", stderr)


class DocumentationTests(unittest.TestCase):
    def test_skill_metadata_and_workflow_are_complete(self):
        skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("name: sufy-image2", skill_text)
        self.assertIn("description: Use when", skill_text)
        self.assertIn("SF-gpt-image-2", skill_text)
        self.assertIn("LTS4AI_API_KEY", skill_text)
        self.assertIn("seed", skill_text.lower())
        self.assertRegex(skill_text.lower(), r"render|display")

    def test_references_document_exact_contract_and_key_safety(self):
        contract = (ROOT / "references" / "api-contract.md").read_text(encoding="utf-8")
        workflow = (ROOT / "references" / "agent-workflow.md").read_text(encoding="utf-8")
        self.assertIn("/v1/images/generations", contract)
        self.assertIn("/v1/images/edits", contract)
        self.assertIn("multipart/form-data", contract)
        self.assertIn("output_format", contract)
        self.assertIn("LTS4AI_API_KEY", workflow)
        self.assertIn("--api-key-stdin", workflow)
        self.assertIn("Never", workflow)

    def test_readme_has_installation_and_usage_without_real_secrets(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("安装", readme)
        self.assertIn("generate", readme)
        self.assertIn("edit", readme)
        self.assertIn("batch", readme)
        self.assertIn("MIT", readme)

        public_files = [
            ROOT / "SKILL.md",
            ROOT / "README.md",
            ROOT / "agents" / "openai.yaml",
            ROOT / "references" / "api-contract.md",
            ROOT / "references" / "agent-workflow.md",
        ]
        all_public_text = "\n".join(path.read_text(encoding="utf-8") for path in public_files)
        self.assertNotRegex(all_public_text, r"sk-[A-Za-z0-9]{16,}")
        self.assertNotRegex(all_public_text, r"gho_[A-Za-z0-9]+")


if __name__ == "__main__":
    unittest.main()
