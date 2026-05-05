"""``kw-demo-load`` console-script entry point.

Thin wrapper around :mod:`scripts.load_demo_dataset` so the full demo
loader is reachable from a packaged install (``pip install -e
'apps/api[test]'``) without exposing the ``scripts/`` directory as an
import package.

The loader's actual implementation lives in
``apps/api/scripts/load_demo_dataset.py`` because that is where every
other demo helper (``seed_demo.py``, ``customer_demo_smoke.py``)
already lives — keeping it there means ``apps/api/scripts/`` remains
the single canonical location for demo plumbing. This wrapper resolves
the script path relative to the installed package, prepends it to
``sys.path``, and invokes ``main`` with the caller's argv.

Usage:

    .venv312/bin/kw-demo-load [--api http://127.0.0.1:8000]

Reachable as ``python -m app.demo_loader`` for callers who prefer the
module form.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _load_main() -> object:
    """Resolve the loader's ``main`` callable from the scripts directory.

    The scripts directory is intentionally excluded from the wheel's
    package list (see ``pyproject.toml``'s ``tool.setuptools.packages.find``),
    but the editable install keeps the source tree on disk so we can reach
    in and import it lazily. A non-editable / wheel-only install would
    need to copy the loader and the fixtures next to the package — out of
    scope for the v1 demo path.
    """
    api_root = Path(__file__).resolve().parent.parent
    scripts_dir = api_root / "scripts"
    if not scripts_dir.exists():
        raise SystemExit(
            "Cannot locate the demo loader script directory at "
            f"{scripts_dir}. The full demo loader requires an editable "
            "install (`pip install -e 'apps/api[test]'`) so the scripts/ "
            "directory is reachable on disk."
        )
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from load_demo_dataset import main  # noqa: PLC0415  (lazy by design)

    return main


def main() -> int:
    """Console-script entry point — forwards to the loader's main."""
    runner = _load_main()
    return int(runner(sys.argv[1:]) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
