"""Write the live OpenAPI spec to docs/api/openapi.json.

Generated directly from the app object -- no server, no port to collide with, no
stale process to accidentally curl. Run after adding or changing any route:

    python -m scripts.export_openapi
"""

import json
from pathlib import Path

from app.main import app

TARGET = Path("docs/api/openapi.json")


def render() -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(render())
    print(f"wrote {TARGET} ({len(app.openapi()['paths'])} paths)")
