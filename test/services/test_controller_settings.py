import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app import asgi  # noqa: E402
from app.config import config  # noqa: E402
from app.services import catalog, ui_settings  # noqa: E402


class TestSecretsNeverLeave(unittest.TestCase):
    def test_a_credential_is_described_not_disclosed(self):
        masked = ui_settings.mask_secret("sk-abcdef123456")
        self.assertTrue(masked["set"])
        self.assertEqual(masked["hint"], "…3456")
        self.assertNotIn("sk-abcdef123456", str(masked))

    def test_a_short_credential_gets_no_hint(self):
        """Four characters or fewer is the whole key, so a hint would be it."""
        self.assertEqual(ui_settings.mask_secret("abcd")["hint"], "")

    def test_a_list_of_keys_reports_how_many(self):
        masked = ui_settings.mask_secret(["one-key-1111", "two-key-2222"])
        self.assertEqual(masked["count"], 2)
        self.assertTrue(masked["set"])

    def test_an_empty_credential_reads_as_unset(self):
        for empty in ("", None, []):
            with self.subTest(empty=empty):
                self.assertFalse(ui_settings.mask_secret(empty)["set"])

    def test_every_credential_suffix_is_recognised(self):
        for key in (
            "pexels_api_keys",
            "moonshot_api_key",
            "loomloom_api_token",
            "azure_speech_key",
            "some_secret_key",
            "aws_access_key",
        ):
            with self.subTest(key=key):
                self.assertTrue(ui_settings.is_credential_key(key))
        self.assertFalse(ui_settings.is_credential_key("video_source"))

    def test_the_settings_endpoint_returns_no_raw_key(self):
        secret = "super-secret-value-9999"
        with patch.object(config, "app", dict(config.app, pexels_api_keys=[secret])):
            body = TestClient(asgi.app).get("/api/v1/settings").json()
        self.assertNotIn(secret, str(body))
        self.assertEqual(body["data"]["sections"]["app"]["pexels_api_keys"]["hint"], "…9999")


class TestSettingsWrites(unittest.TestCase):
    """Every test here patches `config.config_file` so the developer's own
    config.toml is never rewritten (see test/services/test_config.py)."""

    def setUp(self):
        self._tmp = Path(__file__).parent / "_settings_test_config.toml"
        self._file_patcher = patch.object(config, "config_file", str(self._tmp))
        self._file_patcher.start()

    def tearDown(self):
        self._file_patcher.stop()
        self._tmp.unlink(missing_ok=True)

    def test_a_value_is_written_and_reported(self):
        with patch.object(config, "ui", dict(config.ui)):
            changed = ui_settings.apply_settings({"ui": {"font_size": 72}})
            self.assertIn("ui.font_size", changed)
            self.assertEqual(config.ui["font_size"], 72)

    def test_an_unchanged_value_is_not_reported(self):
        with patch.object(config, "ui", dict(config.ui, font_size=72)):
            self.assertEqual(ui_settings.apply_settings({"ui": {"font_size": 72}}), [])

    def test_the_unchanged_sentinel_leaves_a_secret_alone(self):
        """
        A form renders a masked secret. Saving it must not write the mask over
        the real key, so the client sends a sentinel instead.
        """
        with patch.object(config, "app", dict(config.app, pexels_api_keys=["real-key"])):
            changed = ui_settings.apply_settings(
                {"app": {"pexels_api_keys": ui_settings.UNCHANGED_SECRET}}
            )
            self.assertEqual(changed, [])
            self.assertEqual(config.app["pexels_api_keys"], ["real-key"])

    def test_a_comma_separated_key_list_is_stored_as_a_list(self):
        """The services read `*_api_keys` as a list; a form submits one string."""
        with patch.object(config, "app", dict(config.app)):
            ui_settings.apply_settings({"app": {"pexels_api_keys": "one, two ,three"}})
            self.assertEqual(config.app["pexels_api_keys"], ["one", "two", "three"])

    def test_an_unknown_section_is_ignored_not_fatal(self):
        """One stale field must not cost the user the rest of their form."""
        with patch.object(config, "ui", dict(config.ui)):
            changed = ui_settings.apply_settings(
                {"nonsense": {"a": 1}, "ui": {"font_size": 41}}
            )
        self.assertEqual(changed, ["ui.font_size"])

    def test_the_put_endpoint_persists(self):
        with patch.object(config, "ui", dict(config.ui)):
            response = TestClient(asgi.app).put(
                "/api/v1/settings", json={"sections": {"ui": {"video_count": 3}}}
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["data"]["changed"], ["ui.video_count"])
            self.assertEqual(config.ui["video_count"], 3)


class TestCoercionDegradesBadConfig(unittest.TestCase):
    """A hand-edited config.toml must not break a form or change a render."""

    def test_an_unknown_choice_falls_back_to_the_default(self):
        with patch.object(config, "ui", {"video_aspect": "21:9"}):
            self.assertEqual(
                ui_settings.saved_choice("ui", "video_aspect", ["9:16", "16:9"], "9:16"),
                "9:16",
            )

    def test_a_boolean_cannot_masquerade_as_the_first_numeric_option(self):
        """`bool` subclasses `int`, so `True == 1` would silently pick option 1."""
        with patch.object(config, "ui", {"video_count": True}):
            self.assertEqual(ui_settings.saved_choice("ui", "video_count", [1, 2], 2), 2)

    def test_a_numeric_string_still_matches_a_numeric_option(self):
        with patch.object(config, "ui", {"video_count": "2"}):
            self.assertEqual(ui_settings.saved_choice("ui", "video_count", [1, 2], 1), 2)

    def test_a_number_is_clamped_to_its_range(self):
        with patch.object(config, "ui", {"font_size": 9999}):
            self.assertEqual(ui_settings.saved_number("ui", "font_size", 60, 30, 100, int), 100)

    def test_a_non_finite_number_falls_back(self):
        with patch.object(config, "ui", {"voice_rate": float("inf")}):
            self.assertEqual(ui_settings.saved_number("ui", "voice_rate", 1.0, 0.5, 2.0), 1.0)

    def test_common_boolean_spellings_are_accepted(self):
        for stored, expected in (("yes", True), ("off", False), ("1", True), ("maybe", True)):
            with self.subTest(stored=stored), patch.object(
                config, "ui", {"subtitle_enabled": stored}
            ):
                # "maybe" is meaningless, so it degrades to the default (True).
                self.assertIs(ui_settings.saved_bool("ui", "subtitle_enabled", True), expected)

    def test_only_a_six_digit_hex_colour_is_accepted(self):
        for stored in ("#fff", "white", "", "#12345g"):
            with self.subTest(stored=stored), patch.object(
                config, "ui", {"text_fore_color": stored}
            ):
                self.assertEqual(
                    ui_settings.saved_color("ui", "text_fore_color", "#FFFFFF"), "#FFFFFF"
                )
        with patch.object(config, "ui", {"text_fore_color": "#A1B2C3"}):
            self.assertEqual(
                ui_settings.saved_color("ui", "text_fore_color", "#FFFFFF"), "#A1B2C3"
            )


class TestCatalogEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(asgi.app)

    def test_the_catalog_carries_what_a_form_needs(self):
        data = self.client.get("/api/v1/catalog").json()["data"]
        for key in ("fonts", "tts_servers", "video_sources", "llm_providers", "songs", "visual_styles"):
            self.assertIn(key, data)
        self.assertTrue(data["fonts"], "no fonts found under resource/fonts")
        self.assertIn("pollinations_image", data["video_sources"]["ai_image"])

    def test_the_catalog_offers_exactly_what_submit_accepts(self):
        """
        The dropdown and the submit-time whitelist drifted once already
        ("pollinations_image" was selectable but rejected). Both now derive from
        the same service constant; this pins that they still agree.
        """
        data = self.client.get("/api/v1/catalog").json()["data"]
        offered = {source for group in data["video_sources"].values() for source in group}
        self.assertEqual(offered, set(catalog.SELECTABLE_VIDEO_SOURCES))

    def test_edge_tts_voices_need_no_credentials(self):
        """The free default must populate without any key configured."""
        data = self.client.get("/api/v1/catalog/voices?tts_server=azure-tts-v1").json()["data"]
        self.assertTrue(data["voices"])
        values = [voice["value"] for voice in data["voices"]]
        self.assertTrue(all("V2" not in value for value in values))

    def test_azure_v2_is_the_complement_of_v1(self):
        v2 = self.client.get("/api/v1/catalog/voices?tts_server=azure-tts-v2").json()["data"]
        self.assertTrue(all("V2" in voice["value"] for voice in v2["voices"]))

    def test_an_unreachable_provider_returns_an_empty_list_not_an_error(self):
        """A form must still render so the user can go and set the key."""
        with patch.object(
            catalog.voice, "get_fish_audio_voices", side_effect=RuntimeError("no network")
        ):
            response = self.client.get("/api/v1/catalog/voices?tts_server=fish_audio")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["voices"], [])

    def test_voice_labels_are_readable(self):
        self.assertEqual(
            catalog.friendly_voice_name("en-US-AvaNeural-Female"), "en-US-Ava-Female"
        )
        self.assertEqual(catalog.friendly_voice_name("elevenlabs:abc123:Rachel"), "Rachel")
        self.assertEqual(catalog.friendly_voice_name("kokoro:af_bella-Female"), "af_bella")


if __name__ == "__main__":
    unittest.main()


class TestWizardLlmSteps(unittest.TestCase):
    """
    The two buttons the script step offers. The routes already existed; these
    pin the request and response shapes the form depends on, because the wizard
    reads `data.video_script` and `data.video_terms` directly.
    """

    def setUp(self):
        self.client = TestClient(asgi.app)

    def test_generate_script_returns_the_script_under_a_stable_key(self):
        from app.services import llm

        with patch.object(llm, "generate_script", return_value="a script") as generate:
            body = self.client.post(
                "/api/v1/scripts",
                json={
                    "video_subject": "sleep habits",
                    "video_language": "",
                    "paragraph_number": 2,
                },
            ).json()

        self.assertEqual(body["data"]["video_script"], "a script")
        self.assertEqual(generate.call_args.kwargs["paragraph_number"], 2)

    def test_generate_terms_returns_a_list_the_form_can_join(self):
        from app.services import llm

        with patch.object(llm, "generate_terms", return_value=["one", "two"]):
            body = self.client.post(
                "/api/v1/terms",
                json={"video_subject": "sleep", "video_script": "text", "amount": 5},
            ).json()

        self.assertEqual(body["data"]["video_terms"], ["one", "two"])

    def test_a_script_request_needs_no_key_the_form_cannot_supply(self):
        """
        The wizard sends only subject, language and paragraph count. Anything
        else must have a server-side default, or the button would 400.
        """
        from app.services import llm

        with patch.object(llm, "generate_script", return_value="ok"):
            response = self.client.post(
                "/api/v1/scripts", json={"video_subject": "sleep habits"}
            )
        self.assertEqual(response.status_code, 200)
