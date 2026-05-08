"""Pin the workstation-deploy compose-file contract.

Lightweight regression guards for ``docker/docker-compose.yml`` that
caught operators in the past:

- The cloudflared service must pass ``--config /etc/cloudflared/config.yml``
  to the connector. Without it the official image's runtime user
  doesn't find the mounted ``config.yml`` and errors out with
  "tunnel run requires the ID or name of the tunnel".

These are pure text-shape assertions — no docker / YAML parser
dependency, no compose-up — so they run in the default
``pytest -m "not integration"`` lane.
"""

from __future__ import annotations

from pathlib import Path

_COMPOSE = Path(__file__).resolve().parents[3] / "docker" / "docker-compose.yml"


def test_cloudflared_command_passes_config_flag() -> None:
    text = _COMPOSE.read_text()
    # Find the cloudflared service block by scanning for the service
    # header, then walk forward to the next top-level key. This is
    # cheaper than parsing YAML and gives a clearer failure message.
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
    block_text = "\n".join(block)

    assert "command:" in block_text, "cloudflared service has no command: directive"
    assert "--config" in block_text and "/etc/cloudflared/config.yml" in block_text, (
        "cloudflared command must include `--config /etc/cloudflared/config.yml`. "
        "Without it the connector skips the mounted config and errors "
        '"tunnel run requires the ID or name of the tunnel". '
        "See git blame on this test for context."
    )
