import importlib.util
import struct
import sys
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
