"""Export the FastAPI OpenAPI schema to a JSON file.

Used by the frontend type-generation pipeline (issue #80). The output is
deterministic — keys are sorted and indentation is fixed — so the committed
``apps/api/openapi.json`` snapshot can be byte-compared in CI to detect
contract drift between the backend and the generated TypeScript types.

Usage:
    python scripts/export_openapi.py [output_path]

When no path is given, writes to ``apps/api/openapi.json`` next to this
script's parent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make `import app.*` work regardless of the caller's CWD.
APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.main import create_app  # noqa: E402


def render_openapi() -> str:
    """Build the app, dump its OpenAPI schema, and return it as a JSON string.

    ``sort_keys=True`` is the load-bearing bit: FastAPI's schema generation is
    insertion-ordered, so without sorting, unrelated reorderings of routes or
    Pydantic fields would produce noisy diffs in the committed snapshot.
    """
    app = create_app()
    schema = app.openapi()
    return json.dumps(schema, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) > 2:
        print("usage: export_openapi.py [output_path]", file=sys.stderr)
        return 2
    output = Path(argv[1]) if len(argv) == 2 else APP_ROOT / "openapi.json"
    output.write_text(render_openapi(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
