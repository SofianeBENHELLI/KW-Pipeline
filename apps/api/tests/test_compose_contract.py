"""Pin the workstation-deploy compose-file contract.

Lightweight regression guards for ``docker/docker-compose.yml`` that
caught operators in the past:

- The cloudflared service must pass
  ``--config /etc/cloudflared/config.yml`` to the connector with the
  flag and path adjacent in the command list. Without it (or with
  the path drifted to e.g. ``config.yaml``), the connector skips the
  mounted config and errors out with
  "tunnel run requires the ID or name of the tunnel".

These are pure text-shape assertions — no docker / YAML parser
dependency, no compose-up — so they run in the default
``pytest -m "not integration"`` lane.
"""

from __future__ import annotations

from pathlib import Path

_COMPOSE = Path(__file__).resolve().parents[3] / "docker" / "docker-compose.yml"
_DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def _cloudflared_block(text: str) -> str:
    """Return the cloudflared service block (everything indented under
    ``cloudflared:`` up to the next top-level key)."""
    lines = text.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.strip().startswith("cloudflared:")),
        None,
    )
    assert start is not None, "cloudflared service block not found in docker-compose.yml"
    block: list[str] = []
    for line in lines[start + 1 :]:
        if line and not line.startswith(" ") and not line.startswith("\t"):
            break
        block.append(line)
    return "\n".join(block)


def test_cloudflared_command_passes_config_flag() -> None:
    block_text = _cloudflared_block(_COMPOSE.read_text())

    assert "command:" in block_text, "cloudflared service has no command: directive"
    # Tight coupling: the flag and its path must appear adjacent in
    # the command list with the literal compose-array quoting. Two
    # independent ``in`` checks would let a typo like
    # ``--config /etc/cloudflared/config.yaml`` slip through; this
    # form will not.
    expected = '"--config", "/etc/cloudflared/config.yml"'
    assert expected in block_text, (
        f"cloudflared command must include {expected!r} as adjacent "
        "list elements. Without it the connector skips the mounted "
        'config and errors "tunnel run requires the ID or name of '
        'the tunnel". See git blame on this test for context.'
    )


def test_every_service_caps_docker_log_size() -> None:
    """Pin docker-log rotation on every service.

    Docker's default ``json-file`` driver keeps growing log files
    forever. Over weeks of uptime that fills the host disk, and SQLite
    starts failing with "database or disk is full" — looks like an
    outage but is actually just retention. Each service must declare
    a bounded ``max-size`` / ``max-file`` policy so the host floor is
    knowable.
    """
    text = _COMPOSE.read_text()
    # One occurrence per service block (neo4j, api, cloudflared = 3).
    assert text.count("driver: json-file") >= 3, (
        "every service in docker-compose.yml must declare a bounded "
        "logging driver to prevent runaway docker-log growth"
    )
    assert text.count('max-size: "50m"') >= 3
    assert text.count('max-file: "5"') >= 3


def test_dockerfile_uses_gunicorn_with_periodic_recycling() -> None:
    """Pin the worker-recycling defense in the production CMD.

    Memory drift in spaCy / pdfplumber / SDK clients accumulates over
    weeks-long uptimes and is invisible until the kernel OOM-kills the
    container. ``gunicorn --max-requests`` recycles the worker every N
    requests, returning that memory to the OS. If a future change
    drops back to a bare ``uvicorn`` invocation, this guard fires.
    """
    text = _DOCKERFILE.read_text()
    # Tolerate either the JSON-array CMD form or the shell form, but
    # require all three load-bearing tokens to be present.
    assert '"gunicorn"' in text, "production CMD must use gunicorn as the supervisor"
    assert '"uvicorn.workers.UvicornWorker"' in text, (
        "gunicorn must run the uvicorn worker class so FastAPI's async stack still serves requests."
    )
    assert '"--max-requests"' in text, (
        "gunicorn must be configured with --max-requests so the worker "
        "periodically recycles and returns leaked memory to the OS."
    )
