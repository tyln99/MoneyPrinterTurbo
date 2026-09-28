"""
Turn an absolute task-output path into a URL a browser can fetch.

Lifted out of `app/controllers/v1/video.py` unchanged so the library endpoints
use one implementation rather than a second copy. The library service returns
absolute host paths (`library._row_to_summary` emits `task_path` and
`video_file`), and those must never reach a client.

The result points at the `/tasks` StaticFiles mount in `app/asgi.py`, which
supports HTTP range requests -- unlike the hand-rolled `/api/v1/stream`.
"""

from __future__ import annotations

import os

from loguru import logger

from app.utils import file_security


def task_file_to_uri(file: str, endpoint: str, task_dir: str, request_id: str) -> str:
    if not isinstance(file, str):
        return file

    if file.startswith(("http://", "https://")):
        return file

    try:
        resolved_path = file_security.resolve_path_within_directory(task_dir, file)
    except ValueError as exc:
        # 任务状态理论上只应保存任务目录内的产物路径。这里不再继续拼接 URL，
        # 避免把异常路径包装成可访问链接；同时保留原值，便于排查历史脏数据。
        logger.warning(
            f"skip unsafe task output path, request_id: {request_id}, path: {file}, "
            f"error: {str(exc)}"
        )
        return file

    relative_path = os.path.relpath(resolved_path, task_dir).replace("\\", "/")
    uri_path = f"tasks/{relative_path}"
    if endpoint:
        return f"{endpoint.rstrip('/')}/{uri_path}"
    return f"/{uri_path}"
