# Backend image for the KW-Pipeline FastAPI app.
#
# Used by:
# - ``docker/docker-compose.yml`` for local end-to-end development.
# - The workstation deployment per ``docs/runbook/workstation-deploy.md``
#   (compose stack runs this image alongside the cloudflared sidecar).
#
# Multi-stage build keeps the runtime layer small. ``pdfplumber`` and
# ``python-pptx`` ship pure-Python wheels; no system-level build deps
# beyond what ``python:3.12-slim`` already bundles.
FROM python:3.12-slim AS build
WORKDIR /app

# Match the venv setup the local launcher uses — install the project
# editable so dependency resolution matches CI exactly. Heavy third-party
# wheels (``anthropic``, ``voyageai``, ``neo4j``, ``pdfplumber``,
# ``python-pptx``) are pulled in transitively from ``pyproject.toml``.
COPY pyproject.toml ./
COPY app ./app
COPY scripts ./scripts
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir .


FROM python:3.12-slim AS runtime
WORKDIR /app

# Bring the resolved site-packages + console scripts across, dropping
# the build cache. Keeps the runtime image ~300 MB instead of ~600 MB.
COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin /usr/local/bin
COPY app ./app
COPY scripts ./scripts

# Drop privileges. The persistent SQLite + raw-file tree mounts at
# ``/data`` (see docker-compose.yml); make sure the runtime user owns
# it so writes succeed.
RUN useradd --system --create-home --shell /usr/sbin/nologin kw \
 && mkdir -p /data \
 && chown -R kw:kw /data /app
USER kw

# Default the persistent root to the mounted volume. ``KW_PERSISTENT=true``
# flips the demo entry point to the SQLite + filesystem wiring; the
# operator can override either via compose env.
ENV KW_PERSISTENT=true \
    KW_DATA_DIR=/data \
    KW_KNOWLEDGE_LAYER_ENABLED=true \
    KW_LOG_FORMAT=json \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# Healthcheck that exercises the full app stack, not just the TCP port,
# so a stuck startup (e.g. blocked on a Neo4j connection) marks the
# container as unhealthy and compose can restart it.
HEALTHCHECK --interval=15s --timeout=5s --retries=3 --start-period=20s \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3).status==200 else 1)" \
  || exit 1

# uvicorn entry point — no ``--reload`` in production.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
