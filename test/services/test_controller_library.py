import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app import asgi  # noqa: E402
from app.config import config  # noqa: E402
from app.controllers.v1 import library as library_controller  # noqa: E402
from app.services import db, task_logs  # noqa: E402

from test.services.test_db import isolated_dsn, reset_test_schema  # noqa: E402

TEST_DSN = os.environ.get("MPT_TEST_DATABASE_URL", "")


class TestPathsNeverReachTheClient(unittest.TestCase):
    """Runs without a database."""

    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.tasks = os.path.join(os.path.realpath(self.root.name), "tasks")
        os.makedirs(os.path.join(self.tasks, "abc"))
        self.video = os.path.join(self.tasks, "abc", "final-1.mp4")
        Path(self.video).write_bytes(b"x")
        self._patcher = patch("app.utils.utils.task_dir", return_value=self.tasks)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        self.root.cleanup()

    def test_summary_swaps_the_host_path_for_a_url(self):
        """
        `library._row_to_summary` emits absolute server paths. Serving those to
        a browser would leak the host filesystem layout and would not be
        fetchable anyway.
        """
        summary = library_controller._public_summary(
            {
                "task_id": "abc",
                "subject": "Walking",
                "task_path": os.path.join(self.tasks, "abc"),
                "video_file": self.video,
            },
            "request-1",
        )

        self.assertNotIn("task_path", summary)
        self.assertNotIn("video_file", summary)
        self.assertEqual(summary["video_url"], "/tasks/abc/final-1.mp4")

    def test_a_missing_video_maps_to_an_empty_url(self):
        summary = library_controller._public_summary(
            {"task_id": "abc", "video_file": ""}, "request-1"
        )
        self.assertEqual(summary["video_url"], "")

    def test_a_path_that_cannot_be_mapped_is_dropped_not_leaked(self):
        """
        `resolve_path_within_directory` stats the file, so a render row whose
        file was deleted -- or any path outside the task tree -- fails to map.
        The helper returns its input on failure; passing that through would put
        an absolute host path in the response.
        """
        for path in (
            os.path.join(self.tasks, "abc", "deleted.mp4"),
            "/etc/passwd",
        ):
            with self.subTest(path=path):
                summary = library_controller._public_summary(
                    {"video_file": path}, "request-1"
                )
                self.assertEqual(summary["video_url"], "")

    def test_an_endpoint_prefix_is_honoured(self):
        with patch.object(
            config, "app", dict(config.app, endpoint="https://cdn.example.com/")
        ):
            summary = library_controller._public_summary(
                {"video_file": self.video}, "request-1"
            )
        self.assertEqual(
            summary["video_url"], "https://cdn.example.com/tasks/abc/final-1.mp4"
        )


class TestTaskLogsEndpoint(unittest.TestCase):
    """Runs without a database; the log buffer is process memory."""

    def setUp(self):
        self.client = TestClient(asgi.app)
        self.task_id = "log-endpoint-task"
        task_logs.clear(self.task_id)

    def tearDown(self):
        task_logs.clear(self.task_id)

    def test_returns_the_captured_lines(self):
        task_logs.append(self.task_id, "first line")
        task_logs.append(self.task_id, "second line")

        body = self.client.get(f"/api/v1/tasks/{self.task_id}/logs").json()

        self.assertEqual(body["status"], 200)
        self.assertEqual(body["data"]["logs"], ["first line", "second line"])

    def test_an_unknown_task_is_empty_rather_than_404(self):
        """
        The buffer keeps only the 20 most recent tasks and is per-process, so
        "no logs" is a normal answer for a real task, not an error.
        """
        response = self.client.get("/api/v1/tasks/never-ran/logs")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["logs"], [])


class TestCaptureIsReentrant(unittest.TestCase):
    def tearDown(self):
        task_logs.clear("outer-task")
        task_logs.clear("inner-task")

    def test_a_nested_capture_does_not_record_twice(self):
        """
        `webui_task` opens a capture around the worker and `task.start` opens
        one around the pipeline. Two live sinks would duplicate every line.
        """
        from loguru import logger

        with task_logs.capture("outer-task"):
            with task_logs.capture("inner-task"):
                logger.info("only once")

        self.assertEqual(len(task_logs.get("outer-task")), 1)
        self.assertEqual(task_logs.get("inner-task"), [])

    def test_disabling_the_outer_capture_disables_the_inner_one(self):
        """`capture_logs=False` must still mean no capture at all."""
        from loguru import logger

        with task_logs.capture("outer-task", enabled=False):
            with task_logs.capture("inner-task"):
                logger.info("suppressed")

        self.assertEqual(task_logs.get("outer-task"), [])
        self.assertEqual(task_logs.get("inner-task"), [])

    def test_the_sink_is_removed_afterwards(self):
        from loguru import logger

        with task_logs.capture("outer-task"):
            logger.info("during")
        logger.info("after")

        self.assertEqual(len(task_logs.get("outer-task")), 1)


@unittest.skipUnless(TEST_DSN, "MPT_TEST_DATABASE_URL not set")
class TestLibraryRoutes(unittest.TestCase):
    def setUp(self):
        reset_test_schema()
        self._config_patcher = patch.object(
            config, "app", dict(config.app, database_url=isolated_dsn(), endpoint="")
        )
        self._config_patcher.start()
        db.reset_pool()
        db.ensure_migrated()

        self.storage = tempfile.TemporaryDirectory()
        self.tasks_root = os.path.join(os.path.realpath(self.storage.name), "tasks")
        self.task_id = str(uuid.uuid4())
        task_path = os.path.join(self.tasks_root, self.task_id)
        os.makedirs(task_path)
        Path(task_path, "final-1.mp4").write_bytes(b"not really a video")

        self._task_dir_patcher = patch(
            "app.utils.utils.task_dir", side_effect=self._task_dir
        )
        self._task_dir_patcher.start()

        with db.connection() as conn:
            conn.execute(
                "INSERT INTO episode (id, title, topic, script, params, state, progress)"
                " VALUES (%s, 'Walking', 'Benefits of walking', 'Walk daily.',"
                " '{\"video_subject\": \"Benefits of walking\"}'::jsonb, 1, 100)",
                (self.task_id,),
            )
            conn.execute(
                "INSERT INTO scene (episode_id, idx, narration, start_ms, end_ms)"
                " VALUES (%s, 0, 'First sentence', 100, 1450)",
                (self.task_id,),
            )
            conn.execute(
                "INSERT INTO asset (episode_id, file_name, provider, width, height)"
                " VALUES (%s, 'clip-1.mp4', 'pexels', 1080, 1920)",
                (self.task_id,),
            )
            conn.execute(
                "INSERT INTO render (episode_id, video_index, file_name, size_bytes)"
                " VALUES (%s, 1, 'final-1.mp4', 18)",
                (self.task_id,),
            )
            conn.commit()

        self.client = TestClient(asgi.app)

    def _task_dir(self, sub_dir: str = ""):
        return os.path.join(self.tasks_root, sub_dir) if sub_dir else self.tasks_root

    def tearDown(self):
        self._task_dir_patcher.stop()
        self.storage.cleanup()
        db.reset_pool()
        self._config_patcher.stop()

    def test_listing_episodes_reports_a_total_that_matches_the_filter(self):
        body = self.client.get("/api/v1/episodes").json()["data"]
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["episodes"][0]["task_id"], self.task_id)

        missed = self.client.get("/api/v1/episodes?query=zzzznomatch").json()["data"]
        self.assertEqual(missed["total"], 0)
        self.assertEqual(missed["episodes"], [])

    def test_a_listed_episode_carries_no_server_path(self):
        row = self.client.get("/api/v1/episodes").json()["data"]["episodes"][0]
        self.assertNotIn("task_path", row)
        self.assertNotIn("video_file", row)
        self.assertEqual(row["video_url"], f"/tasks/{self.task_id}/final-1.mp4")

    def test_search_matches_the_script(self):
        found = self.client.get("/api/v1/episodes?query=walk").json()["data"]
        self.assertEqual(found["total"], 1)

    def test_pagination_arguments_are_echoed_back(self):
        body = self.client.get("/api/v1/episodes?limit=1&offset=1").json()["data"]
        self.assertEqual((body["limit"], body["offset"]), (1, 1))
        self.assertEqual(body["episodes"], [])
        self.assertEqual(body["total"], 1)

    def test_an_oversized_limit_is_rejected(self):
        """Without a ceiling a client can ask for the whole table in one request."""
        self.assertEqual(self.client.get("/api/v1/episodes?limit=5000").status_code, 400)

    def test_episode_detail_scenes_and_assets(self):
        episode = self.client.get(f"/api/v1/episodes/{self.task_id}").json()["data"]
        self.assertEqual(episode["topic"], "Benefits of walking")

        scenes = self.client.get(
            f"/api/v1/episodes/{self.task_id}/scenes"
        ).json()["data"]["scenes"]
        self.assertEqual(scenes[0]["start_ms"], 100)

        assets = self.client.get(
            f"/api/v1/episodes/{self.task_id}/assets"
        ).json()["data"]["assets"]
        self.assertEqual(assets[0]["file_name"], "clip-1.mp4")
        self.assertEqual(assets[0]["width"], 1080)

    def test_renders_are_urls_with_sizes(self):
        renders = self.client.get(
            f"/api/v1/episodes/{self.task_id}/renders"
        ).json()["data"]["renders"]
        self.assertEqual(renders[0]["file_name"], "final-1.mp4")
        self.assertEqual(renders[0]["url"], f"/tasks/{self.task_id}/final-1.mp4")
        self.assertEqual(renders[0]["size_bytes"], 18)

    def test_renders_fall_back_to_run_data_for_a_live_run(self):
        """
        A run finished by the pipeline records its outputs in run_data and
        writes no render row; reading only the table greyed out the Play button.
        """
        with db.connection() as conn:
            conn.execute("DELETE FROM render WHERE episode_id = %s", (self.task_id,))
            conn.execute(
                "UPDATE episode SET run_data = %s::jsonb WHERE id = %s",
                ('{"videos": ["/some/other/host/path/final-1.mp4"]}', self.task_id),
            )
            conn.commit()

        renders = self.client.get(
            f"/api/v1/episodes/{self.task_id}/renders"
        ).json()["data"]["renders"]
        self.assertEqual(renders[0]["url"], f"/tasks/{self.task_id}/final-1.mp4")
        self.assertIsNone(renders[0]["size_bytes"])

    def test_an_unknown_episode_is_404(self):
        for path in ("", "/renders"):
            with self.subTest(path=path):
                response = self.client.get(f"/api/v1/episodes/no-such-id{path}")
                self.assertEqual(response.status_code, 404)

    def test_projects_can_be_listed_and_created(self):
        created = self.client.post("/api/v1/projects", json={"name": "Fitness"})
        self.assertEqual(created.status_code, 200)
        project_id = created.json()["data"]["id"]

        names = {row["name"] for row in self.client.get("/api/v1/projects").json()["data"]["projects"]}
        self.assertIn("Fitness", names)

        # The default project always exists so an episode has a parent.
        self.assertIn("Unsorted", names)
        self.assertNotEqual(project_id, 1)

    def test_creating_a_project_twice_returns_the_same_one(self):
        first = self.client.post("/api/v1/projects", json={"name": "Fitness"})
        second = self.client.post("/api/v1/projects", json={"name": "Fitness"})
        self.assertEqual(first.json()["data"]["id"], second.json()["data"]["id"])

    def test_a_blank_project_name_is_rejected(self):
        self.assertEqual(
            self.client.post("/api/v1/projects", json={"name": "   "}).status_code, 400
        )

    def test_an_episode_can_be_moved_between_projects(self):
        project_id = self.client.post(
            "/api/v1/projects", json={"name": "Fitness"}
        ).json()["data"]["id"]

        moved = self.client.patch(
            f"/api/v1/episodes/{self.task_id}", json={"project_id": project_id}
        )
        self.assertEqual(moved.status_code, 200)

        episode = self.client.get(f"/api/v1/episodes/{self.task_id}").json()["data"]
        self.assertEqual(episode["project_id"], project_id)

        filtered = self.client.get(
            f"/api/v1/episodes?project_id={project_id}"
        ).json()["data"]
        self.assertEqual(filtered["total"], 1)

    def test_the_task_list_tolerates_an_episode_with_no_state(self):
        """
        `episode.state` is nullable on purpose -- an imported task has no state
        and the UI buckets it as history. `TaskStatusData.state` was typed
        `int`, so /api/v1/tasks returned a 500 as soon as the library held one
        of those rows.
        """
        with db.connection() as conn:
            conn.execute(
                "UPDATE episode SET state = NULL WHERE id = %s", (self.task_id,)
            )
            conn.commit()

        response = self.client.get("/api/v1/tasks")
        self.assertEqual(response.status_code, 200)
        states = [task["state"] for task in response.json()["data"]["tasks"]]
        self.assertIn(None, states)

    def test_moving_an_unknown_episode_is_404(self):
        response = self.client.patch(
            "/api/v1/episodes/no-such-id", json={"project_id": 1}
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
