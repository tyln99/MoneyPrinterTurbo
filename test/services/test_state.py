import os
import sys
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.models import const
from unittest.mock import patch

from app.config import config
from app.services import db
from app.services.state import MemoryState, PostgresState, RedisState


class _FakeRedis:
    def __init__(self, batches):
        self.batches = batches
        self.scan_types = []
        self.data = {}
        for key in [key for batch in batches for key in batch]:
            index = int(key.decode("utf-8").split(":")[-1])
            self.data[key] = {
                b"task_id": key,
                b"state": b"1",
                b"progress": str(index).encode("utf-8"),
            }

    def scan(self, cursor, count, _type=None):
        self.scan_types.append(_type)
        batch_index = int(cursor)
        next_cursor = batch_index + 1
        if next_cursor >= len(self.batches):
            next_cursor = 0
        return next_cursor, self.batches[batch_index]

    def hgetall(self, key):
        if isinstance(key, str):
            key = key.encode("utf-8")
        return self.data[key]

    def exists(self, key):
        if isinstance(key, str):
            key = key.encode("utf-8")
        return key in self.data

    def hset(self, key, field=None, value=None, mapping=None):
        if isinstance(key, str):
            key = key.encode("utf-8")
        target = self.data.setdefault(key, {})
        if mapping:
            target.update(
                {
                    str(item_key).encode("utf-8"): str(item_value).encode("utf-8")
                    for item_key, item_value in mapping.items()
                }
            )
        elif field is not None:
            target[str(field).encode("utf-8")] = str(value).encode("utf-8")

    def eval(self, script, numkeys, key, *arguments):
        if isinstance(key, str):
            key = key.encode("utf-8")
        if key not in self.data:
            return 0

        target = self.data[key]
        for index in range(0, len(arguments), 2):
            field = str(arguments[index]).encode("utf-8")
            value = str(arguments[index + 1]).encode("utf-8")
            target[field] = value
        return 1


class TestMemoryState(unittest.TestCase):
    def test_get_task_and_get_all_tasks_return_isolated_snapshots(self):
        state = MemoryState()
        state.update_task(
            "task-1",
            state=const.TASK_STATE_PROCESSING,
            progress=25,
            videos=["first.mp4"],
        )

        task = state.get_task("task-1")
        task["videos"].append("mutated.mp4")

        tasks, total = state.get_all_tasks(page=1, page_size=10)
        tasks[0]["videos"].append("mutated-again.mp4")

        self.assertEqual(total, 1)
        self.assertEqual(state.get_task("task-1")["videos"], ["first.mp4"])

    def test_concurrent_memory_updates_are_preserved(self):
        state = MemoryState()
        thread_count = 5
        tasks_per_thread = 50

        def update_tasks(thread_index):
            for task_index in range(tasks_per_thread):
                state.update_task(
                    f"task-{thread_index}-{task_index}",
                    state=const.TASK_STATE_PROCESSING,
                    progress=task_index,
                )

        threads = [
            threading.Thread(target=update_tasks, args=(thread_index,))
            for thread_index in range(thread_count)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        tasks, total = state.get_all_tasks(page=1, page_size=thread_count * tasks_per_thread)

        self.assertEqual(total, thread_count * tasks_per_thread)
        self.assertEqual(len(tasks), total)

    def test_patch_task_preserves_generated_outputs(self):
        """异步发布更新不能覆盖已经完成的视频任务字段。"""
        state = MemoryState()
        state.update_task(
            "task-1",
            state=const.TASK_STATE_COMPLETE,
            progress=100,
            videos=["final.mp4"],
        )

        patched = state.patch_task(
            "task-1",
            cross_post_state=const.CROSS_POST_STATE_COMPLETE,
            cross_post_results=[{"success": True}],
        )

        self.assertTrue(patched)
        self.assertEqual(
            state.get_task("task-1"),
            {
                "task_id": "task-1",
                "state": const.TASK_STATE_COMPLETE,
                "progress": 100,
                "videos": ["final.mp4"],
                "cross_post_state": const.CROSS_POST_STATE_COMPLETE,
                "cross_post_results": [{"success": True}],
            },
        )
        self.assertFalse(state.patch_task("missing", value="ignored"))


class TestRedisState(unittest.TestCase):
    def _build_state(self, batch_sizes):
        keys = [f"task:{i}".encode("utf-8") for i in range(sum(batch_sizes))]
        batches = []
        offset = 0
        for batch_size in batch_sizes:
            batches.append(keys[offset : offset + batch_size])
            offset += batch_size

        state = RedisState.__new__(RedisState)
        state._redis = _FakeRedis(batches)
        return state

    def test_get_all_tasks_paginates_across_scan_batches(self):
        """
        Redis SCAN 分批返回 key 时，分页切片必须按当前批次起始位置计算。

        这个用例复现 PR #890 描述的 18 条任务、page_size=10 场景：
        第一批 10 条，第二批 8 条。旧逻辑第一页会返回空列表，第二页
        只返回 2 条；修复后第一页返回 10 条，第二页返回剩余 8 条。
        """
        state = self._build_state([10, 8])

        first_page, first_total = state.get_all_tasks(page=1, page_size=10)
        second_page, second_total = state.get_all_tasks(page=2, page_size=10)

        self.assertEqual(first_total, 18)
        self.assertEqual(second_total, 18)
        self.assertEqual(len(first_page), 10)
        self.assertEqual(len(second_page), 8)
        self.assertEqual(
            [task["task_id"] for task in first_page],
            [f"task:{i}" for i in range(10)],
        )
        self.assertEqual(
            [task["task_id"] for task in second_page],
            [f"task:{i}" for i in range(10, 18)],
        )
        self.assertTrue(state._redis.scan_types)
        self.assertEqual(set(state._redis.scan_types), {"HASH"})

    @unittest.skipUnless(
        os.getenv("MPT_TEST_REDIS_HOST"),
        "MPT_TEST_REDIS_HOST not set",
    )
    def test_real_redis_get_all_tasks_ignores_queue_keys(self):
        """真实 Redis 中的 List 队列不能被任务列表误当作 Hash 读取。"""
        state = RedisState(
            host=os.environ["MPT_TEST_REDIS_HOST"],
            port=int(os.getenv("MPT_TEST_REDIS_PORT", "6379")),
            db=int(os.getenv("MPT_TEST_REDIS_DB", "15")),
        )
        suffix = uuid.uuid4()
        task_ids = [f"ci-list-{suffix}-{index}" for index in range(3)]
        queue_key = f"ci-queue-{suffix}"

        try:
            for task_id in task_ids:
                state.update_task(
                    task_id,
                    state=const.TASK_STATE_COMPLETE,
                    progress=100,
                )
            state._redis.rpush(queue_key, *task_ids)

            tasks, _ = state.get_all_tasks(page=1, page_size=1000)
            returned_ids = {task["task_id"] for task in tasks}

            self.assertTrue(set(task_ids).issubset(returned_ids))
            self.assertNotIn(queue_key, returned_ids)
        finally:
            state._redis.delete(queue_key, *task_ids)

    def test_patch_task_updates_only_existing_redis_task(self):
        state = self._build_state([1])

        self.assertTrue(
            state.patch_task(
                "task:0",
                cross_post_state=const.CROSS_POST_STATE_FAILED,
                cross_post_error="upload failed",
            )
        )
        task = state.get_task("task:0")
        self.assertEqual(task["progress"], 0)
        self.assertEqual(task["cross_post_state"], const.CROSS_POST_STATE_FAILED)
        self.assertEqual(task["cross_post_error"], "upload failed")
        self.assertFalse(state.patch_task("missing", value="ignored"))

    @unittest.skipUnless(
        os.getenv("MPT_TEST_REDIS_HOST"),
        "MPT_TEST_REDIS_HOST not set",
    )
    def test_real_redis_patch_and_delete_are_atomic(self):
        """真实 Redis 中并发删除和局部更新不能重新创建残缺任务。"""
        state = RedisState(
            host=os.environ["MPT_TEST_REDIS_HOST"],
            port=int(os.getenv("MPT_TEST_REDIS_PORT", "6379")),
            db=int(os.getenv("MPT_TEST_REDIS_DB", "15")),
        )

        for _ in range(50):
            task_id = f"ci-atomic-{uuid.uuid4()}"
            state.update_task(
                task_id,
                state=const.TASK_STATE_COMPLETE,
                progress=100,
            )
            barrier = threading.Barrier(2)

            def patch_task():
                barrier.wait()
                state.patch_task(
                    task_id,
                    cross_post_state=const.CROSS_POST_STATE_COMPLETE,
                )

            def delete_task():
                barrier.wait()
                state.delete_task(task_id)

            # Future.result() 会把工作线程异常重新抛到测试线程，避免 Redis
            # 命令实际失败但仅打印线程异常、最终仍被误判为测试通过。
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(patch_task),
                    executor.submit(delete_task),
                ]
                for future in futures:
                    future.result(timeout=5)

            self.assertIsNone(state.get_task(task_id))


TEST_DSN = os.environ.get("MPT_TEST_DATABASE_URL", "")

from test.services.test_db import isolated_dsn, reset_test_schema  # noqa: E402


@unittest.skipUnless(TEST_DSN, "MPT_TEST_DATABASE_URL not set")
class TestPostgresState(unittest.TestCase):
    """
    PostgresState is the production implementation, so it has to honour the same
    contract MemoryState does - including the parts that are easy to get wrong,
    like update_task replacing rather than merging.
    """

    def setUp(self):
        reset_test_schema()
        self._patcher = patch.object(
            config, "app", dict(config.app, database_url=isolated_dsn())
        )
        self._patcher.start()
        db.reset_pool()
        db.ensure_migrated()
        self.state = PostgresState()
        self.task_id = f"pgstate-{uuid.uuid4().hex[:8]}"

    def tearDown(self):
        self.state.delete_task(self.task_id)
        db.reset_pool()
        self._patcher.stop()

    def test_round_trips_the_task_record(self):
        self.state.update_task(
            self.task_id,
            state=const.TASK_STATE_PROCESSING,
            progress=40,
            videos=["final-1.mp4"],
            audio_duration=12.5,
        )
        task = self.state.get_task(self.task_id)
        self.assertEqual(task["task_id"], self.task_id)
        self.assertEqual(task["state"], const.TASK_STATE_PROCESSING)
        self.assertEqual(task["progress"], 40)
        self.assertEqual(task["videos"], ["final-1.mp4"])
        self.assertEqual(task["audio_duration"], 12.5)

    def test_update_replaces_rather_than_merges(self):
        """MemoryState assigns a fresh dict; this must not quietly differ."""
        self.state.update_task(self.task_id, progress=10, videos=["a.mp4"])
        self.state.update_task(self.task_id, progress=20)
        task = self.state.get_task(self.task_id)
        self.assertEqual(task["progress"], 20)
        self.assertNotIn("videos", task)

    def test_patch_merges_into_an_existing_task(self):
        self.state.update_task(self.task_id, progress=10, videos=["a.mp4"])
        self.assertTrue(self.state.patch_task(self.task_id, cross_post_state="pending"))
        task = self.state.get_task(self.task_id)
        self.assertEqual(task["videos"], ["a.mp4"])
        self.assertEqual(task["cross_post_state"], "pending")

    def test_patch_refuses_to_create_a_missing_task(self):
        """Async publishing must not resurrect a task the user just deleted."""
        self.assertFalse(self.state.patch_task("pgstate-does-not-exist", x=1))
        self.assertIsNone(self.state.get_task("pgstate-does-not-exist"))

    def test_patch_without_fields_is_a_no_op(self):
        self.state.update_task(self.task_id, progress=10)
        self.assertFalse(self.state.patch_task(self.task_id))

    def test_progress_is_clamped(self):
        self.state.update_task(self.task_id, progress=150)
        self.assertEqual(self.state.get_task(self.task_id)["progress"], 100)

    def test_missing_task_reads_as_none(self):
        self.assertIsNone(self.state.get_task("pgstate-never-written"))

    def test_delete_removes_the_task(self):
        self.state.update_task(self.task_id, progress=10)
        self.state.delete_task(self.task_id)
        self.assertIsNone(self.state.get_task(self.task_id))

    def test_get_all_tasks_paginates_and_reports_a_total(self):
        self.state.update_task(self.task_id, progress=10)
        tasks, total = self.state.get_all_tasks(1, 1)
        self.assertEqual(len(tasks), 1)
        self.assertGreaterEqual(total, 1)

    def test_searchable_fields_are_mirrored_into_columns(self):
        """
        title/topic/script back the full-text index, so they must leave run_data
        and land in real columns.
        """
        self.state.update_task(
            self.task_id, video_subject="Walking every day", script="Walking is good."
        )
        with db.connection() as conn:
            row = conn.execute(
                "SELECT title, topic, script FROM episode WHERE id = %s",
                (self.task_id,),
            ).fetchone()
        self.assertEqual(row["topic"], "Walking every day")
        self.assertEqual(row["script"], "Walking is good.")

    def test_a_write_failure_does_not_abort_the_caller(self):
        """
        update_task is called from inside the render loop. A database blip must
        not kill a generation the user has already paid for.
        """
        with patch.object(db, "connection", side_effect=RuntimeError("db down")):
            self.state.update_task(self.task_id, progress=50)      # must not raise
            self.assertIsNone(self.state.get_task(self.task_id))
            self.assertFalse(self.state.patch_task(self.task_id, x=1))
            self.assertEqual(self.state.get_all_tasks(1, 10), ([], 0))
            self.state.delete_task(self.task_id)                   # must not raise


if __name__ == "__main__":
    unittest.main()
