"""pytest 根配置：确保项目根在 sys.path 中，可导入 edu_assistant。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
