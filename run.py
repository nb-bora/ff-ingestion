"""FastAPI development and production startup script."""

from __future__ import annotations

import os
import sys

import uvicorn


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
    from config import settings  # noqa: E402

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=os.getenv("ENVIRONMENT") == "dev",
        log_level="info",
    )

