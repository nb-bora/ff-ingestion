from __future__ import annotations

import sys
from pathlib import Path

# Ensure `src/` is importable as top-level (src-layout)
SRC_PATH = (Path(__file__).parent.parent / "src").resolve()
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))
