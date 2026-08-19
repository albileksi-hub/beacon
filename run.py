"""Development server entry point.

Changes into the project directory first so relative paths (the SQLite file,
.env) resolve predictably regardless of where the process was launched from.
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

if __name__ == "__main__":
    os.chdir(PROJECT_ROOT)
    sys.path.insert(0, str(PROJECT_ROOT))

    # Keep the rollups fresh while developing. Set before the app is
    # imported, because settings are read once and cached.
    os.environ.setdefault("BEACON_ROLLUP_INTERVAL_SECONDS", "30")

    import uvicorn

    from app.db import init_db

    init_db()
    uvicorn.run("app.main:app", host="127.0.0.1", port=8100)
