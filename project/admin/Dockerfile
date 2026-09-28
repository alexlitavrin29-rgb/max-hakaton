# Dependency environment; application entrypoint will be added later.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN groupadd --system appuser && useradd --system --gid appuser --no-create-home appuser
COPY --chown=appuser:appuser . .
USER appuser
