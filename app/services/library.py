"""
The task library: reads over projects, episodes, scenes, assets and renders,
plus a one-time importer for task directories written before the database
existed.

Media stays on disk. Rows record file names and the episode id, from which the
directory is derivable, so the storage tree can be moved or remounted without
invalidating anything.

Run the importer with:

    uv run python -m app.services.library
"""

from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any

from loguru import logger
from psycopg.types.json import Jsonb

from app.models import const
from app.services import db, subtitle
from app.utils import utils

UNSORTED_PROJECT_ID = 1

_FINAL_VIDEO_RE = re.compile(r"^final-(\d+)\.(mp4|mov|mkv|webm)$", re.IGNORECASE)
_SRT_RANGE_RE = re.compile(
    r"(\d+):(\d+):(\d+),(\d+)\s*-->\s*(\d+):(\d+):(\d+),(\d+)"
)


def _tasks_root() -> str:
    """The task directory, without creating it.

    `utils.task_dir()` with no argument is safe; passing a task id to it would
    `os.makedirs` that directory as a side effect, which a read must never do.
    """
    return utils.task_dir()


def episode_dir(episode_id: str) -> str:
    return os.path.join(_tasks_root(), episode_id)


def _srt_range_to_ms(line: str) -> tuple[int, int] | None:
    """Turn `00:00:01,650 --> 00:00:04,475` into milliseconds."""
    match = _SRT_RANGE_RE.search(line or "")
    if not match:
        return None
    values = [int(part) for part in match.groups()]
    start = (values[0] * 3600 + values[1] * 60 + values[2]) * 1000 + values[3]
    end = (values[4] * 3600 + values[5] * 60 + values[6]) * 1000 + values[7]
    if end <= start:
        return None
    return start, end


def episode_video_files(episode_id: str, run_data: dict | None = None) -> list[str]:
    """
    Absolute paths to an episode's finished videos.

    Two sources, because they are populated by different things: the `render`
    table is filled by the legacy importer, while a run completed by the live
    pipeline records its outputs in `run_data["videos"]` and writes no render
    row. Reading only one of them left freshly finished tasks with their Play
    button greyed out.

    Only the file name is taken from run_data: those paths are absolute and were
    written by whichever process ran the pipeline, so a container path would not
    resolve on the host.
    """
    directory = episode_dir(episode_id)
    names: list[str] = [row["file_name"] for row in list_renders(episode_id)]
    if not names:
        videos = (run_data or {}).get("videos") or []
        names = [os.path.basename(str(video)) for video in videos if video]
    return [os.path.join(directory, name) for name in names if name]


def _row_to_summary(row: dict) -> dict:
    """
    Shape a row the way the WebUI task panel already expects, so switching it
    over from the directory scan is a source change rather than a data change.
    """
    episode_id = row["id"]
    run_data = row.get("run_data") or {}
    file_name = row.get("render_file")
    task_path = episode_dir(episode_id)
    if not file_name:
        videos = run_data.get("videos") or []
        file_name = os.path.basename(str(videos[0])) if videos else ""
    return {
        "task_id": episode_id,
        "subject": row.get("title") or row.get("topic") or episode_id,
        "state": row.get("state"),
        "progress": row.get("progress") or 0,
        "mtime": row["updated_at"].timestamp(),
        "task_path": task_path,
        "video_file": os.path.join(task_path, file_name) if file_name else "",
        "has_restore_data": bool(row.get("has_params")),
        "cross_post_state": run_data.get("cross_post_state"),
        "source": "library",
    }


def _episode_filter(project_id: int | None, query: str) -> tuple[str, list[Any]]:
    """The WHERE clause shared by `list_episodes` and `count_episodes`.

    Kept in one place so a paginated listing can never be filtered differently
    from the total it is displayed next to.
    """
    where = []
    params: list[Any] = []
    if project_id:
        where.append("e.project_id = %s")
        params.append(project_id)
    if query.strip():
        where.append("e.search_doc @@ websearch_to_tsquery('simple', %s)")
        params.append(query.strip())
    return (f"WHERE {' AND '.join(where)}" if where else ""), params


def count_episodes(project_id: int | None = None, query: str = "") -> int:
    """How many episodes match, for paginating clients that need a page count."""
    clause, params = _episode_filter(project_id, query)
    try:
        with db.connection() as conn:
            row = conn.execute(
                f"SELECT count(*) AS n FROM episode e {clause}", tuple(params)
            ).fetchone()
    except Exception as e:
        logger.error(f"failed to count episodes: {type(e).__name__}: {e}")
        return 0
    return int(row["n"]) if row else 0


def list_episodes(
    limit: int = 50,
    offset: int = 0,
    project_id: int | None = None,
    query: str = "",
) -> list[dict]:
    """Most recently touched first, optionally filtered by project and search."""
    clause, params = _episode_filter(project_id, query)

    sql = f"""
        SELECT e.id, e.title, e.topic, e.state, e.progress, e.run_data, e.updated_at,
               e.params <> '{{}}'::jsonb AS has_params,
               (SELECT r.file_name FROM render r
                 WHERE r.episode_id = e.id
                 ORDER BY r.video_index LIMIT 1) AS render_file
        FROM episode e
        {clause}
        ORDER BY e.updated_at DESC
        LIMIT %s OFFSET %s
    """
    try:
        with db.connection() as conn:
            rows = conn.execute(sql, (*params, limit, offset)).fetchall()
    except Exception as e:
        logger.error(f"failed to list episodes: {type(e).__name__}: {e}")
        return []
    return [_row_to_summary(row) for row in rows]


def get_episode(episode_id: str) -> dict | None:
    try:
        with db.connection() as conn:
            row = conn.execute(
                "SELECT id, project_id, title, topic, script, params, run_data,"
                " state, progress, created_at, updated_at"
                " FROM episode WHERE id = %s",
                (episode_id,),
            ).fetchone()
    except Exception as e:
        logger.error(f"failed to read episode {episode_id}: {type(e).__name__}: {e}")
        return None
    return dict(row) if row else None


def list_scenes(episode_id: str) -> list[dict]:
    try:
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT idx, narration, search_term, start_ms, end_ms"
                " FROM scene WHERE episode_id = %s ORDER BY idx",
                (episode_id,),
            ).fetchall()
    except Exception as e:
        logger.error(f"failed to read scenes for {episode_id}: {type(e).__name__}: {e}")
        return []
    return [dict(row) for row in rows]


def list_projects() -> list[dict]:
    try:
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT p.id, p.name,"
                " (SELECT count(*) FROM episode e WHERE e.project_id = p.id) AS episodes"
                " FROM project p ORDER BY p.name"
            ).fetchall()
    except Exception as e:
        logger.error(f"failed to list projects: {type(e).__name__}: {e}")
        return []
    return [dict(row) for row in rows]


def list_assets(episode_id: str) -> list[dict]:
    try:
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT kind, file_name, provider, search_term, duration_s,"
                " width, height"
                " FROM asset WHERE episode_id = %s ORDER BY id",
                (episode_id,),
            ).fetchall()
    except Exception as e:
        logger.error(f"failed to read assets for {episode_id}: {type(e).__name__}: {e}")
        return []
    return [dict(row) for row in rows]


def list_renders(episode_id: str) -> list[dict]:
    try:
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT video_index, file_name, size_bytes, created_at"
                " FROM render WHERE episode_id = %s ORDER BY video_index",
                (episode_id,),
            ).fetchall()
    except Exception as e:
        logger.error(f"failed to read renders for {episode_id}: {type(e).__name__}: {e}")
        return []
    return [dict(row) for row in rows]


def create_project(name: str) -> int | None:
    """Create a project, or return the id of the one already using that name."""
    clean = (name or "").strip()
    if not clean:
        return None
    try:
        with db.connection() as conn:
            row = conn.execute(
                "INSERT INTO project (name) VALUES (%s)"
                " ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name"
                " RETURNING id",
                (clean,),
            ).fetchone()
            conn.commit()
            return row["id"] if row else None
    except Exception as e:
        logger.error(f"failed to create project {clean!r}: {type(e).__name__}: {e}")
        return None


def move_episode(episode_id: str, project_id: int) -> bool:
    try:
        with db.connection() as conn:
            result = conn.execute(
                "UPDATE episode SET project_id = %s, updated_at = now() WHERE id = %s",
                (project_id, episode_id),
            )
            conn.commit()
            return result.rowcount > 0
    except Exception as e:
        logger.error(f"failed to move episode {episode_id}: {type(e).__name__}: {e}")
        return False


def _is_importable(name: str, path: str) -> bool:
    """
    A real task, not a test fixture.

    Measured against the current storage tree: 43 of 70 directories parse as
    UUIDs and exactly 28 of those carry a script.json, which is precisely the
    set of genuine tasks. Every fixture (`test-wavespeed`, `sonilo-task`, …)
    fails the UUID check.
    """
    try:
        uuid.UUID(name)
    except ValueError:
        return False
    return os.path.isfile(os.path.join(path, "script.json"))


def _import_one(conn, name: str, path: str) -> None:
    with open(os.path.join(path, "script.json"), "r", encoding="utf-8") as handle:
        script_data = json.load(handle)
    if not isinstance(script_data, dict):
        raise ValueError("script.json is not a JSON object")

    params = script_data.get("params")
    params = params if isinstance(params, dict) else {}
    script = str(script_data.get("script") or "")
    subject = str(params.get("video_subject") or "").strip() or script[:40]

    finals = sorted(
        (entry for entry in os.listdir(path) if _FINAL_VIDEO_RE.match(entry)),
        key=lambda entry: int(_FINAL_VIDEO_RE.match(entry).group(1)),
    )
    # NULL, not 0: the WebUI buckets a stateless task as "history", and 0 would
    # silently re-bucket every imported episode.
    state = const.TASK_STATE_COMPLETE if finals else None
    mtime = os.stat(path).st_mtime

    conn.execute(
        "INSERT INTO episode"
        " (id, project_id, title, topic, script, params, state, progress,"
        "  created_at, updated_at)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, to_timestamp(%s), to_timestamp(%s))"
        " ON CONFLICT (id) DO NOTHING",
        (
            name,
            UNSORTED_PROJECT_ID,
            subject[:200],
            subject[:200],
            script,
            Jsonb(params),
            state,
            100 if finals else 0,
            mtime,
            mtime,
        ),
    )

    for record in script_data.get("material_sources") or []:
        if not isinstance(record, dict) or not record.get("local_file"):
            continue
        rendition = record.get("rendition") or {}
        conn.execute(
            "INSERT INTO asset"
            " (episode_id, kind, file_name, provider, search_term, duration_s,"
            "  width, height, source_info)"
            " VALUES (%s, 'material', %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (episode_id, kind, file_name) DO NOTHING",
            (
                name,
                str(record.get("local_file")),
                str(record.get("provider") or ""),
                str(record.get("search_term") or ""),
                record.get("duration"),
                rendition.get("width"),
                rendition.get("height"),
                Jsonb(record),
            ),
        )

    for file_name in finals:
        full = os.path.join(path, file_name)
        conn.execute(
            "INSERT INTO render (episode_id, video_index, file_name, size_bytes)"
            " VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (episode_id, file_name) DO NOTHING",
            (
                name,
                int(_FINAL_VIDEO_RE.match(file_name).group(1)),
                file_name,
                os.path.getsize(full) if os.path.isfile(full) else None,
            ),
        )

    # The narration/visual alignment the pipeline already computed and threw
    # away after burning captions.
    for index, times, text in subtitle.file_to_subtitles(
        os.path.join(path, "subtitle.srt")
    ):
        span = _srt_range_to_ms(times)
        conn.execute(
            "INSERT INTO scene (episode_id, idx, narration, start_ms, end_ms)"
            " VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT (episode_id, idx) DO NOTHING",
            (
                name,
                index - 1,
                text.strip(),
                span[0] if span else None,
                span[1] if span else None,
            ),
        )


def import_legacy_tasks(tasks_root: str | None = None, dry_run: bool = False) -> dict:
    """
    Import task directories into the library. Safe to run repeatedly.

    Never writes, moves or deletes anything under storage/. Each directory is
    imported in its own transaction, so one corrupt script.json costs that
    directory and nothing else.
    """
    root = tasks_root or _tasks_root()
    counts = {"imported": 0, "skipped_fixture": 0, "failed": 0}
    if not os.path.isdir(root):
        return counts

    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        if not _is_importable(name, path):
            counts["skipped_fixture"] += 1
            continue
        if dry_run:
            counts["imported"] += 1
            continue
        try:
            with db.connection() as conn:
                _import_one(conn, name, path)
                conn.commit()
            counts["imported"] += 1
        except Exception as e:
            counts["failed"] += 1
            logger.exception(f"failed to import task {name}: {type(e).__name__}: {e}")

    return counts


if __name__ == "__main__":
    import sys

    result = import_legacy_tasks(dry_run="--dry-run" in sys.argv)
    logger.info(
        "library import finished: "
        f"imported={result['imported']} "
        f"skipped_fixture={result['skipped_fixture']} "
        f"failed={result['failed']}"
    )
