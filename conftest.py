"""pytest 入口：把仓库根加入 sys.path 并安装框架替身。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests import framework_stub  # noqa: E402

framework_stub.install()
