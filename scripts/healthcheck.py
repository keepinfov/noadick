from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def main() -> int:
    path = Path(os.environ.get("HEARTBEAT_PATH", "/tmp/noadick-heartbeat"))
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return 1
    return 0 if age <= 75 else 1


if __name__ == "__main__":
    sys.exit(main())
