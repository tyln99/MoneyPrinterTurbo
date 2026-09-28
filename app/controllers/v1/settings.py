"""
The option lists and saved defaults a generation form is built from.

Creating a video already works over HTTP -- `POST /api/v1/videos` takes the full
`VideoParams`. What had no endpoint was everything needed to *build* the form:
which voices a provider offers, which fonts are installed, which video sources
exist, and what the user chose last time. Without these a second UI can only
hard-code its options and forget every choice between visits.

Credentials are never returned. `read_settings` replaces each one with
`{set, count, hint}`, and a form saves an unchanged secret back as the
`UNCHANGED_SECRET` sentinel rather than as the mask it rendered.
"""

import os

from fastapi import Body, Depends, Query, Request

from app.controllers import base
from app.controllers.v1.base import new_router
from app.models.schema import (
    CatalogResponse,
    SettingsResponse,
    SettingsUpdateRequest,
    SettingsUpdateResponse,
    VoiceListResponse,
)
from app.services import bgm as bgm_service
from app.services import catalog, ui_settings
from app.utils import utils

router = new_router(dependencies=[Depends(base.verify_token)])


@router.get(
    "/catalog",
    response_model=CatalogResponse,
    summary="Options a generation form is built from",
)
def get_catalog(request: Request):
    """Everything cheap and local, in one request.

    Voices are deliberately not here: several providers fetch them over the
    network, so one slow provider would hold up the whole form.
    """
    return utils.get_response(
        200,
        {
            "fonts": catalog.list_fonts(),
            "tts_servers": catalog.list_tts_servers(),
            "video_sources": catalog.list_video_sources(),
            "llm_providers": catalog.list_llm_providers(),
            "songs": bgm_service.list_builtin_bgm_files(),
            "visual_styles": _visual_styles(),
        },
    )


def _visual_styles() -> list[str]:
    from app.services.material import VISUAL_STYLE_PRESETS

    return list(VISUAL_STYLE_PRESETS)


@router.get(
    "/catalog/voices",
    response_model=VoiceListResponse,
    summary="Voices offered by one TTS provider",
)
def get_voices(
    request: Request,
    tts_server: str = Query(catalog.DEFAULT_TTS_SERVER),
):
    """
    A provider that is unconfigured or unreachable returns an empty list rather
    than an error, so the form still renders and the user can go and set a key.
    """
    api_key = str(ui_settings.section("elevenlabs").get("api_key", "") or "")
    # Env override matches how the service itself resolves the key.
    api_key = api_key or os.getenv("ELEVENLABS_API_KEY", "")
    voices = catalog.list_voices(tts_server, elevenlabs_api_key=api_key)
    return utils.get_response(200, {"tts_server": tts_server, "voices": voices})


@router.get(
    "/settings", response_model=SettingsResponse, summary="Read the saved settings"
)
def get_settings(request: Request):
    return utils.get_response(200, {"sections": ui_settings.read_settings()})


@router.put(
    "/settings",
    response_model=SettingsUpdateResponse,
    summary="Update the saved settings",
)
def put_settings(request: Request, body: SettingsUpdateRequest = Body(...)):
    """
    Writes go through `config.save_config`, which is the blocking path -- the
    same one the CLI uses. The WebUI uses the non-blocking variant because a
    Streamlit rerun can land mid-render; an HTTP request has no such constraint
    and should not silently defer the user's save.
    """
    changed = ui_settings.apply_settings(body.sections)
    return utils.get_response(200, {"changed": changed})
