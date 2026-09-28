# MoneyPrinterTurbo — React UI

The library and episode-details screens. Creating a video still happens in the
Streamlit wizard (`webui/Main.py`): its four form panels and the settings dialog
have no REST equivalent yet. Both UIs read the same Postgres database, so a run
started in either one shows up in this table.

## Develop

```bash
uv run python main.py      # the API, on :8080
cd web && pnpm install && pnpm dev
```

`pnpm dev` serves on :5173 and **proxies** `/api` and `/tasks` to the API. The
proxy is not a convenience: with `CORS_ALLOWED_ORIGINS` unset (the default, and
unset in every compose file) a browser origin is rejected twice — by
`reject_untrusted_browser_origin`, which 403s every path, and by the absence of
`CORSMiddleware`. A proxied request reaches FastAPI with no `Origin` header,
which `is_browser_origin_allowed` explicitly permits.

Point it elsewhere with `MPT_API_URL`, and set `VITE_STREAMLIT_URL` if Streamlit
is not on :8501.

## Types

`src/api/schema.d.ts` is generated, not written:

```bash
uv run python scripts/gen_openapi.py   # writes openapi.json, no server needed
cd web && pnpm gen:api
```

CI regenerates both and fails on a diff, so a route or response-model change
cannot silently drift from what the frontend compiles against.

## Ship

```bash
pnpm build     # -> web/dist
```

`app/asgi.py` serves `web/dist` at `/` when it exists and falls back to
`resource/public` when it does not, so a checkout that never ran `pnpm build`
still starts. Production is therefore same-origin and needs no CORS. Deep links
work: the mount serves `index.html` for extensionless paths, while a missing
asset still 404s.

## Note on auth

`<video src>` cannot send the `x-api-key` header, and media is served from the
`/tasks` mount. This works today only because `app.api_key` is empty. Setting a
key will break playback until a signed-URL or cookie mechanism exists.
