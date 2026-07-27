from __future__ import annotations

import uvicorn

from video_edit_automation.config import Settings
from video_edit_automation.main import create_app

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def main() -> None:
    settings = Settings()
    if settings.host not in LOOPBACK_HOSTS:
        raise SystemExit("The Cutroom desktop backend must bind to localhost")
    uvicorn.run(
        create_app(settings=settings),
        host=settings.host,
        port=settings.port,
        log_level="info",
        access_log=False,
    )


if __name__ == "__main__":
    main()
