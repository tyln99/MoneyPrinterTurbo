"""
HTTP access to the task library.

`app/services/library.py` existed for a while with the Streamlit page as its
only caller, which meant projects, episodes, scenes, assets and renders were
unreachable over HTTP. Every route here is a thin adapter over that service; the
two things it adds are pagination totals and turning absolute server paths into
`/tasks/...` URLs, because the service returns host filesystem paths that must
never be handed to a browser.

The service swallows its own database errors and returns `[]`/`None`, so an
empty listing here means "nothing matched" *or* "the database is unreachable".
That is pre-existing behaviour and is deliberately not papered over at this
layer -- the fix belongs in the service.
"""

import os

from fastapi import Body, Depends, Path, Query, Request
from loguru import logger

from app.config import config
from app.controllers import base
from app.controllers.v1.base import new_router
from app.models.exception import HttpException
from app.models.schema import (
    AssetListResponse,
    EpisodeDetailResponse,
    EpisodeListResponse,
    EpisodeUpdateRequest,
    EpisodeUpdateResponse,
    ProjectCreateRequest,
    ProjectCreateResponse,
    ProjectListResponse,
    RenderListResponse,
    SceneListResponse,
    TaskLogResponse,
)
from app.services import library
from app.services import task_logs
from app.utils import task_uri, utils

router = new_router(dependencies=[Depends(base.verify_token)])


def _endpoint() -> str:
    return config.app.get("endpoint", "").rstrip("/")


def _to_uri(path: str, request_id: str) -> str:
    """Map a task-directory path to a URL, or to "" if it cannot be mapped.

    `task_file_to_uri` returns its input unchanged when the path will not
    resolve -- which includes the ordinary case of a render row whose file has
    since been deleted, because `resolve_path_within_directory` stats the file.
    Returning that input would hand the client an absolute host path, so an
    unmapped path becomes an empty URL instead.
    """
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path
    uri = task_uri.task_file_to_uri(path, _endpoint(), utils.task_dir(), request_id)
    if uri == path:
        return ""
    # `task_file_to_uri` builds the URL with `os.path.relpath`, which happily
    # produces `../..` when the resolved file sits outside the task root (a
    # symlinked storage tree does this). Such a URL is both broken and a
    # traversal attempt as far as a client is concerned.
    if ".." in uri.split("/"):
        logger.warning(f"drop task URL outside the task root, path: {path}")
        return ""
    return uri


def _public_summary(row: dict, request_id: str) -> dict:
    """Drop the host paths and expose a fetchable URL in their place."""
    summary = dict(row)
    video_file = summary.pop("video_file", "")
    summary.pop("task_path", None)
    summary["video_url"] = _to_uri(video_file, request_id)
    return summary


@router.get("/projects", response_model=ProjectListResponse, summary="List projects")
def get_projects(request: Request):
    return utils.get_response(200, {"projects": library.list_projects()})


@router.post(
    "/projects", response_model=ProjectCreateResponse, summary="Create a project"
)
def create_project(request: Request, body: ProjectCreateRequest = Body(...)):
    request_id = base.get_task_id(request)
    name = (body.name or "").strip()
    if not name:
        raise HttpException(
            task_id=request_id,
            status_code=400,
            message=f"{request_id}: project name is required",
        )
    project_id = library.create_project(name)
    if project_id is None:
        raise HttpException(
            task_id=request_id,
            status_code=500,
            message=f"{request_id}: failed to create project",
        )
    return utils.get_response(200, {"id": project_id, "name": name, "episodes": 0})


@router.get("/episodes", response_model=EpisodeListResponse, summary="List episodes")
def get_episodes(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    project_id: int | None = Query(None, ge=1),
    query: str = Query(""),
):
    request_id = base.get_task_id(request)
    rows = library.list_episodes(
        limit=limit, offset=offset, project_id=project_id, query=query
    )
    return utils.get_response(
        200,
        {
            "episodes": [_public_summary(row, request_id) for row in rows],
            "total": library.count_episodes(project_id=project_id, query=query),
            "limit": limit,
            "offset": offset,
        },
    )


def _require_episode(episode_id: str, request_id: str) -> dict:
    episode = library.get_episode(episode_id)
    if not episode:
        raise HttpException(
            task_id=episode_id,
            status_code=404,
            message=f"{request_id}: episode not found",
        )
    return episode


@router.get(
    "/episodes/{episode_id}",
    response_model=EpisodeDetailResponse,
    summary="Get one episode",
)
def get_episode(
    request: Request, episode_id: str = Path(..., description="Episode / task ID")
):
    request_id = base.get_task_id(request)
    return utils.get_response(200, _require_episode(episode_id, request_id))


@router.get(
    "/episodes/{episode_id}/scenes",
    response_model=SceneListResponse,
    summary="List an episode's scenes",
)
def get_scenes(
    request: Request, episode_id: str = Path(..., description="Episode / task ID")
):
    return utils.get_response(200, {"scenes": library.list_scenes(episode_id)})


@router.get(
    "/episodes/{episode_id}/assets",
    response_model=AssetListResponse,
    summary="List an episode's source materials",
)
def get_assets(
    request: Request, episode_id: str = Path(..., description="Episode / task ID")
):
    return utils.get_response(200, {"assets": library.list_assets(episode_id)})


@router.get(
    "/episodes/{episode_id}/renders",
    response_model=RenderListResponse,
    summary="List an episode's finished videos",
)
def get_renders(
    request: Request, episode_id: str = Path(..., description="Episode / task ID")
):
    request_id = base.get_task_id(request)
    episode = _require_episode(episode_id, request_id)

    # `episode_video_files` merges the render table with run_data["videos"],
    # because a run finished by the live pipeline records no render row. Sizes
    # only exist for the rows that came from the table.
    sizes = {row["file_name"]: row["size_bytes"] for row in library.list_renders(episode_id)}
    paths = library.episode_video_files(episode_id, episode.get("run_data"))
    renders = [
        {
            "file_name": os.path.basename(path),
            "url": _to_uri(path, request_id),
            "size_bytes": sizes.get(os.path.basename(path)),
        }
        for path in paths
    ]
    return utils.get_response(200, {"renders": renders})


@router.patch(
    "/episodes/{episode_id}",
    response_model=EpisodeUpdateResponse,
    summary="Move an episode to another project",
)
def update_episode(
    request: Request,
    episode_id: str = Path(..., description="Episode / task ID"),
    body: EpisodeUpdateRequest = Body(...),
):
    request_id = base.get_task_id(request)
    if not library.move_episode(episode_id, body.project_id):
        raise HttpException(
            task_id=episode_id,
            status_code=404,
            message=f"{request_id}: episode not found",
        )
    return utils.get_response(200)


@router.get(
    "/tasks/{task_id}/logs",
    response_model=TaskLogResponse,
    summary="Read a task's captured logs",
)
def get_task_logs(
    request: Request, task_id: str = Path(..., description="Task ID")
):
    """
    Logs are an in-memory ring buffer in the process that ran the task
    (`app/services/task_logs.py`), capped at the 20 most recent tasks. A task
    started in a different process -- the Streamlit page, say -- has no logs
    here, and neither does one whose buffer has since been evicted. Both cases
    return an empty list rather than a 404.
    """
    return utils.get_response(
        200, {"task_id": task_id, "logs": task_logs.get(task_id)}
    )
