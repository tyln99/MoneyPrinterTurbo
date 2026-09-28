"""
Per-task log capture, shared by every entry point.

This used to live in `webui_task.py`, which meant only runs submitted from the
Streamlit page had logs: `task.start()` never installed the sink, so a run
started over the REST API or the CLI left the buffer empty and the API had
nothing to serve. The buffer now belongs to the pipeline, and the WebUI reads it
through the same functions it always did.

Loguru sinks are process-global, so the sink filters on the worker thread. Two
tasks running concurrently would otherwise interleave into both buffers.
"""

from __future__ import annotations

import threading
from collections import deque
from contextlib import contextmanager

from loguru import logger

from app.utils.logging_utils import format_log_record

# 只保留最近任务的日志，避免服务长时间运行后持续占用内存。dict 保持插入顺序；
# 任务日志仅用于界面诊断，淘汰最早的记录不影响任务本身。
_MAX_LOG_TASKS = 20
_MAX_LOG_RECORDS_PER_TASK = 1000

_task_logs: dict[str, deque[str]] = {}
_task_logs_lock = threading.RLock()

# Streamlit 无法由后台线程直接推送组件更新，只能通过 Fragment 轮询。0.5 秒
# 足以让日志接近终端实时输出，又不会像高频刷新那样持续占用浏览器资源。
TASK_LOG_REFRESH_INTERVAL_SECONDS = 0.5

# Which task this thread is already capturing, so the capture `task.start()`
# opens does not install a second sink on top of the one the WebUI worker
# opened -- and so a caller that asked for no capture at all still gets none.
_active = threading.local()


def append(task_id: str, message: str) -> None:
    """按任务保存有限数量的日志，供界面安全轮询。"""
    with _task_logs_lock:
        records = _task_logs.get(task_id)
        if records is None:
            if len(_task_logs) >= _MAX_LOG_TASKS:
                oldest_task_id = next(iter(_task_logs))
                _task_logs.pop(oldest_task_id, None)
            records = deque(maxlen=_MAX_LOG_RECORDS_PER_TASK)
            _task_logs[task_id] = records
        records.append(message.rstrip())


def get(task_id: str) -> list[str]:
    """返回日志快照，避免读取期间持有后台线程使用的锁。"""
    with _task_logs_lock:
        return list(_task_logs.get(task_id, ()))


def clear(task_id: str) -> None:
    with _task_logs_lock:
        _task_logs.pop(task_id, None)


@contextmanager
def capture(task_id: str, enabled: bool = True):
    """
    Route this thread's log records into `task_id`'s buffer.

    Re-entrant: the outermost caller decides. `webui_task` opens a capture
    around the whole worker and `task.start()` opens one around the pipeline, so
    without this guard a WebUI run would install two sinks and record every line
    twice -- and `capture_logs=False` would be silently overridden by the inner
    one.
    """
    if getattr(_active, "task_id", None) is not None:
        yield
        return

    _active.task_id = task_id
    handler_id = None
    worker_thread_id = threading.get_ident()
    try:
        if enabled:
            handler_id = logger.add(
                lambda message: append(task_id, str(message)),
                level="DEBUG",
                format=format_log_record,
                colorize=False,
                filter=lambda record: record["thread"].id == worker_thread_id,
            )
        yield
    finally:
        _active.task_id = None
        if handler_id is not None:
            try:
                logger.remove(handler_id)
            except ValueError:
                logger.debug(f"task log handler already removed: task_id={task_id}")
