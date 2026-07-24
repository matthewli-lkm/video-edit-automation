from __future__ import annotations

import argparse
import json
from pathlib import Path

from video_edit_automation.main import app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export the FastAPI schema used to generate dashboard API types."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("frontend/openapi.json"),
    )
    args = parser.parse_args()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(output)


if __name__ == "__main__":
    main()
