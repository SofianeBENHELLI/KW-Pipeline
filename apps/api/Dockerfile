# Minimal image for the FastAPI app, used by docker/docker-compose.yml.
# Production deployment is out of scope for ADR-012 Phase 1b; this is
# a developer-convenience image so `docker compose up` works locally.
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
