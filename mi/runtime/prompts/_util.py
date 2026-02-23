from __future__ import annotations

import json
from typing import Any


def _to_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True)

