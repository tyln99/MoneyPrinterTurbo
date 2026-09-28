"""
Reading and writing the persisted settings a generation form is built from.

`config.toml` is a single-user global file that the process mutates, and the
WebUI has always normalised what it reads: a value hand-edited into the file, or
left over from an older release, must not crash a widget or silently change a
generated video's parameters. That normalisation lived in `webui/Main.py` as the
`_saved_ui_*` helpers, so any second UI would have re-derived it -- differently.

Everything here is section-agnostic and free of Streamlit, so the WebUI, the
REST API and the CLI coerce a stored value the same way.
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

from loguru import logger

from app.config import config
from app.models.llm_provider import LLM_PROVIDER_REGISTRY

# The config sections a UI may write. Bound lazily by name rather than held as
# references, because the section globals are bound once at import
# (app/config/config.py) and a test that patches `config.app` replaces the
# object -- a captured reference would keep writing to the old dict.
WRITABLE_SECTIONS = (
    "app",
    "azure",
    "chatterbox",
    "kokoro",
    "elevenlabs",
    "minimax_tts",
    "siliconflow",
    "fish_audio",
    "voxcpm",
    "ui",
)

# 密钥按配置项名称后缀识别。新增 Provider 只要沿用现有命名，就会自动被
# 识别为凭据，不需要再维护第二份密钥清单。
CREDENTIAL_KEY_SUFFIXES = (
    "api_key",
    "api_keys",
    "api_token",
    "access_key",
    "secret_key",
    "speech_key",
)

# A write of this value means "leave the stored secret alone". A settings form
# renders a masked secret, and without a sentinel, saving the form would write
# the mask back over the real key.
UNCHANGED_SECRET = "__unchanged__"

_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}")


def section(name: str) -> dict:
    """The live config section dict, or an empty one for an unknown name."""
    return getattr(config, name, {}) if name in WRITABLE_SECTIONS else {}


def is_credential_key(key: str) -> bool:
    return str(key).endswith(CREDENTIAL_KEY_SUFFIXES)


def mask_secret(value: Any) -> dict:
    """Describe a secret without disclosing it.

    A settings form needs to show whether a key is configured and which one it
    is, which the last four characters give, without the response carrying a
    usable credential.
    """
    if isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value if str(item).strip()]
        return {"set": bool(items), "count": len(items), "hint": _hint(items[0] if items else "")}
    text = str(value or "").strip()
    return {"set": bool(text), "count": 1 if text else 0, "hint": _hint(text)}


def _hint(text: str) -> str:
    return f"…{text[-4:]}" if len(text) > 4 else ""


# ---- coercion -------------------------------------------------------------
# Each reads one stored value and degrades an illegal one to the default, so a
# hand-edited config.toml cannot break a form or change a render's parameters.


def saved_choice(section_name: str, key: str, options: Iterable, default: Any) -> Any:
    """Read a stored choice, degrading an unknown value to the default."""
    options = list(options)
    saved = section(section_name).get(key, default)
    numeric_default = isinstance(default, (int, float)) and not isinstance(default, bool)
    # bool 是 int 的子类，``True == 1``。手工把数值选项写成 TOML 布尔值时
    # 必须拒绝，不能让它伪装成第一个数值 option。
    if numeric_default and isinstance(saved, bool):
        return default
    for option in options:
        if saved == option:
            # 返回 options 中的真实值，顺便把 TOML 1.0 等价归一化为整数选项 1，
            # 避免下游参数类型随配置写法漂移。
            return option

    # TOML 中的数值通常保留原类型；仍兼容用户手工写成字符串的情况。
    if numeric_default and isinstance(saved, str):
        try:
            converted = type(default)(saved)
        except (TypeError, ValueError):
            converted = None
        for option in options:
            if converted == option:
                return option
    return default


def saved_number(
    section_name: str,
    key: str,
    default: float,
    minimum: float,
    maximum: float,
    number_type=float,
):
    """Read and clamp a stored number, so an illegal one cannot reach a slider."""
    try:
        saved = section(section_name).get(key, default)
        if isinstance(saved, bool):
            raise ValueError("boolean is not a numeric setting")
        value = number_type(saved)
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("non-finite value")
    except (TypeError, ValueError, OverflowError):
        value = default
    return min(maximum, max(minimum, value))


def saved_bool(section_name: str, key: str, default: bool) -> bool:
    """兼容 TOML 布尔值和常见手工字符串，拒绝含义不明的旧值。"""
    value = section(section_name).get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def saved_color(section_name: str, key: str, default: str) -> str:
    """Only a standard six-digit hex colour reaches a colour picker."""
    value = str(section(section_name).get(key, default) or "").strip()
    return value if _HEX_COLOR.fullmatch(value) else default


def saved_text(
    section_name: str, key: str, default: str = "", max_length: int | None = None
) -> str:
    value = str(section(section_name).get(key, default) or default)
    return value[:max_length] if max_length is not None else value


# ---- read / write ---------------------------------------------------------


def read_settings() -> dict:
    """
    Every writable section, with credentials replaced by a masked description.

    `ui` holds only presentation preferences and never a credential, so it comes
    back verbatim.
    """
    result: dict[str, dict] = {}
    for name in WRITABLE_SECTIONS:
        values: dict[str, Any] = {}
        for key, value in section(name).items():
            values[key] = mask_secret(value) if is_credential_key(key) else value
        result[name] = values
    return result


def _coerce_incoming(key: str, value: Any) -> Any:
    """Normalise one incoming value before it reaches config.toml.

    A comma-separated string is how the WebUI collects the `*_api_keys` fields,
    which the services read as a list; accepting both keeps one storage shape.
    """
    if key.endswith("api_keys") and isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, str):
        return value.strip()
    return value


def apply_settings(updates: dict[str, dict]) -> list[str]:
    """
    Write settings and persist them, returning the `section.key` names changed.

    A secret sent as `UNCHANGED_SECRET` is skipped, so saving a form built from
    `read_settings` cannot overwrite a real key with its own mask. An unknown
    section or a non-dict payload is ignored rather than raising: a partial
    write is better than rejecting a form over one stale field.
    """
    changed: list[str] = []
    for name, values in (updates or {}).items():
        if name not in WRITABLE_SECTIONS or not isinstance(values, dict):
            logger.warning(f"ignoring settings for unknown section: {name!r}")
            continue
        target = section(name)
        for key, value in values.items():
            if is_credential_key(key) and value == UNCHANGED_SECRET:
                continue
            coerced = _coerce_incoming(str(key), value)
            if target.get(key) != coerced:
                target[key] = coerced
                changed.append(f"{name}.{key}")

    if changed:
        config.save_config()
        logger.info(f"settings updated: {', '.join(changed)}")
    return changed


def llm_provider_keys(provider_id: str) -> dict[str, str]:
    """The `[app]` keys that configure one LLM provider."""
    spec = next(
        (item for item in LLM_PROVIDER_REGISTRY if item.provider_id == provider_id),
        None,
    )
    if spec is None:
        return {}
    keys = {
        "api_key": spec.config_key("api_key"),
        "base_url": spec.config_key("base_url"),
        "model_name": spec.config_key("model_name"),
    }
    for field in spec.extra_fields:
        keys[field.config_suffix] = spec.config_key(field.config_suffix)
    return keys
