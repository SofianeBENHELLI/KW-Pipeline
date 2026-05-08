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
