-- The task library: projects, episodes, scenes, assets, renders.
--
-- Everything here replaces data that today lives in storage/tasks/<id>/script.json
-- plus whatever the WebUI can infer by scanning directories. Media bytes stay on
-- disk; only storage-relative paths are recorded, so the storage tree can be
-- remounted or moved without invalidating a row.

CREATE TABLE project (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT        NOT NULL,
    defaults    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT project_name_key      UNIQUE (name),
    CONSTRAINT project_name_present  CHECK (btrim(name) <> ''),
    CONSTRAINT project_defaults_obj  CHECK (jsonb_typeof(defaults) = 'object')
);

-- A partial VideoParams dict, not columns: VideoParams has 39 fields and grows
-- with every provider added, so promoting them here would mean a migration per
-- feature and a second source of truth alongside app/models/schema.py.
INSERT INTO project (id, name, defaults)
VALUES (1, 'Unsorted', '{}'::jsonb);
SELECT setval('project_id_seq', 1, true);


CREATE TABLE episode (
    id          TEXT        PRIMARY KEY,
    project_id  BIGINT      NOT NULL DEFAULT 1 REFERENCES project(id) ON DELETE RESTRICT,
    title       TEXT        NOT NULL DEFAULT '',
    topic       TEXT        NOT NULL DEFAULT '',
    script      TEXT        NOT NULL DEFAULT '',
    params      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    run_data    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    state       SMALLINT,
    progress    SMALLINT    NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- TEXT rather than UUID because this string is also the storage directory
    -- name, and the test suite runs the pipeline with ids like "test-wavespeed".
    -- The invariant that actually matters is that it is a safe path segment.
    CONSTRAINT episode_id_safe     CHECK (id ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'),
    CONSTRAINT episode_params_obj  CHECK (jsonb_typeof(params) = 'object'),
    CONSTRAINT episode_run_obj     CHECK (jsonb_typeof(run_data) = 'object'),
    CONSTRAINT episode_progress_ok CHECK (progress BETWEEN 0 AND 100)
);

-- state is nullable on purpose. The WebUI buckets a task with no state as
-- "history" (_task_state_filter_key); storing 0 instead of NULL would silently
-- re-bucket every imported episode.
COMMENT ON COLUMN episode.state IS
    'const.TASK_STATE_* (-1 failed, 1 complete, 4 processing); NULL = history';

CREATE INDEX episode_project_updated_idx ON episode (project_id, updated_at DESC);
CREATE INDEX episode_updated_idx         ON episode (updated_at DESC);

-- 'simple' rather than 'english': the corpus is mixed Vietnamese and English,
-- so an English stemmer and stopword list would be wrong for most rows.
ALTER TABLE episode ADD COLUMN search_doc tsvector
    GENERATED ALWAYS AS (
        to_tsvector('simple', title || ' ' || topic || ' ' || script)
    ) STORED;
CREATE INDEX episode_search_idx ON episode USING gin (search_doc);


CREATE TABLE scene (
    id          BIGSERIAL   PRIMARY KEY,
    episode_id  TEXT        NOT NULL REFERENCES episode(id) ON DELETE CASCADE,
    idx         INTEGER     NOT NULL,
    narration   TEXT        NOT NULL DEFAULT '',
    search_term TEXT        NOT NULL DEFAULT '',
    start_ms    INTEGER,
    end_ms      INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT scene_episode_idx_key UNIQUE (episode_id, idx),
    CONSTRAINT scene_idx_nonneg      CHECK (idx >= 0),
    -- Integer milliseconds, not float seconds: SRT is ms-precision and float
    -- seconds accumulate rounding across dozens of scenes.
    CONSTRAINT scene_time_pair       CHECK ((start_ms IS NULL) = (end_ms IS NULL)),
    CONSTRAINT scene_time_order      CHECK (start_ms IS NULL OR (start_ms >= 0 AND end_ms > start_ms))
);

CREATE INDEX scene_episode_order_idx ON scene (episode_id, idx);


CREATE TABLE asset (
    id           BIGSERIAL   PRIMARY KEY,
    episode_id   TEXT        NOT NULL REFERENCES episode(id) ON DELETE CASCADE,
    -- Nullable because material_sources records no scene mapping today; the
    -- pipeline picks materials by keyword, not by sentence.
    scene_id     BIGINT      REFERENCES scene(id) ON DELETE SET NULL,
    kind         TEXT        NOT NULL DEFAULT 'material',
    file_name    TEXT        NOT NULL,
    provider     TEXT        NOT NULL DEFAULT '',
    search_term  TEXT        NOT NULL DEFAULT '',
    duration_s   INTEGER,
    width        INTEGER,
    height       INTEGER,
    source_info  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT asset_episode_file_key UNIQUE (episode_id, kind, file_name),
    CONSTRAINT asset_source_obj       CHECK (jsonb_typeof(source_info) = 'object'),
    CONSTRAINT asset_dims_pair        CHECK ((width IS NULL) = (height IS NULL))
);

CREATE INDEX asset_episode_idx ON asset (episode_id);
CREATE INDEX asset_scene_idx   ON asset (scene_id);


CREATE TABLE render (
    id           BIGSERIAL   PRIMARY KEY,
    episode_id   TEXT        NOT NULL REFERENCES episode(id) ON DELETE CASCADE,
    video_index  INTEGER     NOT NULL,
    file_name    TEXT        NOT NULL,
    size_bytes   BIGINT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT render_episode_file_key UNIQUE (episode_id, file_name)
);

CREATE INDEX render_episode_created_idx ON render (episode_id, created_at DESC);
