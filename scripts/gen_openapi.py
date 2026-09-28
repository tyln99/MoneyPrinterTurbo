"""Write openapi.json without starting the server or touching the database.

`openapi-typescript` then generates the frontend's types from it, so the
39-field VideoParams and every response model stay in sync by construction
rather than by hand.
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from app.asgi import app  # noqa: E402

out = pathlib.Path(__file__).parent.parent / "openapi.json"
out.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False), encoding="utf-8")
print(f"wrote {out} ({len(app.openapi()['paths'])} paths)")
