"""让 pytest 运行时也能 import src / helpers。"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "tests")):
    if path not in sys.path:
        sys.path.insert(0, path)
