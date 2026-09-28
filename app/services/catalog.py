"""
The option lists a generation form has to render: fonts, voices, video sources,
TTS providers, LLM providers, background music.

These lived inside `webui/Main.py` as module-level constants, `os.walk` helpers
and a 60-line if/elif chain, which meant a second UI could not offer the same
choices without copying them. Nothing here renders anything or depends on
Streamlit; labels come back untranslated so each UI can localise them (Streamlit
through `tr()`, React through its own catalogue).
"""

from __future__ import annotations

import os

from loguru import logger

from app.models.llm_provider import LLM_PROVIDER_REGISTRY
from app.services import voice
from app.utils import utils

# WebUI 按素材能力分组展示视频来源，但底层仍保存原有 video_source 值。
# AI 视频组与设置页共用同一业务顺序：合作服务商优先，并按秘塔、OFox、
# 胜算云、火山引擎排列；其余服务随后展示。这样两个入口的顺序一致，同时
# 不改变 config.toml、历史任务和 API 请求中的字段语义，旧用户无需迁移配置。
VIDEO_SOURCE_GROUPS: dict[str, tuple[str, ...]] = {
    "stock_video": ("pexels", "pixabay", "coverr"),
    "ai_video": (
        "metaso_minimax",
        "ofox",
        "loomloom",
        "volcengine_seedance",
        "wavespeed",
        "muapi",
    ),
    "ai_image": ("openai_image", "pollinations_image"),
    "local": ("local",),
}

# Flattened once so validation cannot fall behind what the dropdown offers.
SELECTABLE_VIDEO_SOURCES = frozenset(
    source for group in VIDEO_SOURCE_GROUPS.values() for source in group
)

DEFAULT_TTS_SERVER = "azure-tts-v1"

# 顺序即界面展示顺序：免费的 Edge TTS 在最前，其余按接入时间排列。
TTS_SERVERS: tuple[tuple[str, str], ...] = (
    ("azure-tts-v1", "Azure TTS V1 (Edge TTS)"),
    ("azure-tts-v2", "Azure TTS V2"),
    ("siliconflow", "SiliconFlow TTS"),
    ("gemini-tts", "Google Gemini TTS"),
    ("mimo-tts", "Xiaomi MiMo TTS"),
    ("minimax-tts", "MiniMax TTS"),
    ("elevenlabs", "ElevenLabs TTS"),
    ("chatterbox", "Chatterbox TTS"),
    ("kokoro", "Kokoro TTS"),
    ("fish_audio", "Fish Audio TTS"),
    ("voxcpm", "VoxCPM TTS"),
)

FONT_EXTENSIONS = (".ttf", ".ttc")


def list_fonts() -> list[str]:
    """Font file names under resource/fonts, sorted."""
    font_dir = utils.font_dir()
    fonts: list[str] = []
    for _root, _dirs, files in os.walk(font_dir):
        fonts.extend(name for name in files if name.endswith(FONT_EXTENSIONS))
    fonts.sort()
    return fonts


def list_tts_servers() -> list[dict]:
    return [{"value": value, "label": label} for value, label in TTS_SERVERS]


def list_video_sources() -> dict[str, list[str]]:
    return {group: list(sources) for group, sources in VIDEO_SOURCE_GROUPS.items()}


def list_llm_providers() -> list[dict]:
    """Enough of each provider spec to build a settings form."""
    return [
        {
            "provider_id": spec.provider_id,
            "label": spec.default_label,
            "api_key_url": spec.api_key_url,
            "default_model": spec.default_model,
            "default_base_url": spec.default_base_url,
            "requires_api_key": spec.requires_api_key,
            "requires_model_name": spec.requires_model_name,
            "requires_base_url": spec.requires_base_url,
            "show_api_key": spec.show_api_key,
            "show_base_url": spec.show_base_url,
            "extra_fields": [
                {
                    "config_suffix": field.config_suffix,
                    "required": field.required,
                    "secret": field.secret,
                    "default_value": field.default_value,
                }
                for field in spec.extra_fields
            ],
        }
        for spec in LLM_PROVIDER_REGISTRY
    ]


def friendly_voice_name(voice_name: str) -> str:
    """
    A display name for a raw voice id.

    Lifted from the WebUI's inner `_friendly`. The gender words are left in
    English here rather than translated in place, because the same value is
    consumed by two UIs with separate translation catalogues.
    """
    if voice.is_no_voice(voice_name):
        return "No Voice"
    if voice.is_elevenlabs_voice(voice_name):
        parts = voice_name.split(":", 2)
        return parts[2] if len(parts) >= 3 else voice_name
    if voice.is_chatterbox_voice(voice_name) or voice.is_kokoro_voice(voice_name):
        name = voice_name.split(":", 1)[1] if ":" in voice_name else voice_name
        return name.replace("-Female", "").replace("-Male", "")
    if voice.is_minimax_voice(voice_name):
        return voice_name.split(":", 1)[1]
    if voice.is_fish_audio_voice(voice_name):
        parts = voice_name.split(":", 2)
        return parts[2] if len(parts) >= 3 else voice_name
    if voice.is_voxcpm_voice(voice_name):
        return voice_name.split(":", 1)[1] or voice_name
    return voice_name.replace("Neural", "")


def _azure_voices(tts_server: str) -> list[str]:
    """Azure V1 and V2 share one catalogue, told apart by "V2" in the name."""
    wants_v2 = tts_server == "azure-tts-v2"
    return [
        name
        for name in voice.get_all_azure_voices(filter_locals=None)
        if ("V2" in name) == wants_v2
    ]


def list_voices(tts_server: str, elevenlabs_api_key: str = "") -> list[dict]:
    """
    The voices a TTS provider offers, as `{value, label}`.

    Several providers reach out over the network (ElevenLabs, Kokoro, MiniMax,
    Fish Audio). A provider that is unreachable or unconfigured returns an empty
    list rather than raising, so a settings form still renders.
    """
    try:
        if tts_server == "siliconflow":
            names = voice.get_siliconflow_voices()
        elif tts_server == "gemini-tts":
            names = voice.get_gemini_voices()
        elif tts_server == "mimo-tts":
            names = voice.get_mimo_voices()
        elif tts_server == "minimax-tts":
            names = voice.get_minimax_voices()
        elif tts_server == "elevenlabs":
            names = voice.get_elevenlabs_voices(elevenlabs_api_key)
        elif tts_server == "chatterbox":
            names = voice.get_chatterbox_voices()
        elif tts_server == "kokoro":
            names = voice.get_kokoro_voices()
        elif tts_server == "fish_audio":
            names = voice.get_fish_audio_voices()
        elif tts_server == "voxcpm":
            names = voice.get_voxcpm_voices()
        else:
            names = _azure_voices(tts_server)
    except Exception as e:
        logger.error(f"failed to list {tts_server} voices: {type(e).__name__}: {e}")
        return []

    return [{"value": name, "label": friendly_voice_name(name)} for name in names]
