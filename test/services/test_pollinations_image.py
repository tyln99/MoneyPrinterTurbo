import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.models.schema import VideoAspect
from app.services import material, pollinations_image


RUN_INTEGRATION_TESTS = os.environ.get("MPT_RUN_INTEGRATION_TESTS", "").lower() in {
    "1",
    "true",
    "yes",
}


def _png_bytes(width: int = 64, height: int = 96) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeResponse:
    def __init__(self, status_code, content=b"", content_type="image/jpeg", text=""):
        self.status_code = status_code
        self.content = content
        self.headers = {"Content-Type": content_type}
        self.text = text or ""


class TestPollinationsImageUrl(unittest.TestCase):
    def test_provider_is_always_enabled(self):
        """The service needs no credentials, so it can never be misconfigured."""
        self.assertTrue(pollinations_image.is_enabled())

    def test_prompt_is_percent_encoded_into_the_path(self):
        """A slash in the prompt must not escape into the route."""
        url = pollinations_image.build_image_url("a/b night sky", 100, 200)
        path = urlsplit(url).path
        self.assertIn("a%2Fb%20night%20sky", path)
        self.assertEqual(path.count("/prompt/"), 1)

    def test_size_and_model_are_sent_as_query_parameters(self):
        url = pollinations_image.build_image_url("apple", 1080, 1920)
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["width"], ["1080"])
        self.assertEqual(query["height"], ["1920"])
        self.assertEqual(query["nologo"], ["true"])
        self.assertEqual(query["model"], [pollinations_image.DEFAULT_MODEL])

    def test_seed_is_only_sent_when_provided(self):
        """Without a seed the service is deterministic per prompt."""
        with_seed = parse_qs(
            urlsplit(pollinations_image.build_image_url("a", 10, 10, seed=42)).query
        )
        without_seed = parse_qs(
            urlsplit(pollinations_image.build_image_url("a", 10, 10)).query
        )
        self.assertEqual(with_seed["seed"], ["42"])
        self.assertNotIn("seed", without_seed)


class TestPollinationsImageRequest(unittest.TestCase):
    def test_image_response_is_returned(self):
        payload = _png_bytes()
        with patch.object(
            pollinations_image.requests,
            "get",
            return_value=_FakeResponse(200, payload, "image/png"),
        ):
            data, detail = pollinations_image.generate_image("apple", 100, 200)
        self.assertEqual(data, payload)
        self.assertEqual(detail, "")

    def test_json_body_on_a_200_is_treated_as_a_failure(self):
        """The service reports upstream errors in the body with a 200 status."""
        with (
            patch.object(pollinations_image.time, "sleep"),
            patch.object(
                pollinations_image.requests,
                "get",
                return_value=_FakeResponse(
                    200, b"{}", "application/json", text='{"error":"boom"}'
                ),
            ),
        ):
            data, detail = pollinations_image.generate_image("apple", 100, 200)
        self.assertIsNone(data)
        self.assertIn("application/json", detail)

    def test_rate_limit_is_retried_until_it_succeeds(self):
        """429 is routine on this shared service and nothing is billed."""
        payload = _png_bytes()
        responses = [
            _FakeResponse(429, text="rate limited"),
            _FakeResponse(200, payload, "image/png"),
        ]
        with (
            patch.object(pollinations_image.time, "sleep") as sleep,
            patch.object(
                pollinations_image.requests, "get", side_effect=responses
            ) as get,
        ):
            data, _ = pollinations_image.generate_image("apple", 100, 200)
        self.assertEqual(data, payload)
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once()

    def test_non_retryable_status_fails_immediately(self):
        """A 400 is a definite rejection; retrying only wastes time."""
        with (
            patch.object(pollinations_image.time, "sleep") as sleep,
            patch.object(
                pollinations_image.requests,
                "get",
                return_value=_FakeResponse(400, text="bad request"),
            ) as get,
        ):
            data, detail = pollinations_image.generate_image("apple", 100, 200)
        self.assertIsNone(data)
        self.assertIn("HTTP 400", detail)
        self.assertEqual(get.call_count, 1)
        sleep.assert_not_called()

    def test_transport_errors_are_retried(self):
        with (
            patch.object(pollinations_image.time, "sleep"),
            patch.object(
                pollinations_image.requests, "get", side_effect=OSError("boom")
            ) as get,
        ):
            data, detail = pollinations_image.generate_image("apple", 100, 200)
        self.assertIsNone(data)
        self.assertIn("OSError", detail)
        self.assertEqual(get.call_count, pollinations_image.MAX_ATTEMPTS)


class TestGenerateImagesPollinations(unittest.TestCase):
    def test_material_is_saved_with_the_real_image_size(self):
        """Pollinations caps resolution, so record what came back, not what was asked."""
        with tempfile.TemporaryDirectory() as save_dir:
            with patch.object(
                material.pollinations_image,
                "generate_image",
                return_value=(_png_bytes(64, 96), ""),
            ):
                items = material.generate_images_pollinations(
                    search_term="morning coffee",
                    minimum_duration=5,
                    video_aspect=VideoAspect.portrait,
                    save_dir=save_dir,
                )
            self.assertEqual(len(items), 1)
            item = items[0]
            self.assertEqual(item.provider, "pollinations_image")
            self.assertEqual(item.duration, 5)
            self.assertTrue(os.path.isfile(item.url))
            self.assertIn("pollinations-image", os.path.basename(item.url))
            self.assertEqual(item.source_info["rendition"]["width"], 64)
            self.assertEqual(item.source_info["rendition"]["height"], 96)

    def test_output_canvas_size_is_requested(self):
        """Ask for the real canvas; the service scales down but keeps the ratio."""
        with tempfile.TemporaryDirectory() as save_dir:
            with patch.object(
                material.pollinations_image,
                "generate_image",
                return_value=(_png_bytes(), ""),
            ) as generate:
                material.generate_images_pollinations(
                    search_term="apple",
                    minimum_duration=5,
                    video_aspect=VideoAspect.portrait,
                    save_dir=save_dir,
                )
        width, height = VideoAspect.portrait.to_resolution()
        self.assertEqual(generate.call_args.kwargs["width"], width)
        self.assertEqual(generate.call_args.kwargs["height"], height)
        self.assertIsNotNone(generate.call_args.kwargs["seed"])

    def test_failed_generation_skips_the_keyword(self):
        """An empty list tells the on-demand loop to move to the next keyword."""
        with patch.object(
            material.pollinations_image,
            "generate_image",
            return_value=(None, "HTTP 500"),
        ):
            items = material.generate_images_pollinations(
                search_term="apple", minimum_duration=5
            )
        self.assertEqual(items, [])

    def test_undecodable_bytes_skip_the_keyword_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as save_dir:
            with patch.object(
                material.pollinations_image,
                "generate_image",
                return_value=(b"not an image", ""),
            ):
                items = material.generate_images_pollinations(
                    search_term="apple", minimum_duration=5, save_dir=save_dir
                )
        self.assertEqual(items, [])


class TestPollinationsLiveGeneration(unittest.TestCase):
    @unittest.skipUnless(RUN_INTEGRATION_TESTS, "MPT_RUN_INTEGRATION_TESTS not set")
    def test_live_request_returns_a_decodable_image(self):
        data, detail = pollinations_image.generate_image(
            "cinematic photo of a red apple", 1080, 1920, seed=7
        )
        self.assertIsNotNone(data, detail)
        image = Image.open(io.BytesIO(data))
        # Resolution is capped but the 9:16 ratio must survive.
        self.assertAlmostEqual(image.width / image.height, 1080 / 1920, places=2)


if __name__ == "__main__":
    unittest.main()
