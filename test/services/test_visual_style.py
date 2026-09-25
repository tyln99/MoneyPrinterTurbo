import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.config import config
from app.services import material


class TestVisualStyleResolution(unittest.TestCase):
    """
    Style is one shared setting across every generated-material source, so a
    preset picked once survives switching provider.
    """

    def _resolve(self, **overrides):
        with patch.object(config, "app", dict(config.app, **overrides)):
            return material.resolve_visual_style_template()

    def _apply(self, term, **overrides):
        with patch.object(config, "app", dict(config.app, **overrides)):
            return material.apply_visual_style(term)

    def test_named_preset_is_resolved(self):
        template = self._resolve(visual_style="watercolor", visual_style_template="")
        self.assertEqual(template, material.VISUAL_STYLE_PRESETS["watercolor"])

    def test_preset_wins_over_a_previously_typed_template(self):
        """
        Otherwise a template typed once would keep overriding every preset chosen
        afterwards, and the dropdown would look broken.
        """
        template = self._resolve(
            visual_style="anime", visual_style_template="oil painting of {term}"
        )
        self.assertEqual(template, material.VISUAL_STYLE_PRESETS["anime"])

    def test_custom_preset_uses_the_typed_template(self):
        template = self._resolve(
            visual_style="custom", visual_style_template="oil painting of {term}"
        )
        self.assertEqual(template, "oil painting of {term}")

    def test_unknown_preset_falls_through_instead_of_disabling_style(self):
        """A typo in config.toml should degrade, not silently drop the style."""
        template = self._resolve(
            visual_style="nonsense", visual_style_template="oil painting of {term}"
        )
        self.assertEqual(template, "oil painting of {term}")

    def test_legacy_key_still_works_when_no_style_is_configured(self):
        """Configs written before the shared layer must keep their look."""
        template = self._resolve(
            visual_style="",
            visual_style_template="",
            openai_image_prompt_template="retro poster of {term}",
        )
        self.assertEqual(template, "retro poster of {term}")

    def test_preset_takes_precedence_over_the_legacy_key(self):
        template = self._resolve(
            visual_style="flat",
            visual_style_template="",
            openai_image_prompt_template="retro poster of {term}",
        )
        self.assertEqual(template, material.VISUAL_STYLE_PRESETS["flat"])


class TestVisualStyleApplication(unittest.TestCase):
    def test_keyword_is_substituted_into_the_template(self):
        prompt = self._apply_style("a quiet street", visual_style="watercolor")
        self.assertIn("a quiet street", prompt)
        self.assertIn("watercolor", prompt)
        self.assertNotIn("{term}", prompt)

    def _apply_style(self, term, **overrides):
        with patch.object(config, "app", dict(config.app, **overrides)):
            return material.apply_visual_style(term)

    def test_bare_keyword_when_no_style_is_configured(self):
        prompt = self._apply_style(
            "a quiet street",
            visual_style="",
            visual_style_template="",
            openai_image_prompt_template="",
        )
        self.assertEqual(prompt, "a quiet street")

    def test_template_without_the_placeholder_is_ignored(self):
        """
        A template that forgot {term} would otherwise send the same prompt for
        every scene and produce one image repeated.
        """
        prompt = self._apply_style(
            "a quiet street",
            visual_style="custom",
            visual_style_template="watercolor painting",
        )
        self.assertEqual(prompt, "a quiet street")

    def test_every_preset_carries_the_placeholder(self):
        for name, template in material.VISUAL_STYLE_PRESETS.items():
            with self.subTest(preset=name):
                self.assertIn("{term}", template)


if __name__ == "__main__":
    unittest.main()
