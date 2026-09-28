import json
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.config import config
from app.models import const
from app.services import db, library


TEST_DSN = os.environ.get("MPT_TEST_DATABASE_URL", "")

from test.services.test_db import isolated_dsn, reset_test_schema  # noqa: E402

SRT = """1
00:00:00,100 --> 00:00:01,450
First sentence

2
00:00:01,650 --> 00:00:04,475
Second sentence
"""


class TestSrtTimestampParsing(unittest.TestCase):
    """Runs without a database."""

    def test_parses_a_cue_range_into_milliseconds(self):
        self.assertEqual(
            library._srt_range_to_ms("00:00:01,650 --> 00:00:04,475"), (1650, 4475)
        )

    def test_parses_hours(self):
        self.assertEqual(
            library._srt_range_to_ms("01:02:03,004 --> 01:02:04,005"),
            (3723004, 3724005),
        )

    def test_rejects_a_reversed_or_empty_range(self):
        """The scene table rejects end <= start, so catch it before inserting."""
        self.assertIsNone(library._srt_range_to_ms("00:00:05,000 --> 00:00:01,000"))
        self.assertIsNone(library._srt_range_to_ms("00:00:01,000 --> 00:00:01,000"))

    def test_rejects_unparseable_input(self):
        self.assertIsNone(library._srt_range_to_ms(""))
        self.assertIsNone(library._srt_range_to_ms("not a timestamp"))


class TestImportFilter(unittest.TestCase):
    def test_accepts_a_uuid_directory_holding_a_script(self):
        with tempfile.TemporaryDirectory() as root:
            name = str(uuid.uuid4())
            path = os.path.join(root, name)
            os.makedirs(path)
            Path(path, "script.json").write_text("{}", encoding="utf-8")
            self.assertTrue(library._is_importable(name, path))

    def test_rejects_test_fixture_directories(self):
        """
        Fixtures like `test-wavespeed` and `sonilo-task` live alongside real
        tasks in storage/tasks and must never reach the library.
        """
        with tempfile.TemporaryDirectory() as root:
            for name in ("test-wavespeed", "sonilo-task", "clip-speed-task"):
                path = os.path.join(root, name)
                os.makedirs(path)
                Path(path, "script.json").write_text("{}", encoding="utf-8")
                with self.subTest(name=name):
                    self.assertFalse(library._is_importable(name, path))

    def test_rejects_a_uuid_directory_with_no_script(self):
        with tempfile.TemporaryDirectory() as root:
            name = str(uuid.uuid4())
            path = os.path.join(root, name)
            os.makedirs(path)
            self.assertFalse(library._is_importable(name, path))


@unittest.skipUnless(TEST_DSN, "MPT_TEST_DATABASE_URL not set")
class TestImportAndRead(unittest.TestCase):
    def setUp(self):
        reset_test_schema()
        self._patcher = patch.object(
            config, "app", dict(config.app, database_url=isolated_dsn())
        )
        self._patcher.start()
        db.reset_pool()
        db.ensure_migrated()

        self.root = tempfile.TemporaryDirectory()
        self.task_id = str(uuid.uuid4())
        self.path = os.path.join(self.root.name, self.task_id)
        os.makedirs(self.path)
        Path(self.path, "script.json").write_text(
            json.dumps(
                {
                    "script": "Walking every day is good for you.",
                    "search_terms": ["morning walk"],
                    "params": {"video_subject": "Benefits of walking"},
                    "material_sources": [
                        {
                            "provider": "pexels",
                            "local_file": "clip-1.mp4",
                            "duration": 5,
                            "search_term": "morning walk",
                            "rendition": {"width": 1080, "height": 1920},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        Path(self.path, "subtitle.srt").write_text(SRT, encoding="utf-8")
        Path(self.path, "final-1.mp4").write_bytes(b"not really a video")

    def tearDown(self):
        with db.connection() as conn:
            conn.execute("DELETE FROM episode WHERE id = %s", (self.task_id,))
            conn.commit()
        self.root.cleanup()
        db.reset_pool()
        self._patcher.stop()

    def _episode(self) -> dict:
        return library.get_episode(self.task_id)

    def test_imports_an_episode_with_its_scenes_assets_and_renders(self):
        counts = library.import_legacy_tasks(tasks_root=self.root.name)
        self.assertEqual(counts["imported"], 1)
        self.assertEqual(counts["failed"], 0)

        episode = self._episode()
        self.assertEqual(episode["topic"], "Benefits of walking")
        self.assertEqual(episode["state"], const.TASK_STATE_COMPLETE)

        scenes = library.list_scenes(self.task_id)
        self.assertEqual(len(scenes), 2)
        self.assertEqual(scenes[0]["start_ms"], 100)
        self.assertEqual(scenes[0]["end_ms"], 1450)
        self.assertEqual(scenes[1]["narration"], "Second sentence")

        with db.connection() as conn:
            assets = conn.execute(
                "SELECT file_name, provider, width FROM asset WHERE episode_id = %s",
                (self.task_id,),
            ).fetchall()
            renders = conn.execute(
                "SELECT file_name, size_bytes FROM render WHERE episode_id = %s",
                (self.task_id,),
            ).fetchall()
        self.assertEqual(assets[0]["file_name"], "clip-1.mp4")
        self.assertEqual(assets[0]["width"], 1080)
        self.assertEqual(renders[0]["file_name"], "final-1.mp4")
        self.assertGreater(renders[0]["size_bytes"], 0)

    def test_import_is_idempotent(self):
        library.import_legacy_tasks(tasks_root=self.root.name)
        library.import_legacy_tasks(tasks_root=self.root.name)
        self.assertEqual(len(library.list_scenes(self.task_id)), 2)
        with db.connection() as conn:
            count = conn.execute(
                "SELECT count(*) AS n FROM asset WHERE episode_id = %s",
                (self.task_id,),
            ).fetchone()
        self.assertEqual(count["n"], 1)

    def test_import_never_writes_to_the_storage_tree(self):
        """1.2 GB of media is at stake; the importer must be strictly read-only."""
        def snapshot():
            entries = {}
            for base, _, files in os.walk(self.root.name):
                for name in files:
                    full = os.path.join(base, name)
                    stat = os.stat(full)
                    entries[full] = (stat.st_mtime, stat.st_size)
            return entries

        before = snapshot()
        library.import_legacy_tasks(tasks_root=self.root.name)
        self.assertEqual(before, snapshot())

    def test_an_episode_with_no_final_video_imports_with_a_null_state(self):
        """
        The WebUI buckets a stateless task as history; 0 would re-bucket it as
        failed.
        """
        os.remove(os.path.join(self.path, "final-1.mp4"))
        library.import_legacy_tasks(tasks_root=self.root.name)
        self.assertIsNone(self._episode()["state"])

    def test_a_corrupt_script_costs_only_its_own_directory(self):
        other = str(uuid.uuid4())
        other_path = os.path.join(self.root.name, other)
        os.makedirs(other_path)
        Path(other_path, "script.json").write_text("{not json", encoding="utf-8")

        counts = library.import_legacy_tasks(tasks_root=self.root.name)
        self.assertEqual(counts["imported"], 1)
        self.assertEqual(counts["failed"], 1)
        self.assertIsNotNone(self._episode())

    def test_dry_run_reports_without_writing(self):
        counts = library.import_legacy_tasks(tasks_root=self.root.name, dry_run=True)
        self.assertEqual(counts["imported"], 1)
        self.assertIsNone(self._episode())

    def test_list_episodes_returns_the_shape_the_task_panel_expects(self):
        library.import_legacy_tasks(tasks_root=self.root.name)
        row = next(
            item
            for item in library.list_episodes(limit=100)
            if item["task_id"] == self.task_id
        )
        for key in (
            "task_id",
            "subject",
            "state",
            "progress",
            "mtime",
            "task_path",
            "video_file",
            "has_restore_data",
        ):
            self.assertIn(key, row)
        self.assertEqual(row["subject"], "Benefits of walking")
        self.assertTrue(row["has_restore_data"])
        self.assertTrue(row["video_file"].endswith("final-1.mp4"))

    def test_search_matches_on_title_and_script(self):
        library.import_legacy_tasks(tasks_root=self.root.name)
        found = [
            item["task_id"] for item in library.list_episodes(query="walking")
        ]
        self.assertIn(self.task_id, found)
        missed = [
            item["task_id"] for item in library.list_episodes(query="zzzznomatch")
        ]
        self.assertNotIn(self.task_id, missed)


if __name__ == "__main__":
    unittest.main()
