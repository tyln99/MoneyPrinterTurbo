import ast
import copy
import threading
from abc import ABC, abstractmethod

from loguru import logger
from psycopg.types.json import Jsonb

from app.config import config
from app.models import const
from app.services import db


_PATCH_EXISTING_TASK_SCRIPT = """
if redis.call("EXISTS", KEYS[1]) == 0 then
    return 0
end

for index = 1, #ARGV, 2 do
    redis.call("HSET", KEYS[1], ARGV[index], ARGV[index + 1])
end

return 1
"""


# Base class for state management
class BaseState(ABC):
    @abstractmethod
    def update_task(self, task_id: str, state: int, progress: int = 0, **kwargs):
        pass

    @abstractmethod
    def get_task(self, task_id: str):
        pass

    @abstractmethod
    def get_all_tasks(self, page: int, page_size: int):
        pass

    @abstractmethod
    def patch_task(self, task_id: str, **kwargs) -> bool:
        """只更新已有任务的指定字段；任务不存在时返回 False。"""
        pass

    @abstractmethod
    def delete_task(self, task_id: str):
        """Remove a task. Every implementation already had this; the ABC did not."""
        pass


# Memory state management
class MemoryState(BaseState):
    def __init__(self):
        self._tasks = {}
        self._lock = threading.RLock()

    def get_all_tasks(self, page: int, page_size: int):
        start = (page - 1) * page_size
        end = start + page_size
        with self._lock:
            tasks = [copy.deepcopy(task) for task in self._tasks.values()]
            total = len(tasks)
        return tasks[start:end], total

    def update_task(
        self,
        task_id: str,
        state: int = const.TASK_STATE_PROCESSING,
        progress: int = 0,
        **kwargs,
    ):
        progress = int(progress)
        if progress > 100:
            progress = 100

        with self._lock:
            self._tasks[task_id] = {
                "task_id": task_id,
                "state": state,
                "progress": progress,
                **kwargs,
            }

    def get_task(self, task_id: str):
        with self._lock:
            task = self._tasks.get(task_id, None)
            return copy.deepcopy(task) if task is not None else None

    def patch_task(self, task_id: str, **kwargs) -> bool:
        # 异步发布只应补充发布状态，不能覆盖已经保存的视频、字幕等结果。
        # 在同一把锁内完成存在性判断和字段合并，也可避免任务删除后
        # 被后台线程重建。
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            task.update(copy.deepcopy(kwargs))
            return True

    def delete_task(self, task_id: str):
        with self._lock:
            self._tasks.pop(task_id, None)


# Redis state management
class RedisState(BaseState):
    """
    Redis-backed task state.

    Trust boundary: Redis is expected to be private to this application. Task
    values are written by MoneyPrinterTurbo and converted back from strings for
    compatibility with existing state records. Do not expose this Redis database
    to untrusted writers without replacing deserialization with a stricter
    schema-based format.
    """

    def __init__(self, host="localhost", port=6379, db=0, password=None):
        import redis

        self._redis = redis.StrictRedis(host=host, port=port, db=db, password=password)

    def get_all_tasks(self, page: int, page_size: int):
        start = (page - 1) * page_size
        end = start + page_size
        tasks = []
        cursor = 0
        total = 0
        while True:
            # Redis 数据库中除了任务 Hash，还可能存在 RedisTaskManager 使用的
            # List 队列。只扫描 Hash 可以避免对队列执行 HGETALL 时触发
            # WRONGTYPE，同时保证 total 只统计真正的任务记录。
            cursor, keys = self._redis.scan(
                cursor,
                count=page_size,
                _type="HASH",
            )
            batch_start = total
            batch_size = len(keys)
            total += batch_size

            # Redis SCAN 是分批返回 key。分页切片必须基于“当前批次起始索引”
            # 计算，而不能用累积后的 total 反推，否则第一页会切到空数组，
            # 第二页也可能只返回部分数据。
            if batch_start < end and total > start:
                slice_start = max(0, start - batch_start)
                slice_end = min(batch_size, end - batch_start)
                for key in keys[slice_start:slice_end]:
                    task_data = self._redis.hgetall(key)
                    task = {
                        k.decode("utf-8"): self._convert_to_original_type(v)
                        for k, v in task_data.items()
                    }
                    tasks.append(task)

            # 即使当前页已经取满，也要继续 SCAN 到 cursor=0，
            # 因为调用方需要准确 total 来渲染分页信息。
            if cursor == 0:
                break
        return tasks, total

    def update_task(
        self,
        task_id: str,
        state: int = const.TASK_STATE_PROCESSING,
        progress: int = 0,
        **kwargs,
    ):
        progress = int(progress)
        if progress > 100:
            progress = 100

        fields = {
            "task_id": task_id,
            "state": state,
            "progress": progress,
            **kwargs,
        }

        for field, value in fields.items():
            self._redis.hset(task_id, field, str(value))

    def get_task(self, task_id: str):
        task_data = self._redis.hgetall(task_id)
        if not task_data:
            return None

        task = {
            key.decode("utf-8"): self._convert_to_original_type(value)
            for key, value in task_data.items()
        }
        return task

    def patch_task(self, task_id: str, **kwargs) -> bool:
        if not kwargs:
            return False

        arguments = []
        for field, value in kwargs.items():
            arguments.extend((field, str(value)))

        # EXISTS 和 HSET 如果分成两条命令，后台发布线程与删除请求并发时，
        # HSET 可能在删除后重新创建一条残缺任务。Lua 脚本由 Redis 原子执行，
        # 可以保证任务不存在时不写入，且不会改变现有字段之外的数据。
        updated = self._redis.eval(
            _PATCH_EXISTING_TASK_SCRIPT,
            1,
            task_id,
            *arguments,
        )
        return bool(updated)

    def delete_task(self, task_id: str):
        self._redis.delete(task_id)

    @staticmethod
    def _convert_to_original_type(value):
        """
        Convert values written by this application back to common Python types.

        This compatibility parser assumes Redis is inside the application's
        trust boundary. If Redis can be written by untrusted clients, task state
        should move to a strict JSON/schema parser instead of open-ended literal
        conversion.
        """
        value_str = value.decode("utf-8")

        try:
            # try to convert byte string array to list
            return ast.literal_eval(value_str)
        except (ValueError, SyntaxError):
            pass

        if value_str.isdigit():
            return int(value_str)
        # Add more conversions here if needed
        return value_str



# PostgreSQL state management
class PostgresState(BaseState):
    """
    Durable task state.

    This is the production implementation. `MemoryState` above is kept for the
    test suite, which instantiates it directly or patches the module singleton;
    it is not a runtime fallback.

    Nothing connects here. The pool in `app.services.db` opens on first query, so
    constructing this at import time is safe even when the database is down.

    Write failures are logged and swallowed rather than raised. A task record is
    worth less than the render it is describing: a blip while writing a progress
    tick must not abort a generation the user has already paid for. Reads return
    the same empty answers the other implementations give for a missing task.
    """

    # Mirrored out of the task record into their own columns so the library can
    # index and full-text search them. Everything else stays in run_data.
    _COLUMNS = {"video_subject": "topic", "script": "script"}

    def get_all_tasks(self, page: int, page_size: int):
        offset = max(page - 1, 0) * page_size
        try:
            with db.connection() as conn:
                total = conn.execute("SELECT count(*) AS n FROM episode").fetchone()["n"]
                rows = conn.execute(
                    "SELECT id, state, progress, run_data FROM episode"
                    " ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                    (page_size, offset),
                ).fetchall()
        except Exception as e:
            logger.error(f"failed to list tasks: {type(e).__name__}: {e}")
            return [], 0
        return [self._to_task(row) for row in rows], total

    def update_task(
        self,
        task_id: str,
        state: int = const.TASK_STATE_PROCESSING,
        progress: int = 0,
        **kwargs,
    ):
        progress = min(max(int(progress), 0), 100)
        title = str(kwargs.get("video_subject") or "")[:200]
        columns = {
            column: str(kwargs.get(key) or "")
            for key, column in self._COLUMNS.items()
        }
        try:
            with db.connection() as conn:
                # Replaces the whole record, matching MemoryState, which assigns
                # a fresh dict rather than merging.
                conn.execute(
                    "INSERT INTO episode (id, state, progress, run_data, title, topic, script)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (id) DO UPDATE SET"
                    "   state = EXCLUDED.state,"
                    "   progress = EXCLUDED.progress,"
                    "   run_data = EXCLUDED.run_data,"
                    "   title = coalesce(nullif(EXCLUDED.title, \'\'), episode.title),"
                    "   topic = coalesce(nullif(EXCLUDED.topic, \'\'), episode.topic),"
                    "   script = coalesce(nullif(EXCLUDED.script, \'\'), episode.script),"
                    "   updated_at = now()",
                    (
                        task_id,
                        state,
                        progress,
                        Jsonb(kwargs),
                        title,
                        columns["topic"],
                        columns["script"],
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"failed to update task {task_id}: {type(e).__name__}: {e}")

    def get_task(self, task_id: str):
        try:
            with db.connection() as conn:
                row = conn.execute(
                    "SELECT id, state, progress, run_data FROM episode WHERE id = %s",
                    (task_id,),
                ).fetchone()
        except Exception as e:
            logger.error(f"failed to read task {task_id}: {type(e).__name__}: {e}")
            return None
        return self._to_task(row) if row else None

    def patch_task(self, task_id: str, **kwargs) -> bool:
        if not kwargs:
            return False
        try:
            with db.connection() as conn:
                # jsonb || jsonb merges in one statement, so unlike the
                # read-modify-write this replaces there is no lost-update window
                # between two processes.
                result = conn.execute(
                    "UPDATE episode SET run_data = run_data || %s, updated_at = now()"
                    " WHERE id = %s",
                    (Jsonb(kwargs), task_id),
                )
                conn.commit()
                return result.rowcount > 0
        except Exception as e:
            logger.error(f"failed to patch task {task_id}: {type(e).__name__}: {e}")
            return False

    def delete_task(self, task_id: str):
        try:
            with db.connection() as conn:
                conn.execute("DELETE FROM episode WHERE id = %s", (task_id,))
                conn.commit()
        except Exception as e:
            logger.error(f"failed to delete task {task_id}: {type(e).__name__}: {e}")

    @staticmethod
    def _to_task(row) -> dict:
        """Rebuild the flat record shape the other implementations return."""
        task = dict(row.get("run_data") or {})
        task["task_id"] = row["id"]
        task["state"] = row["state"]
        task["progress"] = row["progress"]
        return task


# Global state
_enable_redis = config.app.get("enable_redis", False)
_redis_host = config.app.get("redis_host", "localhost")
_redis_port = config.app.get("redis_port", 6379)
_redis_db = config.app.get("redis_db", 0)
_redis_password = config.app.get("redis_password", None)

# PostgreSQL is required, so this is unconditional. MemoryState and RedisState
# stay for the test suite and for anyone importing them directly.
state = PostgresState()
