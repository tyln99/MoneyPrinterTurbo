import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.config import config
from app.services import db


ROOT_DIR = Path(__file__).parent.parent.parent
TEST_DSN = os.environ.get("MPT_TEST_DATABASE_URL", "")

TEST_SCHEMA = "mpt_test"


def isolated_dsn() -> str:
    """
    A DSN pinned to a throwaway schema.

    The database tests create and drop their whole schema, so they must never be
    pointed at `public`: MPT_TEST_DATABASE_URL and the application's own
    database_url can legitimately be the same database, and dropping `public`
    there destroys the real library.
    """
    separator = "&" if "?" in TEST_DSN else "?"
    return f"{TEST_DSN}{separator}options=-c%20search_path%3D{TEST_SCHEMA}"


def reset_test_schema() -> None:
    """Recreate the throwaway schema, leaving `public` untouched."""
    import psycopg

    with psycopg.connect(TEST_DSN, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
        conn.execute(f"CREATE SCHEMA {TEST_SCHEMA}")


class TestDsnResolution(unittest.TestCase):
    """These run without a database; that is the point of them."""

    def test_falls_back_to_the_compose_default(self):
        """A config.toml written before this feature existed still works."""
        with patch.object(config, "app", dict(config.app, database_url="")):
            self.assertEqual(db.resolve_dsn(), db.DEFAULT_DSN)

    def test_config_is_read_at_call_time_not_import_time(self):
        """
        Config section globals bind once at import, so reading the DSN lazily is
        what lets tests patch it and what lets a WebUI settings edit take effect.
        """
        with patch.object(
            config, "app", dict(config.app, database_url="postgresql://x/y")
        ):
            self.assertEqual(db.resolve_dsn(), "postgresql://x/y")
        # And it follows the patch back out again.
        self.assertNotEqual(db.resolve_dsn(), "postgresql://x/y")

    def test_importing_the_module_opens_no_connection(self):
        """
        Connecting at import would break `cli.py --help`, test collection, and
        any container that starts before Postgres is healthy.

        Checked in a fresh interpreter rather than by inspecting module globals,
        because any earlier test that touched the database would have opened the
        pool and made an in-process assertion pass or fail by test order.
        """
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, %r);"
                "from app.services import db, state;"
                "print(db._pool is None and not db._migrated)" % str(ROOT_DIR),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("True", result.stdout)

    def test_every_migration_file_is_numbered(self):
        """The runner applies files in filename order, so the prefix matters."""
        names = [path.name for path in db._migration_files()]
        self.assertTrue(names, "no migration files found")
        for name in names:
            with self.subTest(name=name):
                self.assertRegex(name, r"^\d{4}_[a-z0-9_]+\.sql$")


@unittest.skipUnless(TEST_DSN, "MPT_TEST_DATABASE_URL not set")
class TestMigrations(unittest.TestCase):
    def setUp(self):
        reset_test_schema()
        self._patcher = patch.object(
            config, "app", dict(config.app, database_url=isolated_dsn())
        )
        self._patcher.start()
        db.reset_pool()

    def tearDown(self):
        db.reset_pool()
        self._patcher.stop()

    def test_migrations_apply_once_and_are_idempotent(self):
        first = db.run_migrations(db.get_pool())
        self.assertIn("0001_library.sql", first)
        self.assertEqual(db.run_migrations(db.get_pool()), [])

    def test_schema_has_the_library_tables(self):
        db.run_migrations(db.get_pool())
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = %s",
                (TEST_SCHEMA,),
            ).fetchall()
        names = {row["tablename"] for row in rows}
        self.assertLessEqual(
            {"project", "episode", "scene", "asset", "render"}, names
        )

    def test_a_default_project_exists_so_episodes_always_have_a_parent(self):
        db.run_migrations(db.get_pool())
        with db.connection() as conn:
            row = conn.execute("SELECT id, name FROM project").fetchone()
        self.assertEqual(row["id"], 1)

    def test_episode_id_must_be_a_safe_path_segment(self):
        """
        The id doubles as the storage directory name and as a component of
        /stream/{file_path}, so traversal has to be impossible at this layer.
        """
        db.run_migrations(db.get_pool())
        for bad in ("../escape", "has/slash", "", "with space"):
            with self.subTest(bad=bad), self.assertRaises(Exception):
                with db.connection() as conn:
                    conn.execute("INSERT INTO episode (id) VALUES (%s)", (bad,))
                    conn.commit()

    def test_episode_state_is_nullable(self):
        """
        The WebUI buckets a task with no state as 'history'. Storing 0 instead of
        NULL would silently re-bucket every imported episode.
        """
        db.run_migrations(db.get_pool())
        with db.connection() as conn:
            conn.execute("INSERT INTO episode (id) VALUES ('nullable-state')")
            row = conn.execute(
                "SELECT state FROM episode WHERE id = 'nullable-state'"
            ).fetchone()
            conn.commit()
        self.assertIsNone(row["state"])

    def test_scene_time_range_must_be_ordered_and_paired(self):
        db.run_migrations(db.get_pool())
        with db.connection() as conn:
            conn.execute("INSERT INTO episode (id) VALUES ('scene-checks')")
            conn.commit()
        # end before start
        with self.assertRaises(Exception):
            with db.connection() as conn:
                conn.execute(
                    "INSERT INTO scene (episode_id, idx, start_ms, end_ms)"
                    " VALUES ('scene-checks', 0, 500, 100)"
                )
                conn.commit()
        # only one half of the pair set
        with self.assertRaises(Exception):
            with db.connection() as conn:
                conn.execute(
                    "INSERT INTO scene (episode_id, idx, start_ms)"
                    " VALUES ('scene-checks', 1, 500)"
                )
                conn.commit()

    def test_deleting_an_episode_takes_its_scenes_and_assets_with_it(self):
        db.run_migrations(db.get_pool())
        with db.connection() as conn:
            conn.execute("INSERT INTO episode (id) VALUES ('cascade-me')")
            conn.execute(
                "INSERT INTO scene (episode_id, idx) VALUES ('cascade-me', 0)"
            )
            conn.execute(
                "INSERT INTO asset (episode_id, file_name) VALUES ('cascade-me', 'a.mp4')"
            )
            conn.execute("DELETE FROM episode WHERE id = 'cascade-me'")
            scenes = conn.execute(
                "SELECT count(*) AS n FROM scene WHERE episode_id = 'cascade-me'"
            ).fetchone()
            assets = conn.execute(
                "SELECT count(*) AS n FROM asset WHERE episode_id = 'cascade-me'"
            ).fetchone()
            conn.commit()
        self.assertEqual(scenes["n"], 0)
        self.assertEqual(assets["n"], 0)


if __name__ == "__main__":
    unittest.main()
