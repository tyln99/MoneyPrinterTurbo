import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.config import config


ROOT_DIR = Path(__file__).parent.parent.parent
WEBUI_MAIN = ROOT_DIR / "webui" / "Main.py"
I18N_DIR = ROOT_DIR / "webui" / "i18n"
LOCALE = "en"


def _translation(key):
    """Read the expected copy for the test locale instead of hard-coding one language."""
    data = json.loads((I18N_DIR / f"{LOCALE}.json").read_text(encoding="utf-8"))
    return data["Translation"][key]


class TestWebuiSubtitleSource(unittest.TestCase):
    """Subtitle source selector: default, options, and the warning on the combination
    that silently drops subtitles."""

    def _run(self, *, subtitle_provider="edge", voice_mode="tts"):
        app = AppTest.from_file(str(WEBUI_MAIN), default_timeout=90)
        app.session_state["app_view"] = "create"
        app.session_state["ui_language"] = LOCALE
        # The voice panel renders before the subtitle panel, so seeding the widget
        # state here is enough to reproduce a given combination.
        app.session_state[f"voice_mode_control_{LOCALE}"] = voice_mode
        test_app_config = dict(config.app, subtitle_provider=subtitle_provider)
        # The warning only applies when subtitles are on, so the test states that
        # rather than inheriting whatever the developer last set in the UI.
        test_ui_config = dict(config.ui, subtitle_enabled=True)
        with (
            patch.object(config, "app", test_app_config),
            patch.object(config, "ui", test_ui_config),
            # A unit test must never write back to the real config.toml at the repo root.
            patch.object(config, "try_save_config", return_value=True),
            patch.object(config, "save_config"),
        ):
            app.run()
        self.assertEqual([str(item.value) for item in app.exception], [])
        return app

    def _selector(self, app):
        return next(
            item
            for item in app.selectbox
            if str(getattr(item, "key", "")) == f"subtitle_provider_select_{LOCALE}"
        )

    def test_subtitle_source_is_selectable_in_the_webui(self):
        """The subtitle source used to be config.toml only; it must now be in the UI."""
        selector = self._selector(self._run())
        # AppTest exposes the post-format_func labels, so assert against the
        # translations, which also covers that both options are wired to i18n.
        self.assertEqual(
            list(selector.options),
            [
                _translation("Subtitle Source Edge"),
                _translation("Subtitle Source Whisper"),
            ],
        )

    def test_edge_is_the_default_subtitle_source(self):
        """Default to Edge: reuses the TTS timeline, instant and downloads no model."""
        self.assertEqual(self._selector(self._run()).value, "edge")

    def test_saved_whisper_choice_is_restored(self):
        """A user who picked Whisper must not be silently reset to Edge on reload."""
        app = self._run(subtitle_provider="whisper")
        self.assertEqual(self._selector(app).value, "whisper")

    def test_uploaded_audio_with_edge_warns_about_missing_subtitles(self):
        """Uploaded audio has no TTS timeline, so Edge ships no subtitles: warn up front."""
        app = self._run(subtitle_provider="edge", voice_mode="upload")
        warnings = [str(item.value) for item in app.warning]
        self.assertIn(_translation("Subtitle Source Edge Upload Warning"), warnings)

    def test_uploaded_audio_with_whisper_does_not_warn(self):
        """Whisper transcribes uploaded audio directly, so this pairing must not warn."""
        app = self._run(subtitle_provider="whisper", voice_mode="upload")
        warnings = [str(item.value) for item in app.warning]
        self.assertNotIn(_translation("Subtitle Source Edge Upload Warning"), warnings)

    def test_tts_mode_with_edge_does_not_warn(self):
        """Edge is the recommended pairing in TTS mode; no false positive allowed."""
        app = self._run(subtitle_provider="edge", voice_mode="tts")
        warnings = [str(item.value) for item in app.warning]
        self.assertNotIn(_translation("Subtitle Source Edge Upload Warning"), warnings)


if __name__ == "__main__":
    unittest.main()
