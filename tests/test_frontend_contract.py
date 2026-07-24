from __future__ import annotations

import json
from pathlib import Path

from video_edit_automation.main import app


def test_committed_frontend_openapi_schema_matches_fastapi() -> None:
    schema_path = Path(__file__).parents[1] / "frontend" / "openapi.json"
    committed_schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert committed_schema == app.openapi()

